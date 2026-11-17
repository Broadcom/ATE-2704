# ate2704sddcperms.py - ATE-2704: SDDC Manager file permission workaround
# Version 1.0 - 2026-11-17
#
# Why this exists
# ---------------
# The SDDC Manager precheck in Region B (VCF 5.2.1, sddcmanager-b) fails
# SDDC_MANAGER_FILE_PERMISSIONS_CHECK on a healthy lab:
#   /opt/vmware/vcf/lcm/lcm-app/conf/application.properties       r-- (needs rw-)
#   /opt/vmware/vcf/lcm/lcm-app/conf/application-prod.properties  r-- (needs rw-)
#   /opt/vmware/vcf/lcm/lcm-app/conf/BomSpecification.yml         r-- (needs rw-)
#   /opt/vmware/vcf/commonsvcs/bin/commonsvcs                     r-x (needs rw-)
#   /home/vcf/.ssh/known_hosts_backup                             owned by root (needs vcf)
# (details in /var/log/vmware/vcf/lcm/sddcmanager-fs-check.log).
# At least application.properties and known_hosts_backup are rewritten by
# SDDC Manager at boot, so a fix in the template does not stay; this runs at
# every lab start instead.
#
# What it does
# ------------
# Logs in to SDDC Manager as vcf (root SSH is not allowed), switches to root
# with su, and for each file above that exists:
#   - adds the owner write bit if it is missing (group/other are not changed),
#   - gives known_hosts_backup to vcf:vcf if root owns it.
# Files that are already correct are left alone. Only the owner's own access
# changes, which is the state the SDDC Manager precheck asks for.
# It never raises: a failure is logged and the lab start carries on.
#
# Called by Startup/VCFfinal.py (task_sddc_perms).
# Command line (on the Manager, as holuser):
#   python3 ate2704sddcperms.py --dry-run    # show what would change
#   python3 ate2704sddcperms.py              # fix now

import sys

HOSTS = ['sddcmanager-b.site-b.vcf.lab']
CONF = '/opt/vmware/vcf/lcm/lcm-app/conf'
OWNER_WRITE = [f'{CONF}/application.properties', f'{CONF}/application-prod.properties',
               f'{CONF}/BomSpecification.yml', '/opt/vmware/vcf/commonsvcs/bin/commonsvcs']
VCF_OWNED = ['/home/vcf/.ssh/known_hosts_backup']
MARK = 'ATE2704-PERMS'
# The shell joins "ATE2704-PE""RMS" into MARK, so the echoed command line never
# contains MARK itself and pexpect only matches the real output.
MARK_SH = '"ATE2704-PE""RMS"'


def _script(dry_run):
    """Shell run as root on SDDC Manager. Prints one MARK line per file it looks at."""
    act = 'echo' if dry_run else ''
    lines = []
    for f in OWNER_WRITE:
        lines.append(f'if [ -e {f} ]; then if [ -w {f} ] && [ "$(stat -c %A {f} | cut -c3)" = w ]; '
                     f'then echo {MARK_SH}" ok {f}"; else {act} chmod u+w {f} && echo {MARK_SH}" fixed {f} (owner write)"; fi; fi')
    for f in VCF_OWNED:
        lines.append(f'if [ -e {f} ]; then if [ "$(stat -c %U {f})" = vcf ]; then echo {MARK_SH}" ok {f}"; '
                     f'else {act} chown vcf:vcf {f} && echo {MARK_SH}" fixed {f} (owner vcf)"; fi; fi')
    lines.append(f'echo {MARK_SH}" done"')
    return '; '.join(lines)


def fix_host(host, password, log=print, dry_run=False, timeout=90):
    """Returns the list of result lines from host, or raises on login/su failure."""
    import pexpect
    child = pexpect.spawn('ssh', ['-o', 'StrictHostKeyChecking=no', '-o', 'UserKnownHostsFile=/dev/null',
                                  '-o', 'LogLevel=ERROR', '-o', 'PubkeyAuthentication=no', f'vcf@{host}'],
                          encoding='utf-8', timeout=timeout)
    try:
        child.expect(r'(?i)password:')
        child.sendline(password)
        child.expect([r'\$ ', r'> ', r'# '])
        child.sendline('su -')
        child.expect(r'(?i)password:')
        child.sendline(password)
        child.expect(r'# ')
        child.sendline(_script(dry_run))
        child.expect(f'{MARK} done')
        out = [l.strip() for l in child.before.splitlines() if l.strip().startswith(MARK)]
        child.sendline('exit')
        child.sendline('exit')
        return [l[len(MARK) + 1:] for l in out]
    finally:
        child.close(force=True)


def run(lsf=None, dry_run=False):
    """Startup entry point. Never raises; always returns True so the lab carries on."""
    log = lsf.write_output if lsf else print
    if lsf:
        password = lsf.get_password()
    else:
        sys.path.insert(0, '/home/holuser/hol')
        import lsfunctions
        password = lsfunctions.get_password()
    for host in HOSTS:
        try:
            for line in fix_host(host, password, log, dry_run):
                if dry_run and line.startswith('fixed'):
                    line = 'would fix' + line[len('fixed'):]
                log(f'ATE-2704 SDDC Manager permissions ({host}): {line}')
        except Exception as e:
            log(f'ATE-2704 SDDC Manager permissions ({host}): skipped ({type(e).__name__}: {str(e)[:150]}) - '
                f'the precheck may report SDDC_MANAGER_FILE_PERMISSIONS_CHECK')
    return True


if __name__ == '__main__':
    run(dry_run='--dry-run' in sys.argv)
