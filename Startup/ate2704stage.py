# ate2704stage.py - ATE-2704 upgrade-stage detection and config overlay
# Version 1.6 - 2026-11-19
#   1.6: The recovery template also drops the VCF Installer (vcfinstaller-a,
#        10.1.10.250) from the checks, so it can stay powered off there.
#   1.5: Region B recovery template. A lab whose Manager has the marker file
#        /home/holuser/ate2704-no-region-a has no Region A vCenter or hosts
#        (a copy of a lab at the end of Lab 3, for students who move on to
#        Labs 4 and 5). strip_region_a() removes vc-mgmt-a, esx-01a..04a,
#        vsan-mgmt-01a, cluster-mgmt-01a, user-vm-a/b and their pings, ports
#        and URLs from the startup and shutdown settings; detect() then only
#        connects to the Region B hosts, so the Region B stages still apply.
#        Startup/ESXi.py strips them before the core ESXi.py checks the hosts;
#        apply_overlay() strips them too (Shutdown/Shutdown.py, standalone runs).
#        Shared services with site-a names (offline depot, VCF Installer,
#        router, console) are kept (see 1.6 for the VCF Installer). Labs without the marker are unchanged.
#   1.4: Region A after the VVF 9.1.1 upgrade: the VCF Installer deploys VCF
#        Operations as ops-01a and the license server as license-a (the vSphere
#        9.1.1 path uses ops-a and license-server). region_a_9 now detects and
#        starts either set. vcf9_urls entries can be conditional
#        ('?<vm>|<value>': kept only if that VM exists), so the startup waits
#        only for the VCF Operations UI of the VM that is there.
#   1.3: Region A after the VCF Installer (VVF 9.1.1) and after the converge to
#        VCF 9.1.1, plus the optional NSX overlay / VPC chapter:
#        region_a_vsp  starts the vsp-01a runtime (KB 440862 order) and stops it
#                      gracefully (vsp_prep_ips 10.1.1.131); adds opsproxy-01a
#        region_a_vcf  starts/stops nsx-mgmt-01a and sddcmanager-a, adds checks
#        region_a_vpc  drops the 10.1.11.x ping/SSH checks once the test VMs
#                      are on VPC subnets (DHCP addresses)
#        The runtime nodes of both regions now share one power-on block
#        (all control planes, one Pause:300, all workers), and vspvms lists both.
#   1.2: fleet_component() reads the fleet-lcm 'components' list (1.1 expected
#        'elements' and failed with "'str' object has no attribute 'get'").
#   1.1: VCF Automation 9.1 is started and stopped only through the Region B
#        Fleet lifecycle service (fleet_* helpers below), not at VM level: a
#        VM-level power-on is undone by an unfinished Fleet shutdown workflow
#        when the management services runtime comes back (seen on the first
#        cold-start test: VM powered off by svc-sddclcm 4 minutes after Ready).
#
# Why this exists
# ---------------
# config.ini describes the lab as it is delivered: Region A vSphere 8.0 U3,
# Region B VCF 5.2.1 with Aria Suite Lifecycle / Workspace ONE Access / Aria
# Automation 8.18.1, and a vSphere 8.0 U3 workload cluster (wld01-b). During
# the class, attendees upgrade each part to 9.1.1. If a lab is shut down and
# started again after an upgrade, the baseline config.ini is wrong: it does
# not start the new VCF 9 VMs, and it would start the old Aria Automation VM
# (whose IP address now belongs to VCF Automation 9.1.1).
#
# This module finds out which upgrades a lab has had, from the VMs that exist
# and the ESX versions, and adjusts the startup/shutdown settings to match.
# A lab that has not been upgraded gets no changes at all.
#
# Used by:
#   Startup/ESXi.py     Region B recovery template only: remove Region A from
#                       lsf.config and /tmp/config.ini before the host checks
#   Startup/VCF.py      detect over direct ESXi connections, apply the overlay
#                       to lsf.config and /tmp/config.ini, then run the core VCF.py
#   Startup/VCFfinal.py read the saved stage (or detect again) and report
#                       the workload domain and Supervisor state
#   Shutdown/Shutdown.py detect, write the overlay to /tmp/config.ini, then run
#                       the core shutdown
#
# Stages (independent of each other):
#   region_a_9     Region A upgraded to vSphere/VVF/VCF 9.1.1 (VMs ops-a / license-server,
#                  or ops-01a / license-a after the VVF upgrade)
#   region_a_vsp   Region A has the VCF services runtime (vsp-01a-* exist; VCF Installer path)
#   region_a_vcf   Region A converged to VCF 9.1.1 (sddcmanager-a exists)
#   region_a_vpc   test VMs moved to VPC subnets (user-vm-a/b not on user-network-a)
#   region_b_9     Region B management domain on VCF 9.1.1 (ops-01b / vsp-01b-* exist)
#   automation_9   VCF Automation 9.1.1 deployed (auto-int-b-* exists)
#   wld_9          wld01-b hosts on ESX 9.x (version of esx-05b..08b)
#
# Broadcom references used for the VCF 9.1 parts:
#   KB 444635  power VCF Automation off/on through VCF Operations (Fleet lifecycle)
#   KB 440874  graceful shutdown of a VCF services runtime (vcf_services_runtime_shutdown.sh)
#   KB 440862  power-on after that: control plane node(s) first, wait 5 min, then
#              the workers; the runtime recovers by itself in 15-20 min
#
# Command line (on the Manager, as holuser):
#   python3 ate2704stage.py            # detect and print the stage and the changes
#   python3 ate2704stage.py --json     # stage as JSON

import json
import os
import re
import ssl
import sys

STATE_FILE = '/tmp/ate2704-stage.json'
BASE_COPY = '/tmp/config.ini.base'

VC_A = 'vc-mgmt-a.site-a.vcf.lab'
VC_B = 'vc-mgmt-b.site-b.vcf.lab'

# Detection rules: a stage is active when any listed VM name (regex, full match) exists.
STAGE_VMS = {
    'region_a_9': ['ops-a', 'license-server', 'ops-01a', 'license-a'],
    'region_a_vsp': [r'vsp-01a-.*'],
    'region_a_vcf': ['sddcmanager-a'],
    'region_b_9': ['ops-01b', r'vsp-01b-.*'],
    'automation_9': [r'auto-int-b-.*'],
}
WLD_HOSTS = ['esx-05b.site-b.vcf.lab', 'esx-06b.site-b.vcf.lab', 'esx-07b.site-b.vcf.lab', 'esx-08b.site-b.vcf.lab']
MGMT_B_HOSTS = ['esx-01b.site-b.vcf.lab', 'esx-02b.site-b.vcf.lab', 'esx-03b.site-b.vcf.lab', 'esx-04b.site-b.vcf.lab']
TEST_VMS_A = ['user-vm-a', 'user-vm-b']
TEST_NET_A = 'user-network-a'
REGION_A_HOSTS = ['esx-01a.site-a.vcf.lab', 'esx-02a.site-a.vcf.lab', 'esx-03a.site-a.vcf.lab', 'esx-04a.site-a.vcf.lab']

# Old appliances and the host names used for them in TCPServices / URLS.
OLD_ARIA = {'opslcm-b': 'opslcm-b.site-b.vcf.lab', 'vidm-01b': 'vidm-01b.site-b.vcf.lab'}

# Setting changes per stage. 'add' appends values not already present,
# 'remove' drops values (exact match on the part before ':' for VM entries,
# substring match for TCPServices / URLS / Pings), 'set' replaces the value.
OVERLAY = {
    'region_a_9': [
        # vSphere 9.1.1 / converge path: ops-a, license-server. VVF path: ops-01a,
        # license-a. VM list entries for VMs that do not exist are dropped below.
        ('VCF', 'vcfpostedgevms', 'add', ['license-server', 'license-a']),
        ('RESOURCES', 'VMs', 'add', [f'ops-a:{VC_A}', f'ops-01a:{VC_A}', f'opsproxy-01a:{VC_A}']),
        ('VCFFINAL', 'vcf9_urls', 'add', ['?ops-a|https://ops-a.site-a.vcf.lab/ui/',
                                           '?ops-01a|https://ops-01a.site-a.vcf.lab/ui/']),
        ('SHUTDOWN', 'vcf_ops_vms', 'add', ['ops-a', 'ops-01a']),
        ('SHUTDOWN', 'vcf_ops_collector_vms', 'add', ['opsproxy-01a']),
    ],
    # Region A converged to VCF 9.1.1: its own NSX Manager (started by the core
    # VCF.py with the others) and SDDC Manager. No Edges: the distributed
    # transit gateway of the optional overlay chapter runs in the hosts.
    'region_a_vcf': [
        ('VCF', 'vcfnsxmgr', 'add', ['nsx-mgmt-01a']),
        ('RESOURCES', 'VMs', 'add', [f'sddcmanager-a:{VC_A}']),
        ('RESOURCES', 'TCPServices', 'add', ['sddcmanager-a.site-a.vcf.lab:443', 'nsx-mgmt-a.site-a.vcf.lab:443',
                                             'nsx-mgmt-01a.site-a.vcf.lab:443']),
        ('RESOURCES', 'URLS', 'add', ['https://sddcmanager-a.site-a.vcf.lab/ui/', 'https://nsx-mgmt-a.site-a.vcf.lab/']),
        ('SHUTDOWN', 'sddc_manager_vms', 'add', ['sddcmanager-a']),
    ],
    # Region A VCF services runtime (deployed by the VCF Installer with VVF 9.1.1).
    'region_a_vsp': [
        ('VCF', 'vspvms', 'add', [f'vsp-01a-.*:{VC_A}']),
        ('RESOURCES', 'VMs', 'add', [f'@vsp-01a-|{VC_A}']),
        ('SHUTDOWN', 'vsp_prep_ips', 'add', ['10.1.1.131']),
    ],
    # Optional NSX overlay chapter: the test VMs get DHCP addresses on VPC
    # subnets (one only reachable through an external IP), so the baseline
    # 10.1.11.x checks would never pass.
    'region_a_vpc': [
        ('RESOURCES', 'Pings', 'remove', ['10.1.11.130', '10.1.11.131']),
        ('RESOURCES', 'TCPServices', 'remove', ['user-vm-a.site-a.vcf.lab:22', 'user-vm-b.site-a.vcf.lab:22']),
    ],
    'region_b_9': [
        ('VCF', 'vcfpostedgevms', 'add', ['license-b']),
        ('VCF', 'vspvms', 'add', [f'vsp-01b-.*:{VC_B}']),
        # VCF services runtime: control plane node first, 5 minutes, then the workers
        # (KB 440862). '@vsp-01b-' is expanded by apply_overlay() from the VMs found.
        ('RESOURCES', 'VMs', 'add', [f'@vsp-01b-|{VC_B}', f'ops-01b:{VC_B}', f'opsproxy-01b:{VC_B}']),
        ('VCFFINAL', 'vcf9_urls', 'add', ['https://ops-01b.site-b.vcf.lab/ui/',
                                           'https://vidb-b.site-b.vcf.lab/acs/t/CUSTOMER/.well-known/openid-configuration,issuer']),
        ('SHUTDOWN', 'vcf_ops_vms', 'add', ['ops-01b']),
        ('SHUTDOWN', 'vcf_ops_collector_vms', 'add', ['opsproxy-01b']),
        # VCF services runtime (management services, identity broker): graceful
        # stop by the Shutdown.py wrapper before the core shutdown.
        ('SHUTDOWN', 'vsp_prep_ips', 'add', ['10.2.1.131']),
    ],
    'automation_9': [
        # Never start the old Aria Automation 8.18.1 VM: its IP is now VCF Automation's.
        # VCF Automation 9.1 itself is started through Fleet by VCFfinal.py (KB 444635),
        # once the management services runtime is back - not here.
        ('VCF', 'vcfpostedgevms', 'remove', ['auto-01b']),
        ('RESOURCES', 'Pings', 'remove', ['10.2.12.151']),
        ('VCFFINAL', 'vravms', 'set', [f'auto-int-b-.*:{VC_B}']),
        ('VCFFINAL', 'vra_start_hosts', 'set', []),
        ('VCFFINAL', 'vraurls', 'set', ['https://auto-01b.site-b.vcf.lab/provider,VCF Automation',
                                         'https://auto-01b.site-b.vcf.lab/api/versions,SupportedVersions']),
        ('SHUTDOWN', 'vra_prep_hosts', 'set', []),
        # Shutdown wrapper: power VCF Automation off through Region B's Fleet
        # lifecycle service (= VCF Operations > Lifecycle > VCF Automation >
        # Power off, KB 444635). Fallback if that fails: its runtime's
        # graceful shutdown script (vcfa_runtime_ip, KB 440874).
        ('SHUTDOWN', 'vcfa_fleet_lcm_fqdn', 'set', ['fleet-01b.site-b.vcf.lab']),
        ('SHUTDOWN', 'vcfa_vsp_node', 'set', ['vsp-01b.site-b.vcf.lab']),
        ('SHUTDOWN', 'vcfa_runtime_ip', 'set', ['10.2.12.154']),
    ],
}

# VM lists that are filtered against the VMs that exist (entries for missing VMs are dropped).
VM_LISTS = [('VCF', 'vcfpostedgevms'), ('RESOURCES', 'VMs'), ('VCFFINAL', 'vravms'),
            ('SHUTDOWN', 'vcf_identity_broker_vms'), ('SHUTDOWN', 'vcf_ops_fleet_vms'),
            ('SHUTDOWN', 'workload_vms'), ('SHUTDOWN', 'vcf_ops_vms'), ('SHUTDOWN', 'vcf_ops_collector_vms'),
            ('VCFFINAL', 'xregion_vms')]


# Region B recovery template (see 1.5): Region A vCenter and hosts removed.
REGION_A_MARKER = '/home/holuser/ate2704-no-region-a'
# Names that only exist in Region A's nested environment (whole names, so that
# offlinedepot.site-a and router.site-a are not matched). vcfinstaller-a is
# included too (1.6): Labs 4 and 5 do not use it, so it may be powered off.
REGION_A_NAMES = re.compile(r'(?<![\w-])(esx-0[1-4]a|vc-mgmt-a|user-vm-[ab]|vsan-mgmt-01a|cluster-mgmt-01a|vcfinstaller-a)(?![\w-])')
REGION_A_IPS = {'10.1.1.10', '10.1.11.130', '10.1.11.131',   # vc-mgmt-a, user-vm-a, user-vm-b
                '10.1.10.250'}                              # vcfinstaller-a (1.6)
REGION_A_SECTIONS = ('RESOURCES', 'VCF', 'VCFFINAL', 'SHUTDOWN')


def region_a_removed():
    """True on the Region B recovery template (marker file on the Manager)."""
    return os.path.isfile(os.environ.get('ATE2704_NO_REGION_A_MARKER', REGION_A_MARKER))


def _region_a_entry(entry):
    return bool(REGION_A_NAMES.search(entry)) or entry.split(':')[0].strip() in REGION_A_IPS


def strip_region_a(cfg, log=print):
    """Remove the Region A vCenter, hosts, datastore, cluster, test VMs and their checks
    from cfg. Returns a list of change descriptions (empty if there was nothing to remove)."""
    changes = []
    for sec in REGION_A_SECTIONS:
        if not cfg.has_section(sec):
            continue
        for opt in cfg.options(sec):
            cur = _lines(cfg, sec, opt)
            new = [c for c in cur if not _region_a_entry(c)]
            if new != cur:
                _store(cfg, sec, opt, new)
                changes.append(f'[{sec}] {opt} (no Region A): removed {", ".join(c for c in cur if c not in new)}')
    return changes


def _lines(cfg, sec, opt):
    if not cfg.has_option(sec, opt):
        return []
    return [l.strip() for l in cfg.get(sec, opt).split('\n') if l.strip() and not l.strip().startswith(('#', ';'))]


def _store(cfg, sec, opt, values):
    if not cfg.has_section(sec):
        cfg.add_section(sec)
    cfg.set(sec, opt, '\n'.join(values) if values else '')


def _vm_name(entry):
    return entry.split(':')[0].strip()


def _matches(pattern, names):
    try:
        rx = re.compile(pattern + r'\Z')
    except re.error:
        return [n for n in names if n == pattern]
    return [n for n in names if rx.match(n)]


def _esx_inventory(hosts, user, password, log):
    """Connect to each ESXi host directly. Returns ({vm_name: {power, mem_mb}}, {host: version}, errors)."""
    from pyVim.connect import SmartConnect, Disconnect
    from pyVmomi import vim
    ctx = ssl._create_unverified_context()
    vms, versions, errors = {}, {}, []
    for h in hosts:
        try:
            si = SmartConnect(host=h, user=user, pwd=password, sslContext=ctx, connectionPoolTimeout=20)
        except Exception as e:
            errors.append(f'{h}: {e}')
            continue
        try:
            versions[h] = si.content.about.version
            view = si.content.viewManager.CreateContainerView(si.content.rootFolder, [vim.VirtualMachine], True)
            for vm in view.view:
                try:
                    st = vm.runtime.powerState
                    # A VM can be registered on several hosts; keep the powered-on registration.
                    if vms.get(vm.name, {}).get('power') != 'poweredOn':
                        hw = vm.config.hardware if vm.config else None
                        vms[vm.name] = {'power': st, 'mem_mb': hw.memoryMB if hw else 0,
                                        'networks': [n.name for n in vm.network]}
                except Exception:
                    pass
        finally:
            try:
                Disconnect(si)
            except Exception:
                pass
    return vms, versions, errors


def detect(lsf, hosts=None):
    """Detect the lab stage over direct ESXi connections. Returns a dict (see save())."""
    if hosts is None:
        hosts = [l.split(':')[0].strip() for l in lsf.get_config_list('VCF', 'vcfmgmtcluster')]
    no_region_a = region_a_removed()
    if no_region_a:
        hosts = [h for h in hosts if not _region_a_entry(h)]
    vms, versions, errors = _esx_inventory(hosts, 'root', lsf.get_password(), lsf.write_output)
    names = sorted(vms)
    stage = {'ok': bool(vms) and not errors, 'errors': errors, 'hosts_checked': len(hosts),
             'region_a_removed': no_region_a,
             'esx_versions': versions, 'stages': {}, 'evidence': {}, 'old_aria_present': []}
    for st, pats in STAGE_VMS.items():
        found = sorted({n for p in pats for n in _matches(p, names)})
        stage['stages'][st] = bool(found)
        stage['evidence'][st] = found
    wld = [versions.get(h, '') for h in WLD_HOSTS if h in versions]
    stage['stages']['wld_9'] = bool(wld) and all(v.split('.')[0].isdigit() and int(v.split('.')[0]) >= 9 for v in wld)
    stage['evidence']['wld_9'] = wld
    nets = {n: vms[n].get('networks', []) for n in TEST_VMS_A if n in vms}
    stage['stages']['region_a_vpc'] = bool(nets) and any(TEST_NET_A not in v for v in nets.values())
    stage['evidence']['region_a_vpc'] = nets
    stage['old_aria_present'] = [n for n in ('opslcm-b', 'vidm-01b', 'auto-01b') if n in vms]
    stage['vm_names'] = names
    stage['vm_mem_mb'] = {n: v.get('mem_mb', 0) for n, v in vms.items()}
    return stage


def summary(stage):
    s = stage.get('stages', {})
    ra = ('VCF 9.1 (converged)' if s.get('region_a_vcf') else 'VVF 9.1' + (' + services runtime' if s.get('region_a_vsp') else '')
          ) if s.get('region_a_9') else '8.0 U3 (not upgraded)'
    if stage.get('region_a_removed'):
        ra, s = 'removed (Region B recovery template)', dict(s, region_a_vpc=False)
    parts = [f"Region A {ra}" + (', test VMs on VPC subnets' if s.get('region_a_vpc') else ''),
             f"Region B mgmt {'VCF 9.1' if s.get('region_b_9') else 'VCF 5.2.1 (not upgraded)'}",
             f"Automation {'VCF Automation 9.1' if s.get('automation_9') else 'Aria Automation 8.18.1'}",
             f"wld01-b ESX {'9.x' if s.get('wld_9') else '8.0 U3'}"]
    if stage.get('old_aria_present'):
        parts.append('old Aria VMs present: ' + ', '.join(stage['old_aria_present']))
    return '; '.join(parts)


def _expand(values, stage):
    """Expand '@<prefix>|<vcenter>' entries into VCF services runtime nodes in KB 440862
    power-on order. All runtimes share one block, at the place of the first '@' entry:
    every control plane node (the smallest node of each runtime by memory), one
    Pause:300, then every worker."""
    mem = stage.get('vm_mem_mb', {})
    cps, workers, out, at = [], [], [], None
    names = set(stage.get('vm_names', []))
    for v in values:
        if v.startswith('?'):
            # '?<vm>|<value>': keep <value> only if VM <vm> exists.
            vm, _, val = v[1:].partition('|')
            if vm in names:
                out.append(val)
            continue
        if not v.startswith('@'):
            out.append(v)
            continue
        prefix, _, vc = v[1:].partition('|')
        vc = vc or VC_B
        if at is None:
            at = len(out)
        nodes = sorted(n for n in stage.get('vm_names', []) if n.startswith(prefix))
        if not nodes:
            continue
        small = min(mem.get(n, 0) for n in nodes)
        cps += [f'{n}:{vc}' for n in nodes if mem.get(n, 0) == small]
        workers += [f'{n}:{vc}' for n in nodes if mem.get(n, 0) != small]
    if at is None:
        return out
    return out[:at] + cps + (['Pause:300'] if workers else []) + workers + out[at:]


def apply_overlay(cfg, stage, log=print):
    """Change cfg (a ConfigParser) for the detected stage. Returns a list of change descriptions.
    Does nothing if detection did not succeed, except remove Region A on the
    Region B recovery template."""
    changes = []
    if region_a_removed():
        changes += strip_region_a(cfg, log)
    if not stage.get('ok'):
        log('Stage detection incomplete - keeping the baseline config.ini unchanged'
            + (' (apart from removing Region A)' if changes else ''))
        return changes
    names = stage.get('vm_names', [])
    # Combine the 'add' values of all active stages per setting first, so that the
    # runtime nodes of both regions end up in one power-on block (see _expand).
    ops = []
    for st in OVERLAY:
        if not stage['stages'].get(st):
            continue
        for sec, opt, op, vals in OVERLAY[st]:
            prev = next((o for o in ops if op == 'add' and o[2] == 'add' and o[:2] == [sec, opt]), None)
            if prev:
                prev[3] = prev[3] + [v for v in vals if v not in prev[3]]
                prev[4] += ',' + st
            else:
                ops.append([sec, opt, op, list(vals), st])
    for sec, opt, op, vals, st in ops:
        vals = _expand(vals, stage)
        cur = _lines(cfg, sec, opt)
        if op == 'set':
            new = list(vals)
        elif op == 'add':
            new = cur + [v for v in vals if v not in cur]
        else:  # remove
            new = [c for c in cur if not any(c == v or _vm_name(c) == v or v in c for v in vals)]
        if new != cur or (op == 'set' and not cfg.has_option(sec, opt)):
            _store(cfg, sec, opt, new)
            changes.append(f'[{sec}] {opt} ({st}): {", ".join(new) if new else "(empty)"}')
    # Drop entries for VMs that do not exist (e.g. deleted vIDM / Suite Lifecycle / old auto-01b).
    for sec, opt in VM_LISTS:
        cur = _lines(cfg, sec, opt)
        new = [c for c in cur if _vm_name(c).lower() == 'pause' or _matches(_vm_name(c), names)]
        if new != cur:
            _store(cfg, sec, opt, new)
            gone = [c for c in cur if c not in new]
            changes.append(f'[{sec}] {opt}: removed missing VM(s) {", ".join(gone)}')
    # Checks for deleted old Aria appliances.
    missing = [host for vm, host in OLD_ARIA.items() if vm not in names]
    for sec, opt in (('RESOURCES', 'TCPServices'), ('RESOURCES', 'URLS'), ('VCFFINAL', 'xregion_urls')):
        cur = _lines(cfg, sec, opt)
        new = [c for c in cur if not any(h in c for h in missing)]
        if new != cur:
            _store(cfg, sec, opt, new)
            changes.append(f'[{sec}] {opt}: removed checks for deleted {", ".join(missing)}')
    return changes


# ---------------------------------------------------------------------------
# Region B Fleet lifecycle service (VCF Operations > Lifecycle > VCF Management
# Components; KB 444635). Used by Shutdown/Shutdown.py and Startup/VCFfinal.py.
# ---------------------------------------------------------------------------
FLEET_FQDN = 'fleet-01b.site-b.vcf.lab'
FLEET_VSP_NODE = 'vsp-01b.site-b.vcf.lab'


def fleet_token(lsf, fleet_fqdn=FLEET_FQDN, vsp_node=FLEET_VSP_NODE):
    """Token for a Fleet lifecycle service, as the core fleet.get_fleet_lcm_jwt() does for
    Region A (that function only knows vsp-01a): read the IAM client from the
    vcf-iam-vcfa-admin secret on the runtime's control plane, then OAuth2 password grant."""
    import base64
    import re
    import socket
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    pw, user = lsf.get_password(), 'vmware-system-user'
    worker = socket.gethostbyname(vsp_node)
    r = lsf.ssh(f"echo '{pw}' | sudo -S grep server: /etc/kubernetes/node-agent.conf", f'{user}@{worker}')
    m = re.search(r'https?://([0-9.]+):', getattr(r, 'stdout', '') or '')
    if not m:
        raise ValueError(f'could not find the control plane of {vsp_node}')
    r = lsf.ssh(f"echo '{pw}' | sudo -S -i kubectl get secret vcf-iam-vcfa-admin -n vcf-fleet-lcm "
                f"-o jsonpath='{{.data}}' 2>/dev/null", f'{user}@{m.group(1)}')
    raw = getattr(r, 'stdout', '') or ''
    data = json.loads(raw[raw.find('{'):raw.rfind('}') + 1])
    cid = base64.b64decode(data['clientId']).decode()
    sec = base64.b64decode(data['clientSecret']).decode()
    t = requests.post(f'https://{fleet_fqdn}/api/v1/identity/token',
                      data={'grant_type': 'password', 'username': 'admin', 'password': pw},
                      headers={'Authorization': 'Basic ' + base64.b64encode(f'{cid}:{sec}'.encode()).decode()},
                      verify=False, timeout=60)
    t.raise_for_status()
    return t.json()['access_token']


def _fleet(method, path, token, fleet_fqdn=FLEET_FQDN, **kw):
    import requests
    return requests.request(method, f'https://{fleet_fqdn}{path}', headers={'Authorization': 'Bearer ' + token},
                            verify=False, timeout=60, **kw)


def fleet_component(token, ctype='VCFA', fleet_fqdn=FLEET_FQDN):
    """The component dict (id, fqdn, ...) of the given type, or None."""
    r = _fleet('GET', '/fleet-lcm/v1/components', token, fleet_fqdn)
    r.raise_for_status()
    body = r.json()
    # Same shapes as the core fleet.get_components_fleet_lcm(): a list, or a dict with
    # 'components' (fleet-lcm) / 'content' / 'elements'.
    if isinstance(body, dict):
        body = body.get('components') or body.get('content') or body.get('elements') or []
    for c in body if isinstance(body, list) else []:
        if isinstance(c, dict) and (c.get('componentType') or '').upper() == ctype:
            return c
    return None


def fleet_last_power_workflow(token, fqdn, fleet_fqdn=FLEET_FQDN):
    """Newest SHUTDOWN/START component workflow for fqdn: (name, status, id) or None."""
    r = _fleet('GET', '/fleet-lcm/v1/tasks', token, fleet_fqdn, params={'limit': 50})
    r.raise_for_status()
    hits = [t for t in r.json().get('elements', [])
            if t.get('name') in ('SHUTDOWN_COMPONENT_WORKFLOW', 'START_COMPONENT_WORKFLOW')
            and ((t.get('description') or {}).get('args') or {}).get('componentFqdn') == fqdn]
    if not hits:
        return None
    t = max(hits, key=lambda x: x.get('createTime') or '')
    return t.get('name'), t.get('status'), t.get('id')


def fleet_action(token, component_id, action, fleet_fqdn=FLEET_FQDN):
    """POST ?action=shutdown|start. Returns the workflow task id."""
    r = _fleet('POST', f'/fleet-lcm/v1/components/{component_id}', token, fleet_fqdn, params={'action': action})
    if r.status_code not in (200, 202):
        raise RuntimeError(f'{action} returned HTTP {r.status_code}: {r.text[:200]}')
    return r.json().get('id')


def fleet_wait(token_fn, task_id, timeout_s, log, sleep_fn, fleet_fqdn=FLEET_FQDN):
    """Wait until a Fleet workflow ends. token_fn() returns a (fresh) token. Returns the final status."""
    import time
    deadline, status, token = time.time() + timeout_s, 'UNKNOWN', token_fn()
    while time.time() < deadline:
        try:
            r = _fleet('GET', f'/fleet-lcm/v1/tasks/{task_id}', token, fleet_fqdn)
            if r.status_code == 401:
                token = token_fn()
                continue
            status = r.json().get('status', 'UNKNOWN')
        except Exception as e:
            status = f'unreachable ({e.__class__.__name__})'
        if status in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            return status
        log(f'  Fleet workflow {task_id[:8]}: {status}')
        sleep_fn(60)
    return status


def save(stage, changes):
    try:
        with open(STATE_FILE, 'w') as f:
            json.dump(dict(stage, changes=changes), f, indent=1)
    except OSError:
        pass


def load():
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def write_config(cfg, path):
    """Write cfg to path, keeping a copy of the original file the first time."""
    import shutil
    if os.path.isfile(path) and not os.path.isfile(BASE_COPY):
        shutil.copy2(path, BASE_COPY)
    with open(path, 'w') as f:
        f.write('# ATE-2704: written by Startup/ate2704stage.py for the detected upgrade stage.\n'
                f'# Original: {BASE_COPY}\n\n')
        cfg.write(f)


if __name__ == '__main__':
    sys.path.insert(0, '/home/holuser/hol')
    import lsfunctions as lsf
    lsf.init(router=False)
    import configparser
    st = detect(lsf)
    # Start from the original settings, not an already-adjusted /tmp/config.ini.
    cfg = configparser.ConfigParser()
    cfg.read(BASE_COPY if os.path.isfile(BASE_COPY) else lsf.configini)
    ch = apply_overlay(cfg, st, print)
    if '--json' in sys.argv:
        print(json.dumps(dict(st, changes=ch), indent=1))
    else:
        print('Stage: ' + summary(st))
        print('ESX versions: ' + ', '.join(f'{h.split(".")[0]}={v}' for h, v in sorted(st['esx_versions'].items())))
        if st['errors']:
            print('Errors: ' + '; '.join(st['errors']))
        print('Changes to the startup/shutdown settings:' if ch else 'No changes (baseline config applies).')
        for c in ch:
            print('  ' + c)
