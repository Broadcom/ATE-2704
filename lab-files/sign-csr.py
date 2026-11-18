#!/usr/bin/env python3
"""sign-csr.py - have the lab CA (vcf.lab Root Authority) sign certificate requests.

In production you send the CSRs that VCF Operations generates to your company's
certificate authority and import the signed certificates it returns. In this lab
the certificate authority is the HashiCorp Vault PKI on the holorouter, the CA
that already signed the vcf.lab certificates Firefox trusts. This script plays
the part of "send the CSR to the CA team".

Usage (console terminal):
  python3 ~/"Lab Files"/sign-csr.py ~/Downloads/<file>.csr [more.csr ...]
  python3 ~/"Lab Files"/sign-csr.py ~/Downloads/<csrs>.zip      # a zip or tar of CSRs

For each request it writes, next to the CSR:
  <name>.crt            the signed server certificate
  <name>-chain.pem      server certificate + the vcf.lab CA chain (import this)
and prints the name, alternative names, issuer and expiry date.

It reads the Vault token from ~/creds.txt the same way ~/hol/Tools/hol-ssl.py
does, and never prints it.
"""
import os
import re
import subprocess
import sys
import tarfile
import tempfile
import zipfile

import requests
import urllib3

urllib3.disable_warnings()

VAULT = 'https://vault.vcf.lab'
MOUNT = 'pki'
ROLE = 'holodeck'
TTL = '397d'                      # Firefox accepts at most 398 days
TOKEN_FILE = os.path.expanduser('~/creds.txt')
PEM_CSR = re.compile(r'-----BEGIN (NEW )?CERTIFICATE REQUEST-----.+?-----END (NEW )?CERTIFICATE REQUEST-----', re.S)


def token():
    try:
        with open(TOKEN_FILE) as f:
            return f.read().strip()
    except OSError as e:
        sys.exit(f'Cannot read the lab CA token from {TOKEN_FILE}: {e}')


def csr_files(paths, tmp):
    """Expand zip/tar archives and return (path, pem) for every CSR found."""
    out = []
    for p in paths:
        p = os.path.expanduser(p)
        if zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                z.extractall(tmp)
            out += csr_files([os.path.join(r, f) for r, _, fs in os.walk(tmp) for f in fs], tempfile.mkdtemp())
            continue
        if tarfile.is_tarfile(p):
            with tarfile.open(p) as t:
                t.extractall(tmp, filter='data')
            out += csr_files([os.path.join(r, f) for r, _, fs in os.walk(tmp) for f in fs], tempfile.mkdtemp())
            continue
        try:
            text = open(p, errors='ignore').read()
        except OSError as e:
            print(f'Skipping {p}: {e}')
            continue
        m = PEM_CSR.search(text)
        if m:
            out.append((p, m.group(0)))
        elif paths and p in [os.path.expanduser(x) for x in paths]:
            print(f'Skipping {p}: no certificate request in it')
    return out


def sign(session, csr_pem):
    # Keep the names exactly as VCF Operations put them in the request.
    body = {'csr': csr_pem, 'ttl': TTL, 'format': 'pem', 'use_csr_common_name': True, 'use_csr_sans': True}
    r = session.post(f'{VAULT}/v1/{MOUNT}/sign/{ROLE}', json=body, timeout=60)
    if r.status_code != 200 and 'ttl' in r.text.lower():
        body.pop('ttl')               # fall back to the role's own maximum
        r = session.post(f'{VAULT}/v1/{MOUNT}/sign/{ROLE}', json=body, timeout=60)
    if r.status_code != 200:
        raise RuntimeError(f'the lab CA refused the request ({r.status_code}): {r.text[:300]}')
    return r.json()['data']


def describe(cert_pem):
    try:
        r = subprocess.run(['openssl', 'x509', '-noout', '-subject', '-issuer', '-enddate',
                            '-ext', 'subjectAltName'], input=cert_pem, capture_output=True,
                           text=True, timeout=15)
        lines = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        get = lambda k: next((l.split('=', 1)[1].strip() for l in lines if l.startswith(k)), '-')
        sans = next((lines[i + 1] for i, l in enumerate(lines)
                     if l.startswith('X509v3 Subject Alternative Name') and i + 1 < len(lines)), '-')
        return (f'  Name:    {get("subject")}\n'
                f'  SANs:    {sans}\n'
                f'  Issuer:  {get("issuer")}\n'
                f'  Expires: {get("notAfter")}')
    except Exception:
        return ''


def main():
    if len(sys.argv) < 2 or sys.argv[1] in ('-h', '--help'):
        print(__doc__)
        sys.exit(0 if len(sys.argv) > 1 else 1)
    s = requests.Session()
    s.verify = False
    s.headers['X-Vault-Token'] = token()
    with tempfile.TemporaryDirectory() as tmp:
        found = csr_files(sys.argv[1:], tmp)
        if not found:
            sys.exit('No certificate requests found.')
        outdir_default = os.path.dirname(os.path.abspath(os.path.expanduser(sys.argv[1])))
        failed = 0
        for path, pem in found:
            base = os.path.splitext(os.path.basename(path))[0]
            outdir = os.path.dirname(path) if not path.startswith(tmp) else outdir_default
            try:
                d = sign(s, pem)
            except Exception as e:
                print(f'{base}: FAILED - {e}')
                failed += 1
                continue
            chain = [d['certificate']] + [c for c in d.get('ca_chain') or [d['issuing_ca']] if c]
            crt = os.path.join(outdir, base + '.crt')
            full = os.path.join(outdir, base + '-chain.pem')
            with open(crt, 'w') as f:
                f.write(d['certificate'].strip() + '\n')
            with open(full, 'w') as f:
                f.write('\n'.join(c.strip() for c in chain) + '\n')
            print(f'{base}: signed by the lab CA')
            print(describe(d['certificate']))
            print(f'  Files:   {crt}\n           {full}')
        sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
