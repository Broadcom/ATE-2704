#!/usr/bin/env python3
# Shutdown.py - ATE-2704 lab-specific shutdown wrapper
# Version 1.3 - 2026-11-19
#   1.3: Shows each pre-shutdown step in the console Lab Status
#        (/lmchol/hol/startup_status.txt). Until now the panel kept showing
#        "Ready" for the 20-40 minutes before the core shutdown's Phase 1.
#        With the Region B recovery template marker (Startup/ate2704stage.py
#        1.5) the stage step also removes Region A from the shutdown settings.
#   1.2: Waits for Fleet's "Shutdown component VCF Automation" workflow to
#        finish (up to 45 min) before the management services runtime is
#        stopped. In 1.1 the core helper stopped tracking it after ~2 min; the
#        runtime was then stopped mid-workflow, and the workflow finished when
#        the runtime came back, powering VCF Automation off after the next
#        startup. Fleet helpers moved to Startup/ate2704stage.py.
#   1.1: Upgrade-stage aware. Before anything else it detects which 9.1.1
#        upgrades the lab has had (Startup/ate2704stage.py) and writes the
#        matching settings to /tmp/config.ini (built from the original config,
#        /tmp/config.ini.base or the repo config.ini), so the core shutdown
#        also stops the new VCF 9 VMs (VCF Operations, the VCF services runtime,
#        VCF Automation 9.1) and no longer looks for deleted Aria VMs. After the
#        VCF Automation upgrade the deploy.sh step is skipped, and before the
#        core shutdown: VCF Automation 9.1 is powered off the documented way,
#        through the Region B Fleet lifecycle service (= VCF Operations >
#        Lifecycle > VCF Automation > Power off, KB 444635; fallback: its
#        runtime's shutdown script), then the management services runtime is
#        stopped gracefully with Broadcom's vcf_services_runtime_shutdown.sh
#        --skip-poweroff (KB 440874).
#   1.0: Aria Automation 8.x services stopped with deploy.sh --shutdown first.
#
# Why this wrapper exists
# -----------------------
# The core VCFshutdown.py shuts Aria Automation 8.x (auto-01b) down in
# Phase 1b with a VMware Tools guest shutdown, then a hard power off after
# 5 minutes. Its Phase 1 (Fleet / Suite Lifecycle API) and the Phase 1b
# Kubernetes clean-up only know the VCF 9.x Region A addresses, so neither
# applies to this Site B 8.18 appliance.
#
# Broadcom's supported shutdown for Aria Automation 8.x is to stop its
# services first with "/opt/scripts/deploy.sh --shutdown", then shut the
# guest OS down. This wrapper does the first part, then runs the core
# Shutdown.py with the same options, so Phase 1b only has to shut down an
# appliance whose services have already stopped.
#
# Usage (on the Manager, as holuser) - accepts every core Shutdown.py option:
#   python3 <this file> --dry-run      # preview: checks SSH and deploy.sh only
#   python3 <this file>                # full shutdown
#   python3 <this file> --phase 1b     # Aria step, then core Phase 1b only
#   python3 <this file> --skip-aria-prep ...   # core shutdown only
#
# The Aria step runs for a full shutdown, and for --phase/--phases runs that
# include Phase 1 or 1b. It is configured in config.ini [SHUTDOWN]:
#   vra_prep_hosts    appliance FQDNs to run deploy.sh --shutdown on
#                     (empty or missing: step skipped)
#   vra_prep_timeout  seconds to wait for each appliance (default 2700)
#
# A failed or timed-out Aria step is logged and the core shutdown still runs:
# Phase 1b then shuts the VM down as before.
#
# Note: the core Shutdown.py does not look for Shutdown overrides itself, so
# this wrapper must be run directly, not "python3 Shutdown/Shutdown.py" in ~/hol.
# Keep it after the upgrades: it also applies the upgrade-stage settings (1.1).

import argparse
import datetime
import os
import subprocess
import sys

HOLROOT = '/home/holuser/hol'
CORE_SHUTDOWN = os.path.join(HOLROOT, 'Shutdown', 'Shutdown.py')
LOG_FILE = os.path.join(HOLROOT, 'shutdown-aria.log')
DEFAULT_TIMEOUT = 2700

sys.path.insert(0, HOLROOT)
import lsfunctions as lsf  # noqa: E402


def log(msg=''):
    line = f'[{datetime.datetime.now():%Y-%m-%d %H:%M:%S}] {msg}'
    print(line, flush=True)
    try:
        with open(LOG_FILE, 'a') as f:
            f.write(line + '\n')
    except OSError:
        pass


def wanted(phase, phases):
    """Run the Aria step for a full shutdown or when Phase 1/1b is selected."""
    if phase is None and phases is None:
        return True
    selected = [phase] if phase else [p.strip() for p in phases.split(',')]
    return any(p.lower() in ('1', '1b') for p in selected)


def ssh(host, command, password, timeout):
    """Run a command as root on host; stream its output into the log."""
    env = dict(os.environ, SSHPASS=password)
    cmd = ['sshpass', '-e', 'ssh', '-o', 'StrictHostKeyChecking=no',
           '-o', 'UserKnownHostsFile=/dev/null', '-o', 'LogLevel=ERROR',
           '-o', 'PubkeyAuthentication=no', '-o', 'ConnectTimeout=15',
           '-o', 'ServerAliveInterval=30', f'root@{host}', command]
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True)
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line:
                log(f'    {line}')
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        return None


STAGE = {}


def stage_prep():
    """Detect the upgrade stage and write the matching settings to /tmp/config.ini for the core shutdown."""
    import configparser
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location('ate2704stage', os.path.join(here, '..', 'Startup', 'ate2704stage.py'))
    stg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(stg)
    log('=' * 70)
    log('ATE-2704 PRE-SHUTDOWN: upgrade stage')
    log('=' * 70)
    stage = stg.detect(lsf)
    STAGE.update(stage)
    log('Stage: ' + stg.summary(stage))
    if stage.get('errors'):
        log('Detection errors: ' + '; '.join(stage['errors'])[:500])
    # Always start from the original settings: an upgrade may have happened since the lab started.
    base = stg.BASE_COPY if os.path.isfile(stg.BASE_COPY) else lsf.configini
    cfg = configparser.ConfigParser()
    cfg.read(base)
    changes = stg.apply_overlay(cfg, stage, log)
    for c in changes:
        log('  setting: ' + c)
    if changes:
        stg.write_config(cfg, lsf.configini)
        log(f'Wrote the adjusted settings to {lsf.configini} (original: {stg.BASE_COPY})')
    elif base == stg.BASE_COPY:
        import shutil
        shutil.copy2(base, lsf.configini)
        log(f'No changes for this stage - restored the original settings to {lsf.configini}')
    else:
        log('No changes for this stage - baseline settings apply')
    lsf.config = cfg


VSP_SCRIPT = os.path.join(HOLROOT, 'Tools', 'vcf_services_runtime_shutdown.sh')


def _cfg(key):
    if lsf.config.has_option('SHUTDOWN', key):
        return [v.strip() for v in lsf.config.get('SHUTDOWN', key).split('\n') if v.strip()]
    return []


def _stg():
    import importlib.util
    here = os.path.dirname(os.path.abspath(__file__))
    spec = importlib.util.spec_from_file_location('ate2704stage', os.path.join(here, '..', 'Startup', 'ate2704stage.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _runtime_script(ip, dry_run):
    """Broadcom vcf_services_runtime_shutdown.sh --skip-poweroff for one runtime (KB 440874)."""
    if not os.path.isfile(VSP_SCRIPT):
        log(f'WARNING - {VSP_SCRIPT} not found; the VMs will only get a guest shutdown')
        return False
    if not lsf.test_tcp_port(ip, 5480, timeout=5):
        log(f'{ip}: management API (5480) not reachable - runtime already down, skipping')
        return True
    env = dict(os.environ, VMSP_PASSWORD=lsf.get_password())
    env.pop('VCENTER_USERNAME', None)
    env.pop('VCENTER_PASSWORD', None)
    cmd = [VSP_SCRIPT, '--node-ip', ip, '--skip-poweroff', '--skip-snapshot-check'] + (['--dry-run'] if dry_run else [])
    log(f'{ip}: running {" ".join(cmd)}')
    start = datetime.datetime.now()
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if line and 'password' not in line.lower():
                log(f'    {line}')
        rc = proc.wait(timeout=1800)
    except subprocess.TimeoutExpired:
        proc.kill()
        rc = None
    took = int((datetime.datetime.now() - start).total_seconds())
    log(f'{ip}: ' + ('done' if rc == 0 else f'WARNING - exit {rc}') + f' after {took}s')
    return rc == 0


def vcfa_prep(dry_run):
    """Power VCF Automation 9.1 off the documented way before the core shutdown.

    KB 444635: VCF Operations > Build > Lifecycle > VCF Management Components >
    VCF Automation > Power off = Fleet lifecycle action=shutdown on fleet-01b.
    Waits until Fleet reports the workflow SUCCEEDED (up to 45 min): the
    management services runtime that runs the workflow is stopped next, and an
    unfinished workflow would complete - and power VCF Automation off again -
    after the next startup. If it fails, VCF Automation's runtime is stopped
    with Broadcom's runtime script instead (KB 440874). Either way the core
    Phase 1b then shuts down any VCF Automation VM still running.
    """
    fqdn, node, ip = _cfg('vcfa_fleet_lcm_fqdn'), _cfg('vcfa_vsp_node'), _cfg('vcfa_runtime_ip')
    log('=' * 70)
    log('ATE-2704 PRE-SHUTDOWN: VCF Automation 9.1 power off (Fleet lifecycle, KB 444635)')
    log('=' * 70)
    if not fqdn:
        log('VCF Automation 9.1 not deployed - skipping')
        return
    stg = _stg()
    fqdn, node = fqdn[0], (node[0] if node else stg.FLEET_VSP_NODE)
    ok = False
    try:
        token = stg.fleet_token(lsf, fqdn, node)
        comp = stg.fleet_component(token, 'VCFA', fqdn)
        if not comp:
            log(f'{fqdn}: no VCFA component found - nothing to power off')
            return
        cid = comp.get('componentUuid') or comp.get('id')
        if dry_run:
            log(f'{fqdn}: token OK, VCFA component {comp.get("fqdn")} found - would power it off '
                f'(action=shutdown) and wait for the workflow to finish')
            return
        task = stg.fleet_action(token, cid, 'shutdown', fqdn)
        log(f'{fqdn}: "Shutdown component VCF Automation" started (task {task}); waiting for it to finish')
        status = stg.fleet_wait(lambda: stg.fleet_token(lsf, fqdn, node), task, 2700, log,
                                lambda s: __import__('time').sleep(s), fqdn)
        ok = status == 'SUCCEEDED'
        log(f'VCF Automation power-off through Fleet: {status}')
    except Exception as e:
        log(f'WARNING - Fleet lifecycle power-off failed: {e}')
    if not ok and ip:
        log('Falling back to the VCF services runtime shutdown script for VCF Automation')
        _runtime_script(ip[0], dry_run)


def vsp_prep(dry_run):
    """Gracefully stop the VCF management services runtime ([SHUTDOWN] vsp_prep_ips, KB 440874).

    Runs Broadcom's vcf_services_runtime_shutdown.sh with --skip-poweroff: the
    system shutdown API scales down all workloads and sets the power-off marker
    the runtime uses to recover by itself on the next boot. The core Phase 4b
    then shuts the VMs down. The core Phase 2b would run the same script, but
    with power-off through govc, which the Manager does not have. Runs after
    vcfa_prep(), because the Fleet lifecycle service lives on this runtime.
    """
    ips = _cfg('vsp_prep_ips')
    log('=' * 70)
    log('ATE-2704 PRE-SHUTDOWN: VCF management services runtime (KB 440874, --skip-poweroff)')
    log('=' * 70)
    if not ips:
        log('No VCF services runtime in this lab yet - skipping')
        return
    for ip in ips:
        _runtime_script(ip, dry_run)


def aria_prep(dry_run):
    if STAGE.get('stages', {}).get('automation_9'):
        log('VCF Automation 9.1 deployed - no Aria Automation 8 services to stop (deploy.sh step skipped)')
        return
    hosts = []
    if lsf.config.has_option('SHUTDOWN', 'vra_prep_hosts'):
        hosts = [h.strip() for h in lsf.config.get('SHUTDOWN', 'vra_prep_hosts').split('\n')
                 if h.strip() and not h.strip().startswith('#')]
    timeout = DEFAULT_TIMEOUT
    if lsf.config.has_option('SHUTDOWN', 'vra_prep_timeout'):
        timeout = int(lsf.config.get('SHUTDOWN', 'vra_prep_timeout'))

    log('=' * 70)
    log('ATE-2704 PRE-SHUTDOWN: Aria Automation services (deploy.sh --shutdown)')
    log('=' * 70)
    if not hosts:
        log('No [SHUTDOWN] vra_prep_hosts configured - skipping')
        return

    password = lsf.get_password()
    for host in hosts:
        if not lsf.test_tcp_port(host, 22, timeout=5):
            log(f'{host}: SSH not reachable (appliance probably powered off) - skipping')
            continue
        if dry_run:
            rc = ssh(host, 'test -x /opt/scripts/deploy.sh && echo deploy.sh present; '
                           'vracli version 2>/dev/null | head -1', password, 60)
            if rc == 0:
                log(f'{host}: would run /opt/scripts/deploy.sh --shutdown (timeout {timeout}s)')
            else:
                log(f'{host}: WARNING - check failed (exit {rc}); the real run would skip it')
            continue
        log(f'{host}: running /opt/scripts/deploy.sh --shutdown (timeout {timeout}s)')
        start = datetime.datetime.now()
        rc = ssh(host, '/opt/scripts/deploy.sh --shutdown', password, timeout)
        took = int((datetime.datetime.now() - start).total_seconds())
        if rc == 0:
            log(f'{host}: services stopped in {took}s')
        elif rc is None:
            log(f'{host}: WARNING - timed out after {took}s; Phase 1b will shut the VM down')
        else:
            log(f'{host}: WARNING - deploy.sh exited {rc} after {took}s; Phase 1b will shut the VM down')


STATUS_FILE = '/lmchol/hol/startup_status.txt'   # console Lab Status (same file the core shutdown writes)


def status(text, dry_run=False):
    """Show the pre-shutdown step in the console Lab Status. Never raises; skipped on dry runs."""
    if dry_run:
        return
    try:
        with open(STATUS_FILE, 'w') as f:
            f.write(text)
    except OSError as e:
        log(f'(could not update the Lab Status: {e})')


def main():
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument('--dry-run', '-n', action='store_true')
    parser.add_argument('--phase', '-p')
    parser.add_argument('--phases')
    parser.add_argument('--skip-aria-prep', action='store_true')
    parser.add_argument('--help', '-h', action='store_true')
    parser.add_argument('--version', '-v', action='store_true')
    args, _ = parser.parse_known_args()
    core_args = [a for a in sys.argv[1:] if a != '--skip-aria-prep']

    if not (args.help or args.version or args.skip_aria_prep):
        lsf.init(router=False)
        status('Shutdown: checking the upgrade stage', args.dry_run)
        try:
            stage_prep()
        except Exception as e:  # never block the core shutdown
            log(f'WARNING - stage detection failed ({e}); the core shutdown uses the current settings')
        if wanted(args.phase, args.phases):
            for step, text in ((vcfa_prep, 'Shutdown: powering off VCF Automation'),
                               (vsp_prep, 'Shutdown: stopping VCF services runtimes')):
                status(text, args.dry_run)
                try:
                    step(args.dry_run)
                except Exception as e:  # never block the core shutdown
                    log(f'WARNING - {step.__name__} failed: {e}')
            status('Shutdown: stopping Aria Automation services', args.dry_run)
            try:
                aria_prep(args.dry_run)
            except Exception as e:  # never block the core shutdown
                log(f'WARNING - Aria pre-shutdown step failed: {e}')
        log('Handing over to the core shutdown: ' + ' '.join([CORE_SHUTDOWN] + core_args))
        log()

    os.chdir(HOLROOT)
    os.execv(sys.executable, [sys.executable, CORE_SHUTDOWN] + core_args)


if __name__ == '__main__':
    main()
