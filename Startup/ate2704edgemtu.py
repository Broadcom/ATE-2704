# ate2704edgemtu.py - ATE-2704: match the wld01-b Edge uplink profile MTU to the hosts
# Version 1.0 - 2026-11-18
#
# Why this exists
# ---------------
# In the wld01-b workload domain the hosts' overlay runs on the distributed
# switch vds01-wld01-01b at MTU 8900 (TEP vmknics vmk10/vmk11 8900), but the
# Edges' uplink profile "uplinkprofile-edge-wld01-b" on nsx-wld01-b is 1700.
# Hosts and Edges share the overlay transport zone tz-wld01-b, so NSX raises
# the HIGH alarm "MTU configuration mismatch between Transport Nodes attached
# to the same Transport Zone" (open since 2026-10-29: it is in the template).
# Tunnels stay up (VM traffic at 1500 fits), but the alarm is real, and the
# Region B management domain uses 8900 for its Edges (set up by VCF).
#
# What it does
# ------------
# For each target (NSX Manager, uplink profile name, MTU) in TARGETS or
# [VCFFINAL] edge_mtu_profiles ("nsx_fqdn:profile_name:mtu", one per line):
#   - reads the uplink profile from the NSX Manager API
#   - already at that MTU  -> nothing to do
#   - otherwise            -> PUTs the profile back with the new MTU (same body,
#                             including _revision) and reads it back to confirm
# The Edges apply the new MTU to their TEP interfaces while running; expect at
# most a short blip in overlay traffic. A profile that does not exist (renamed
# or replaced after the wld01-b import/upgrade) is reported and skipped.
# It never raises: an NSX Manager that does not answer is retried a few times,
# then skipped, and the lab start carries on.
#
# Called by Startup/VCFfinal.py (task_edge_mtu, Task 0d).
# Command line (on the Manager, as holuser):
#   python3 ate2704edgemtu.py --dry-run    # show what would change
#   python3 ate2704edgemtu.py              # fix now

import base64
import json
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

TARGETS = [('nsx-wld01-b.site-b.vcf.lab', 'uplinkprofile-edge-wld01-b', 8900)]
LAB_PASSWORD = 'VMware123!VMware123!'
ATTEMPTS = 3          # NSX Manager not answering yet: try this many times...
WAIT_SECONDS = 60     # ...this far apart


def _req(nsx, password, method, path, body=None):
    auth = base64.b64encode(f'admin:{password}'.encode()).decode()
    req = urllib.request.Request(f'https://{nsx}{path}', method=method,
                                 data=json.dumps(body).encode() if body is not None else None,
                                 headers={'Authorization': f'Basic {auth}', 'Accept': 'application/json',
                                          'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, context=ssl._create_unverified_context(), timeout=30) as r:
        return json.loads(r.read() or b'null')


def find_profile(nsx, password, name):
    """The uplink profile called name (full body), or None."""
    q = urllib.parse.urlencode({'include_system_owned': 'true', 'hostswitch_profile_type': 'UplinkHostSwitchProfile'})
    cursor = None
    while True:
        r = _req(nsx, password, 'GET', f'/api/v1/host-switch-profiles?{q}' + (f'&cursor={cursor}' if cursor else ''))
        for p in r.get('results', []):
            if p.get('display_name') == name and p.get('resource_type') == 'UplinkHostSwitchProfile':
                return _req(nsx, password, 'GET', f'/api/v1/host-switch-profiles/{p["id"]}')
        cursor = r.get('cursor')
        if not cursor:
            return None


def fix_profile(nsx, password, name, mtu, dry_run=False):
    """Returns a one-line result. Raises on API errors (the caller retries/skips)."""
    p = find_profile(nsx, password, name)
    if p is None:
        return f'no uplink profile called "{name}" - skipped (renamed or replaced after an upgrade?)'
    cur = p.get('mtu')
    if cur == mtu:
        return f'ok (MTU already {mtu})'
    if dry_run:
        return f'would change MTU {cur if cur is not None else "(not set)"} -> {mtu}'
    p['mtu'] = mtu
    _req(nsx, password, 'PUT', f'/api/v1/host-switch-profiles/{p["id"]}', p)
    after = _req(nsx, password, 'GET', f'/api/v1/host-switch-profiles/{p["id"]}').get('mtu')
    if after != mtu:
        raise RuntimeError(f'NSX accepted the change but the profile reads MTU {after}')
    return f'changed MTU {cur if cur is not None else "(not set)"} -> {mtu}'


def parse_targets(lines):
    out = []
    for ln in lines or []:
        parts = [x.strip() for x in str(ln).split(':')]
        if len(parts) == 3 and parts[2].isdigit():
            out.append((parts[0], parts[1], int(parts[2])))
    return out


def run(lsf=None, targets=None, dry_run=False, password=None, sleep=None):
    """Never raises. Returns True if every reachable profile is at its MTU (or would be)."""
    log = lsf.write_output if lsf else print
    if password is None:
        try:
            password = lsf.get_password() if lsf else LAB_PASSWORD
        except Exception:
            password = LAB_PASSWORD
    if sleep is None:
        sleep = getattr(lsf, 'labstartup_sleep', None) or time.sleep
    ok = True
    for nsx, name, mtu in (targets or TARGETS):
        for attempt in range(1, ATTEMPTS + 1):
            try:
                log(f'Edge uplink MTU: {nsx} {name}: {fix_profile(nsx, password, name, mtu, dry_run)}')
                break
            except urllib.error.HTTPError as e:
                if e.code in (401, 403):
                    ok = False
                    log(f'Edge uplink MTU: {nsx}: login refused (HTTP {e.code}) - skipped')
                    break
                err = f'HTTP {e.code}'
            except Exception as e:
                err = str(e) or e.__class__.__name__
            if attempt < ATTEMPTS:
                log(f'Edge uplink MTU: {nsx} not ready ({err[:100]}) - trying again in {WAIT_SECONDS} s')
                sleep(WAIT_SECONDS)
            else:
                ok = False
                log(f'Edge uplink MTU: {nsx} {name}: skipped ({err[:120]})')
    return ok


if __name__ == '__main__':
    import argparse
    ap = argparse.ArgumentParser(description='Match the wld01-b Edge uplink profile MTU to the hosts (8900)')
    ap.add_argument('--dry-run', action='store_true', help='show what would change')
    ap.add_argument('--password', default=LAB_PASSWORD, help='NSX admin password (default: the lab password)')
    ap.add_argument('targets', nargs='*', help='nsx_fqdn:profile_name:mtu (default: ' +
                    ' '.join(f'{a}:{b}:{c}' for a, b, c in TARGETS) + ')')
    a = ap.parse_args()
    sys.exit(0 if run(targets=parse_targets(a.targets) or None, dry_run=a.dry_run, password=a.password) else 1)
