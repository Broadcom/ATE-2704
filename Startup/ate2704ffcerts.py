# ate2704ffcerts.py - ATE-2704: trust the vCenter VMCA roots in the console Firefox
# Version 1.0 - 2026-11-18
#
# Why this exists
# ---------------
# During the upgrades, appliances get certificates signed by a vCenter's own
# certificate authority (VMCA), not by the vcf.lab Root Authority:
#   - Lab 2: about 90 minutes into the converge the VCF Installer, SDDC Manager,
#     NSX and the VCF services runtime switch to vc-mgmt-a VMCA certificates.
#   - Lab 3: the Region B management components get vc-mgmt-b VMCA certificates.
#   - Lab 4: the imported wld01-b components use vc-wld01-b.
# These sites send HSTS, so Firefox hard-blocks them (no "Accept the risk"
# button) unless it trusts the VMCA root. The template's Firefox profile only
# holds an old "vc-mgmt-a.site-a.vcf.lab CA" (SHA-256 84:C9:FF...), not the
# root vc-mgmt-a uses now, and nothing for vc-mgmt-b or vc-wld01-b. The core
# startup only copies the vcf.lab Root Authority into Firefox.
#
# What it does
# ------------
# For each vCenter in VCENTERS (or [VCFFINAL] firefox_vmca_vcenters):
#   1. Downloads https://<vcenter>/certs/download.zip (no login needed) and
#      picks the VMCA root: self-signed, OU=VMware Engineering, O=<vcenter>.
#   2. For each console Firefox profile (cert9.db under /lmchol), compares the
#      root's SHA-256 fingerprint with every certificate already in the profile:
#        - already there and trusted for websites  -> nothing to do
#        - already there but not trusted           -> sets trust "CT,,"
#        - missing                                 -> adds it as
#                                                     "<short name> VMCA Root", trust "CT,,"
# So a profile that already has the root (fixed by hand, or a re-mastered
# template) is left alone. The first change to a profile keeps a backup,
# cert9.db.bak-ate2704 next to it (only once, never overwritten).
# Firefox reads cert9.db at start-up, so a Firefox window already open needs a
# restart to see a newly added root.
# It never raises: a vCenter that does not answer is skipped and the lab start
# carries on.
#
# Called by Startup/VCFfinal.py (task_firefox_vmca).
# Command line (on the Manager, as holuser):
#   python3 ate2704ffcerts.py --dry-run    # show what would change
#   python3 ate2704ffcerts.py              # fix now

import glob
import hashlib
import io
import os
import re
import shutil
import ssl
import subprocess
import sys
import urllib.request
import zipfile

VCENTERS = ['vc-mgmt-a.site-a.vcf.lab', 'vc-mgmt-b.site-b.vcf.lab', 'vc-wld01-b.site-b.vcf.lab']
PROFILE_GLOBS = ['/lmchol/home/holuser/snap/firefox/common/.mozilla/firefox/*/cert9.db',
                 '/lmchol/home/holuser/.mozilla/firefox/*/cert9.db']
TRUST = 'CT,,'
BACKUP_SUFFIX = '.bak-ate2704'
PEM_RE = re.compile(r'-----BEGIN CERTIFICATE-----.+?-----END CERTIFICATE-----', re.S)


def _run(args, data=None):
    r = subprocess.run(args, input=data, capture_output=True, timeout=60)
    return r.returncode, r.stdout.decode(errors='replace'), r.stderr.decode(errors='replace')


def _pem_info(pem):
    """(sha256 fingerprint hex, subject, issuer) of one PEM certificate."""
    rc, out, err = _run(['openssl', 'x509', '-noout', '-subject', '-issuer', '-nameopt', 'RFC2253'], pem.encode())
    if rc != 0:
        raise ValueError(err.strip() or 'openssl could not read the certificate')
    subj = iss = ''
    for line in out.splitlines():
        if line.startswith('subject='):
            subj = line[len('subject='):].strip()
        elif line.startswith('issuer='):
            iss = line[len('issuer='):].strip()
    der = subprocess.run(['openssl', 'x509', '-outform', 'DER'], input=pem.encode(),
                         capture_output=True, timeout=60).stdout
    return hashlib.sha256(der).hexdigest(), subj, iss


def vmca_root(vcenter, timeout=30):
    """The VMCA root PEM of vcenter from its download.zip, or None."""
    ctx = ssl._create_unverified_context()
    with urllib.request.urlopen(f'https://{vcenter}/certs/download.zip', context=ctx, timeout=timeout) as r:
        data = r.read()
    z = zipfile.ZipFile(io.BytesIO(data))
    for name in z.namelist():
        if not name.startswith('certs/lin/') or not name.endswith('.0'):
            continue
        pem = z.read(name).decode(errors='replace')
        m = PEM_RE.search(pem)
        if not m:
            continue
        fp, subj, iss = _pem_info(m.group(0))
        parts = [p.strip() for p in subj.split(',')]
        if subj == iss and 'OU=VMware Engineering' in parts and f'O={vcenter}' in parts:
            return m.group(0), fp
    return None


def profile_certs(dbdir):
    """{fingerprint: (nickname, trust)} for every certificate in a Firefox profile."""
    rc, out, err = _run(['certutil', '-d', f'sql:{dbdir}', '-L'])
    if rc != 0:
        raise RuntimeError(err.strip() or 'certutil -L failed')
    found = {}
    for line in out.splitlines():
        m = re.match(r'^(.*\S)\s{2,}(\S*,\S*,\S*)\s*$', line)
        if not m or m.group(1).startswith('Certificate Nickname'):
            continue
        nick, trust = m.group(1), m.group(2)
        rc, pems, _ = _run(['certutil', '-d', f'sql:{dbdir}', '-L', '-n', nick, '-a'])
        for pem in PEM_RE.findall(pems):
            try:
                found[_pem_info(pem)[0]] = (nick, trust)
            except ValueError:
                pass
    return found


def _backup(db):
    bak = db + BACKUP_SUFFIX
    if not os.path.exists(bak):
        shutil.copy2(db, bak)


def fix_profile(dbdir, vcenter, pem, fp, log=print, dry_run=False):
    """Make one profile trust one root. Returns 'ok', 'trusted', 'added' or 'would ...'."""
    db = os.path.join(dbdir, 'cert9.db')
    have = profile_certs(dbdir)
    short = vcenter.split('.')[0]
    if fp in have:
        nick, trust = have[fp]
        if 'C' in trust.split(',')[0]:
            return f'ok (already trusted as "{nick}")'
        if dry_run:
            return f'would set trust {TRUST} on "{nick}"'
        _backup(db)
        rc, _, err = _run(['certutil', '-d', f'sql:{dbdir}', '-M', '-n', nick, '-t', TRUST])
        if rc != 0:
            raise RuntimeError(err.strip())
        return f'trusted "{nick}"'
    nicks = {n for n, _ in have.values()}
    nick = f'{short} VMCA Root'
    if nick in nicks:
        nick = f'{short} VMCA Root {fp[:8]}'
    if dry_run:
        return f'would add "{nick}" (SHA-256 {fp[:8]}...)'
    _backup(db)
    rc, _, err = _run(['certutil', '-d', f'sql:{dbdir}', '-A', '-n', nick, '-t', TRUST], pem.encode())
    if rc != 0:
        raise RuntimeError(err.strip())
    if fp not in profile_certs(dbdir):
        raise RuntimeError('certutil reported success but the root is not in the profile')
    return f'added "{nick}" (SHA-256 {fp[:8]}...)'


def run(lsf=None, vcenters=None, dry_run=False):
    """Never raises. Returns True if every reachable vCenter root is trusted (or would be)."""
    log = lsf.write_output if lsf else print
    ok = True
    try:
        profiles = sorted({os.path.dirname(p) for g in PROFILE_GLOBS for p in glob.glob(g)})
        if not profiles:
            log('Firefox VMCA roots: no console Firefox profile found under /lmchol - skipped')
            return True
        for vc in (vcenters or VCENTERS):
            try:
                root = vmca_root(vc)
            except Exception as e:
                log(f'Firefox VMCA roots: {vc} not reachable ({e}) - skipped')
                continue
            if not root:
                log(f'Firefox VMCA roots: no VMCA root found in {vc}/certs/download.zip - skipped')
                continue
            pem, fp = root
            for prof in profiles:
                try:
                    res = fix_profile(prof, vc, pem, fp, log, dry_run)
                    log(f'Firefox VMCA roots: {vc} -> {os.path.basename(prof)}: {res}')
                except Exception as e:
                    ok = False
                    log(f'Firefox VMCA roots: {vc} -> {os.path.basename(prof)}: FAILED ({e})')
    except Exception as e:
        log(f'Firefox VMCA roots: skipped ({e})')
        return False
    return ok


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description='Trust the vCenter VMCA roots in the console Firefox')
    p.add_argument('--dry-run', action='store_true', help='show what would change')
    p.add_argument('vcenters', nargs='*', help=f'vCenters (default: {" ".join(VCENTERS)})')
    a = p.parse_args()
    sys.exit(0 if run(vcenters=a.vcenters or None, dry_run=a.dry_run) else 1)
