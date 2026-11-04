# VCFfinal.py - ATE-2704 lab-specific override of the core VCFfinal module
# Version 1.1 - 2026-11-04 (standalone runs load config.ini, as the core modules do)
#
# Why this override exists
# ------------------------
# ATE-2704 is a pre-upgrade baseline: Region A vSphere 8.0 U3, Site B VCF 5.2.1
# with Aria Suite Lifecycle 8.18 / Workspace ONE Access 3.3.7 / Aria Automation
# 8.18, and a separate vSphere 8.0 U3 workload cluster with a Supervisor.
#
# The core VCFfinal.py runs every task as soon as a [VCFFINAL] section exists,
# including VCF 9.x-only work that must not touch this baseline:
#   - Task 2/2c  WCP tuning with vcf-lab-tuner.py
#   - Task 2d/2e VSP platform and VCF component start-up
#   - Task 4b    VCF Automation 9 Kubernetes remediation
#   - Task 7     NSX password-expiry / Fleet policy changes
#   - Task 8     Vault CA distribution through confighol-9.1.py
#
# The shutdown, however, needs [VCFFINAL] tanzucontrol (stop WCP, pause the
# Supervisor) and vravms (graceful Aria Automation shutdown in Phase 1b).
# This override lets config.ini carry those keys while startup only does:
#
#   Task 1  Supervisor (tanzucontrol): make sure WCP is running on the
#           Supervisor vCenter and wait until the Supervisor reports
#           RUNNING / READY. The control plane VMs are started by vCenter
#           (ESX Agent Manager), never powered on here.
#   Task 2  Aria Automation (vravms): power the VMs on if they are still off
#           (normally already started from [VCF] vcfpostedgevms).
#   Task 3  Aria Automation URLs (vraurls): wait for the UI to answer.
#
# Optional [VCFFINAL] keys read by this override:
#   tanzu_wait_minutes   (default 20)    Supervisor readiness wait
#   tanzu_required       (default false) fail the lab if not ready in time
#   vraurl_wait_minutes  (default 35)    Aria Automation URL wait
#   vraurls_required     (default true)  fail the lab if a URL never answers
#
# Remove this file (and revisit config.ini) once the lab is on VCF 9.x and the
# core VCFfinal tasks apply.

import os
import sys
import time

MODULE_NAME = 'VCFfinal'
MODULE_DESCRIPTION = 'ATE-2704 final tasks (Supervisor, Aria Automation 8)'
LOCK_FILE = '/tmp/VCFfinal.lock'
DONE_FILE = '/tmp/VCFfinal.done'

# Core dashboard tasks this override does not run.
NOT_USED = ['wcp_dns', 'svc_dns', 'wcp_certs', 'tanzu_deploy', 'vsp_vms', 'vsp_cp_resize',
            'vcf_components', 'vcfa_k8s_health', 'vcf_component_urls', 'k8s_certs', 'nsx_passwords']


def _bool(lsf, key, default):
    if lsf.config.has_option('VCFFINAL', key):
        return lsf.config.get('VCFFINAL', key).strip().lower() in ('1', 'true', 'yes', 'on')
    return default


def _int(lsf, key, default):
    try:
        return int(lsf.config.get('VCFFINAL', key)) if lsf.config.has_option('VCFFINAL', key) else default
    except ValueError:
        return default


def _dashboard(lsf):
    try:
        sys.path.insert(0, '/home/holuser/hol/Tools')
        from status_dashboard import StatusDashboard, TaskStatus
        return StatusDashboard(lsf.lab_sku), TaskStatus
    except Exception:
        return None, None


def _set(db, ts, task, status, msg=''):
    if not db:
        return
    try:
        db.update_task('vcffinal', task, getattr(ts, status), msg) if msg else db.update_task('vcffinal', task, getattr(ts, status))
        db.generate_html()
    except Exception:
        pass


def _sso_users(lsf):
    users = {}
    for line in lsf.get_config_list('RESOURCES', 'vCenters'):
        parts = line.split(':')
        if len(parts) >= 3:
            users[parts[0].strip()] = parts[2].strip()
    return users


def _vc_api(lsf, vc, user, calls):
    """Open a vCenter REST session, run calls(requests, base_url, headers), close the session.
    Uses the REST API instead of SSH: root on these 8.0 U3 appliances logs in to
    the appliance shell, where vmon-cli is not available."""
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        r = requests.post(f'https://{vc}/api/session', auth=(user, lsf.get_password()), verify=False, timeout=30)
        if r.status_code != 201:
            return None
        h = {'vmware-api-session-id': r.json()}
        try:
            return calls(requests, f'https://{vc}/api', h)
        finally:
            requests.delete(f'https://{vc}/api/session', headers=h, verify=False, timeout=10)
    except Exception as e:
        lsf.write_output(f'  vCenter API error on {vc}: {e}')
        return None


def _supervisor_status(lsf, vc, user):
    """Return a list of (cluster, config_status, kubernetes_status), or None on API error."""
    def calls(rq, base, h):
        c = rq.get(f'{base}/vcenter/namespace-management/clusters', headers=h, verify=False, timeout=30)
        if c.status_code != 200:
            return None
        return [(x.get('cluster_name') or x.get('cluster'), x.get('config_status'), x.get('kubernetes_status')) for x in c.json()]
    return _vc_api(lsf, vc, user, calls)


def _ensure_wcp(lsf, vc, user):
    """Make sure the WCP service is started on vc. Returns the final state or None."""
    def calls(rq, base, h):
        s = rq.get(f'{base}/vcenter/services/wcp', headers=h, verify=False, timeout=30)
        if s.status_code != 200:
            return None
        state = s.json().get('state')
        if state != 'STARTED':
            lsf.write_output(f'  WCP service on {vc} is {state} - starting it')
            rq.post(f'{base}/vcenter/services/wcp?action=start', headers=h, verify=False, timeout=300)
            state = rq.get(f'{base}/vcenter/services/wcp', headers=h, verify=False, timeout=30).json().get('state')
        return state
    return _vc_api(lsf, vc, user, calls)


def task_supervisor(lsf, db, ts, dry_run):
    entries = lsf.get_config_list('VCFFINAL', 'tanzucontrol')
    if not entries:
        lsf.write_output('No tanzucontrol configured - skipping Supervisor checks')
        _set(db, ts, 'wcp_vcenter', 'SKIPPED', 'Not configured')
        _set(db, ts, 'tanzu_control', 'SKIPPED', 'Not configured')
        return True
    vcenters, patterns = [], []
    for e in entries:
        parts = e.split(':')
        patterns.append(parts[0].strip())
        if len(parts) > 1 and parts[1].strip() and parts[1].strip() not in vcenters:
            vcenters.append(parts[1].strip())
    users = _sso_users(lsf)
    wait_min = _int(lsf, 'tanzu_wait_minutes', 20)
    required = _bool(lsf, 'tanzu_required', False)
    if dry_run:
        lsf.write_output(f'Would check WCP and Supervisor status on {vcenters} (wait {wait_min} min, required={required})')
        return True

    lsf.write_vpodprogress('Supervisor', 'GOOD-3')
    _set(db, ts, 'wcp_vcenter', 'RUNNING')
    wcp_ok = True
    for vc in vcenters:
        state = _ensure_wcp(lsf, vc, users.get(vc, 'administrator@vsphere.local'))
        lsf.write_output(f'WCP service on {vc}: {state}')
        wcp_ok = wcp_ok and state == 'STARTED'
    _set(db, ts, 'wcp_vcenter', 'COMPLETE' if wcp_ok else 'FAILED')

    _set(db, ts, 'tanzu_control', 'RUNNING')
    deadline = time.time() + wait_min * 60
    ready, last = False, ''
    while time.time() < deadline:
        states = {vc: _supervisor_status(lsf, vc, users.get(vc, 'administrator@vsphere.local')) for vc in vcenters}
        last = '; '.join(f'{vc}: {s}' for vc, s in states.items())
        ready = all(s and all(c == 'RUNNING' and k == 'READY' for _, c, k in s) for s in states.values())
        if ready:
            break
        lsf.write_output(f'Waiting for Supervisor: {last}')
        lsf.labstartup_sleep(60)

    # Report the control plane VMs (started by vCenter/EAM, not by this module).
    try:
        lsf.connect_vcenters([v for v in lsf.get_config_list('RESOURCES', 'vCenters') if v.split(':')[0] in vcenters])
        for pat in patterns:
            vms = lsf.get_vm_match(pat)
            lsf.write_output(f'Supervisor control plane VMs ({pat}): ' +
                             ', '.join(f'{v.name}={"on" if lsf.is_vm_powered_on(v) else "off"}' for v in vms))
    except Exception as e:
        lsf.write_output(f'Could not list Supervisor control plane VMs: {e}')

    if ready:
        lsf.write_output(f'Supervisor ready: {last}')
        _set(db, ts, 'tanzu_control', 'COMPLETE')
        return True
    msg = f'Supervisor not ready after {wait_min} minutes ({last})'
    lsf.write_output(('FATAL: ' if required else 'WARNING: ') + msg)
    _set(db, ts, 'tanzu_control', 'FAILED', msg)
    if required:
        lsf.labfail(msg)
        return False
    return True


def task_aria_vms(lsf, db, ts, dry_run):
    entries = lsf.get_config_list('VCFFINAL', 'vravms')
    if not entries:
        lsf.write_output('No vravms configured - skipping Aria Automation VMs')
        _set(db, ts, 'vcfa_vms', 'SKIPPED', 'Not configured')
        return True
    if dry_run:
        lsf.write_output(f'Would make sure these Aria Automation VMs are powered on: {entries}')
        return True
    lsf.write_vpodprogress('Aria Automation', 'GOOD-8')
    _set(db, ts, 'vcfa_vms', 'RUNNING')
    wanted = {e.split(':')[1].strip() for e in entries if ':' in e and e.split(':')[1].strip()}
    vc_entries = [v for v in lsf.get_config_list('RESOURCES', 'vCenters') if not wanted or v.split(':')[0] in wanted]
    lsf.connect_vcenters(vc_entries)
    errors = []
    for e in entries:
        pat = e.split(':')[0].strip()
        vms = lsf.get_vm_match(pat)
        if not vms:
            errors.append(f'no VM matches {pat}')
            continue
        for vm in vms:
            if lsf.is_vm_powered_on(vm):
                lsf.write_output(f'{vm.name} already powered on')
            elif not lsf.start_vm(vm):
                errors.append(f'could not power on {vm.name}')
    if errors:
        msg = 'Aria Automation VMs: ' + '; '.join(errors)
        lsf.write_output('FATAL: ' + msg)
        _set(db, ts, 'vcfa_vms', 'FAILED', msg)
        lsf.labfail(msg)
        return False
    _set(db, ts, 'vcfa_vms', 'COMPLETE')
    return True


def task_aria_urls(lsf, db, ts, dry_run):
    specs = lsf.get_config_list('VCFFINAL', 'vraurls')
    if not specs:
        lsf.write_output('No vraurls configured - skipping Aria Automation URL checks')
        _set(db, ts, 'vcfa_urls', 'SKIPPED', 'Not configured')
        return True
    wait_min = _int(lsf, 'vraurl_wait_minutes', 35)
    required = _bool(lsf, 'vraurls_required', True)
    if dry_run:
        lsf.write_output(f'Would check Aria Automation URLs {specs} (wait {wait_min} min, required={required})')
        return True
    lsf.write_vpodprogress('Aria Automation URLs', 'GOOD-8')
    _set(db, ts, 'vcfa_urls', 'RUNNING')
    pending = []
    for s in specs:
        url, _, text = s.partition(',')
        pending.append((url.strip(), text.strip() or None))
    deadline = time.time() + wait_min * 60
    while pending and time.time() < deadline:
        still = []
        for url, text in pending:
            if lsf.test_url(url, expected_text=text, timeout=20):
                lsf.write_output(f'[OK] {url}')
            else:
                still.append((url, text))
        pending = still
        if pending:
            lsf.write_output(f'Waiting for Aria Automation ({len(pending)} URL(s) not answering yet)...')
            lsf.labstartup_sleep(60)
    if not pending:
        _set(db, ts, 'vcfa_urls', 'COMPLETE')
        return True
    msg = 'Aria Automation URL(s) not answering after ' + f'{wait_min} min: ' + ', '.join(u for u, _ in pending)
    lsf.write_output(('FATAL: ' if required else 'WARNING: ') + msg)
    _set(db, ts, 'vcfa_urls', 'FAILED', msg)
    if required:
        lsf.labfail(msg)
        return False
    return True


def main(lsf=None, standalone=False, dry_run=False, force=False):
    if lsf is None:
        sys.path.insert(0, '/home/holuser/hol')
        import lsfunctions as lsf
        if not standalone:
            lsf.init(router=False)
    if not lsf.config.has_section('VCFFINAL'):
        lsf.write_output('No VCFFINAL section in config.ini - skipping VCFfinal')
        return True
    if os.path.isfile(DONE_FILE) and not force:
        lsf.write_output(f'{MODULE_NAME} already completed this boot ({DONE_FILE}); use --force to re-run')
        return True
    if os.path.isfile(LOCK_FILE) and not force:
        lsf.write_output(f'{MODULE_NAME} is already running ({LOCK_FILE}) - skipping')
        return True
    try:
        open(LOCK_FILE, 'w').write(str(os.getpid()))
    except OSError:
        pass
    lsf.write_output(f'Starting {MODULE_NAME} (ATE-2704 override): {MODULE_DESCRIPTION}')
    db, ts = _dashboard(lsf)
    for t in NOT_USED:
        _set(db, ts, t, 'SKIPPED', 'Not used in ATE-2704 (vSphere 8 / VCF 5.2.1 baseline)')
    try:
        ok = (task_supervisor(lsf, db, ts, dry_run)
              and task_aria_vms(lsf, db, ts, dry_run)
              and task_aria_urls(lsf, db, ts, dry_run))
        if ok and not dry_run:
            try:
                open(DONE_FILE, 'w').write(time.strftime('%Y-%m-%dT%H:%M:%S'))
            except OSError:
                pass
        lsf.write_output(f'{MODULE_NAME} (ATE-2704 override) {"completed" if ok else "failed"}')
        return ok
    finally:
        try:
            os.remove(LOCK_FILE)
        except OSError:
            pass


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser(description=MODULE_DESCRIPTION)
    p.add_argument('--standalone', action='store_true', help='run outside labstartup')
    p.add_argument('--dry-run', action='store_true', help='show what would be done')
    p.add_argument('--force', action='store_true', help='run even if already completed this boot')
    p.add_argument('--skip-init', action='store_true', help='skip lsf.init()')
    a = p.parse_args()
    # Same as the core modules: load config.ini (lsf.init) when run directly.
    sys.path.insert(0, '/home/holuser/hol')
    import lsfunctions as _lsf
    if not a.skip_init:
        _lsf.init(router=False)
    sys.exit(0 if main(lsf=_lsf, standalone=a.standalone, dry_run=a.dry_run, force=a.force) else 1)
