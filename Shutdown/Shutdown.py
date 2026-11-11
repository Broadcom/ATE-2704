#!/usr/bin/env python3
# Shutdown.py - ATE-2704 lab-specific shutdown wrapper
# Version 1.0 - 2026-11-04
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
# Remove it once the lab is on VCF 9.x, where the core handles VCF Automation.

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


def aria_prep(dry_run):
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
        if wanted(args.phase, args.phases):
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
