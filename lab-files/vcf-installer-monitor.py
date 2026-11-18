#!/usr/bin/env python3
# vcf-installer-monitor.py - watch the lab's upgrades and deployments, in plain English
# Version 3.0 - 2026-11-18
#
# What it is for
# --------------
# The upgrades in these labs run for minutes to hours. The product UIs show a
# progress list, but when a step sits still you need the logs to know whether
# it is working, retrying or stuck. Those logs are hard to read: thousands of
# lines, and many normal lines are logged as WARNING or even ERROR.
#
# It opens one desktop window (a GTK app, like the other Ubuntu apps). First it
# asks what to watch (it checks what is running and suggests that):
#   Region A converge (VCF Installer)        Lab 2
#   Region A / B: SDDC Manager                upgrades, Deploy VCF Management Components, imports,
#                                             configuration updates (Region B labs 3 and 4)
#   Region A / B: VCF Operations lifecycle    upgrades and installs of the management components
#   vSphere upgrades on vc-mgmt-a, vc-mgmt-b, vc-wld01-b
#                                             vCenter upgrade (reduced downtime) and ESX remediation (vLCM)
# "Change scenario..." switches later.
#
# The window has two panes and a divider you can drag:
#   Left:  the task list from the product's own API: what is happening now, each
#          task with a progress bar and its steps, failed steps and how long they took.
#          Click a task or step to see the log lines from its time window (from both
#          or all of the scenario's logs, read from the log files, so finished steps work too).
#   Right: the scenario's live logs, one tab each, every line explained in plain
#          English and colour coded:
#            Green   Good          the deployment moved on
#            Orange  Warning       can be normal, but matters if it keeps happening
#            Yellow  Benign        logged as ERROR or WARNING, but known to be harmless
#            Red     Error         something failed
#            Blue    Info          a line no pattern knows (shown tidied up)
#            Grey    Routine       housekeeping; hidden unless the filter is Everything
#          Repeated lines are folded into one row with a count. Click a row to see
#          the original line (and any stack trace or JSON, formatted, ready to copy).
#          Some logs (SDDC Manager's lifecycle log, vCenter, the fleet services) are
#          mostly routine: there only the lines a pattern recognises, and failures, are
#          highlighted.
#
# The explanations come from patterns written for this lab; they are not
# product documentation. A line no pattern knows is still shown (Everything),
# with the component name and its message tidied up.
#
# Usage (on the console, in a terminal):
#   python3 ~/"Lab Files"/vcf-installer-monitor.py                       # asks what to watch
#   python3 ~/"Lab Files"/vcf-installer-monitor.py --scenario sddc-b     # straight to one scenario
#   python3 ~/"Lab Files"/vcf-installer-monitor.py --help                # all options
#   python3 vcf-installer-monitor.py --explain domainmanager.log         # translate a saved log
# Close the window (or Ctrl+Q) to stop it. Running it again while it is open
# just brings the window to the front.
#
# Safe to run at any time: it only reads (the product APIs with GET requests, and
# the logs with tail/zcat/kubectl logs over SSH). It changes nothing.
# Logins it uses (lab passwords): admin@local (VCF Installer, SDDC Manager),
# vcf (SDDC Manager and installer logs), administrator@vsphere.local / @wld.sso and
# root (vCenter API and logs), admin@vsp.local (fleet lifecycle API) and
# vmware-system-user with sudo for kubectl logs on the VCF services runtime.
# It needs python3, GTK 3 (python3-gi) and paramiko, all on the lab console.

import argparse
import collections
import datetime
import gzip
import html
import json
import re
import socket
import ssl
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import warnings

LAB_PASSWORD = 'VMware123!VMware123!'
INSTALLER = 'vcfinstaller-a.site-a.vcf.lab'
SDDC_MANAGER = 'sddcmanager-a.site-a.vcf.lab'
SDDC_MANAGER_B = 'sddcmanager-b.site-b.vcf.lab'
DM_DIR = '/var/log/vmware/vcf/domainmanager'
LCM_DIR = '/var/log/vmware/vcf/lcm'
LOG_PATH = DM_DIR + '/domainmanager.log'
CONFIG_FILE = '~/.config/vcf-installer-monitor.json'
KEEP_ENTRIES = 4000
RAW_MAX = 200000  # keep whole lines (JSON specs in the log can be several KB)
FOLD_WINDOW = 6   # a line the same as one of the last 6 entries is folded into it (polling loops interleave)
APP_ID = 'lab.vcf.InstallerMonitor'
SCENARIO_IDS = ['converge-a', 'sddc-a', 'sddc-b', 'fleet-a', 'fleet-b', 'vsphere-mgmt-a', 'vsphere-mgmt-b', 'vsphere-wld01-b']

# How long each milestone took in this lab (.26, 2026-11-17), shown as a guide.
TYPICAL = {
    'Deploy SDDC Manager': '40 min',
    'Convert the existing vCenter to a new VCF instance': '5 min',
    'Deploy and configure NSX': '45 min',
    'Deploy and configure VCF Management Platform': '3 h',
    'Join the existing operations appliance': '40 min',
    'Deploy and configure VCF Management Services': '1 h',
}

# Colours (the lab deck colours, slightly lifted to read well on a dark window)
SEV_COLOUR = {'progress': '#4CC38A', 'watch': '#F0883E', 'benign': '#E8D44D', 'problem': '#FF6B66',
              'info': '#6AAEF0', 'noise': '#8A99B3'}
SEV_LABEL = {'progress': 'Good', 'watch': 'Warning', 'benign': 'Benign', 'problem': 'Error',
             'info': 'Info', 'noise': 'Routine'}
SEV_HELP = {'progress': 'The deployment moved on.',
            'watch': 'Can be normal, but matters if it keeps happening.',
            'benign': 'Logged as ERROR or WARNING, but known to be harmless.',
            'problem': 'Something failed. Read the lines around it.',
            'info': 'A line no pattern knows, shown tidied up.',
            'noise': 'Housekeeping. Only shown when the filter is Everything.'}
SEV_ROW_BG = {'problem': 'rgba(217,83,79,0.16)', 'watch': 'rgba(240,136,62,0.07)'}

# ---------------------------------------------------------------------------
# Plain-English translation
# ---------------------------------------------------------------------------

LINE_RE = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+([A-Z]+)\s+.+?\s+\d+\s+\[((?:[^"\]]|"[^"]*")*)\]\s?(.*)$')
CLASS_RE = re.compile(r'(?:class|logger)="([^"]+)"')
MDC_RE = re.compile(r'^\[([^\]]*)\]\s+-\s+')            # fleet services: [taskId=..., stepName=X, ...] - message
STEP_RE = re.compile(r'^\[step:(?P<st>[A-Z][A-Z0-9_]+)\] (?P<m>.+)$')
SUB_RE = re.compile(r'sub="?([^"\s\]]+)')
# vCenter 8.x / before an upgrade: 2026-11-18T07:16:16.754Z info vpxd[07684] [Originator@6876 sub=AuthorizeManager] message
LINE_RE_VC8 = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?Z)\s+([a-z]+)\s+[\w.-]+\[\d+\]\s+\[((?:[^"\]]|"[^"]*")*)\]\s?(.*)$')
VC8_LEVELS = {'info': 'INFO', 'warning': 'WARNING', 'error': 'ERROR', 'verbose': 'DEBUG', 'trivia': 'TRACE', 'debug': 'DEBUG'}     # vCenter logs: [vc@4413 sub="vpxLro" opID="..."]
# SDDC Manager 5.2 (Region B before its upgrade): 2026-11-17T00:01:00.626+0000 INFO  [vcf_lcm,trace,span] [class,thread] message
LINE_RE52 = re.compile(r'^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d+)?)(Z|[+-]\d\d:?\d\d)\s+([A-Z]+)\s+\[[^\]]*\]\s+\[([^,\]]+)(?:,[^\]]*)?\]\s?(.*)$')


def parse_line(line):
    """(UTC time ending in Z, level, class, message) for a log line, or None for a continuation line."""
    m = LINE_RE.match(line)
    if m:
        ts, level, attrs, msg = m.groups()
        cm = CLASS_RE.search(attrs) or SUB_RE.search(attrs)
        mdc = MDC_RE.match(msg)
        if mdc:
            step = re.search(r'stepName=([A-Z][A-Z0-9_]+)', mdc.group(1))
            msg = (f'[step:{step.group(1)}] ' if step else '') + msg[mdc.end():]
        return ts, level, cm.group(1) if cm else '', msg
    m = LINE_RE_VC8.match(line)
    if m:
        ts, level, attrs, msg = m.groups()
        cm = SUB_RE.search(attrs)
        return ts, VC8_LEVELS.get(level, level.upper()), cm.group(1) if cm else '', msg
    m = LINE_RE52.match(line)
    if m:
        ts, off, level, cls, msg = m.groups()
        if off not in ('Z', '+0000', '+00:00', '-0000'):
            d = datetime.datetime.fromisoformat(ts + off[:3] + ':' + off[-2:]).astimezone(datetime.timezone.utc)
            ts = d.strftime('%Y-%m-%dT%H:%M:%S.%f')[:-3]
        return ts + 'Z', level, cls, msg
    return None
UUID_RE = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')

COMPONENTS = {
    'vcf-sddc-lcm': 'SDDC lifecycle service',
    'vcf-fleet-lcm': 'Fleet lifecycle service',
    'vcf-fleet-depot': 'Fleet depot service',
    'vcf-salt': 'Salt configuration service',
    'vcf-salt-master': 'Salt configuration service',
}

WORDS = [('Nsxt', 'NSX'), ('Nsx', 'NSX'), ('Sddc', 'SDDC'), ('Vcenter', 'vCenter'), ('Vc', 'vCenter'),
         ('Vsp', 'VCF services runtime'), ('Vmca', 'VMCA'), ('Lcm', 'lifecycle'), ('Esxi', 'ESX'),
         ('Esx', 'ESX'), ('Vsan', 'vSAN'), ('Vds', 'distributed switch'), ('Ova', 'OVA'), ('Ovf', 'OVF'),
         ('Api', 'API'), ('Ssh', 'SSH'), ('Ip', 'IP'), ('Dns', 'DNS'), ('Ntp', 'NTP'), ('Hcl', 'HCL'),
         ('Fds', 'depot'), ('Vcf', 'VCF'), ('Ems', 'EMS'), ('Tep', 'TEP'), ('Ceip', 'CEIP')]
SUFFIXES = ['Impl', 'Local']

# Components that write a lot of routine lines. Their unmatched INFO lines count as noise.
CHATTY = {'BaseCache', 'VsphereQueryServiceWithCacheImpl', 'ResourceCacheService', 'CommonsServicesSdkClient',
          'VsphereClusterUtil', 'Dlogger', 'ThreadDetailedMetrics', 'InventoryServiceAdapterImpl',
          'FunctionalityToggleAdapterImpl', 'ResourceFunctionalitiesApiHelper', 'FunctionalityToggleApiFilter',
          'VmodlContextImpl', 'AbstractSession', 'LoggingUtils', 'PasswordUtil', 'VcManagerBase',
          'RecipeComposerImpl', 'TrustAllTrustManager', 'VapiClientFactory$JsonRpcApiClient', 'VsphereClient',
          'ClusterController', 'ImageManagementServiceAdapterImpl', 'NsxtVersionServiceImpl',
          'VcenterServicesConfigDriftCheck', 'VcApplianceClient', 'DomainDiscoveryHostHelper',
          'NsxtClusterServiceImpl', 'ActivityLogger', 'SddcSpecVersionHelper', 'VsanClient'}


def words(cls):
    """'c.v.e.s.s.DeploySddcManagerAction' -> 'Deploy SDDC Manager (step)'."""
    name = cls.split('.')[-1].split('$')[0]
    for s in SUFFIXES:
        if name.endswith(s) and len(name) > len(s):
            name = name[:-len(s)]
    step = name.endswith('Action')
    if step:
        name = name[:-len('Action')]
    parts = re.findall(r'[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+', name)
    fixed = []
    for p in parts:
        for a, b in WORDS:
            if p == a:
                p = b
                break
        fixed.append(p)
    text = ' '.join(fixed) or name
    return f'{text} (step)' if step else text


def friendly_component(t):
    return COMPONENTS.get(t, t)


def _vsp_task(m, msg):
    """VspComponentService 'Task: {json}' - report the stage that is not finished."""
    try:
        d = json.JSONDecoder().raw_decode(msg[msg.index('{'):])[0]
    except Exception:
        return 'Checked the progress of a task on the VCF services runtime.'
    kind = d.get('type', '').split('.')[-1]
    kind = {'InstallComponent': 'Install', 'StageComponent': 'Staging (copying the files)'}.get(kind, kind)
    stages = d.get('stages') or []
    cur = next((s for s in stages if str(s.get('status', '')).upper() not in ('SUCCEEDED', 'COMPLETED', 'SUCCESS')), None)
    if cur:
        return f'{kind} on the VCF services runtime: stage "{cur.get("description") or cur.get("name")}" is {str(cur.get("status", "")).lower()}.'
    return f'{kind} on the VCF services runtime: all {len(stages)} stages finished.'


def _bootstrap(m, msg):
    text = re.sub(r'^\d{4}/\d\d/\d\d \d\d:\d\d:\d\d ', '', msg).strip()
    known = [
        (r'waiting on node agent to get healthy', 'Waiting for the agent on the new runtime node to come up.'),
        (r'node agent is healthy', 'The agent on the new runtime node is up.'),
        (r'waiting on the api to get ready', 'Waiting for the runtime node API to be ready.'),
        (r'node agent api is ready', 'The runtime node API is ready.'),
        (r'setting up connection to agent listening at (\S+)', 'Connecting to the runtime node agent at {0}.'),
        (r'component apply API Task with Id \S+ has status (\w+)', 'Applying a component to the runtime: {0}.'),
        (r'Using appliance name from props: (\S+)', 'The temporary bootstrap VM is called {0}.'),
        (r'Using template folder from props', 'Picked the VM folder for the runtime template.'),
        (r'Executing SSH command for bootstrap log tailing', 'Following the bootstrap log on the new runtime node.'),
        (r'Clone task started', 'Cloning the bootstrap VM from its template.'),
        (r'Configuring \S+ vApp properties', 'Setting the bootstrap VM\'s settings (vApp properties).'),
        (r'Received IP: (\S+)', 'The bootstrap VM got its IP address {0}.'),
        (r'install completed', 'The runtime install step finished.'),
        (r'Bootstrap VM IP removed from exclude list', 'Removed the bootstrap VM from the NSX firewall exclusion list.'),
        (r'^Folder: ', 'Picked the VM folder for the runtime.'),
    ]
    for pat, out in known:
        mm = re.search(pat, text, re.I)
        if mm:
            return out.format(*mm.groups()) + ' (Logged as ERROR, but this is normal bootstrap progress.)'
    if (re.match(r'^[\s\[\]{}(),"\w:.-]*$', text) and len(text) < 60) or re.match(r'^\s*["{}\[\]]', text) or text.rstrip().endswith((',', '{', '[')):
        return 'Part of a settings block printed by the runtime bootstrap (normal).'
    return f'Runtime bootstrap says: "{text[:160]}" (Logged as ERROR, but bootstrap progress is always logged that way.)'


# (pattern on the message, severity, text or function(match, message), optional class name)
# Severity: progress | watch | problem | noise | info
#           hush = routine and known to be harmless even when logged as ERROR (kept out of Highlights)
RULES = [
    # --- the installer waiting on other services --------------------------------------------------
    (r'THIS LOG FILE IS MANAGED BY SDDC MANAGER', 'noise', 'The log file started (new day or service restart).'),
    (r'Polling SDDC LCM task with ID', 'noise', 'Checking on a task it handed to the SDDC lifecycle service.'),
    (r'SDDC LCM Task ID: \S+, Status: (?P<s>\w+)', 'progress', lambda m, s: 'The SDDC lifecycle service task is still running. The detail is in the SDDC Manager log.' if m['s'] == 'RUNNING' else f'The SDDC lifecycle service task is now {m["s"].lower()}.'),
    (r'Polling Fleet LCM task with ID', 'noise', 'Checking on a task it handed to the Fleet lifecycle service.'),
    (r'Fleet LCM Task ID: \S+, Status: (?P<s>\w+)', 'progress', lambda m, s: 'The Fleet lifecycle service task is still running.' if m['s'] == 'RUNNING' else f'The Fleet lifecycle service task is now {m["s"].lower()}.'),
    (r'Attempting to get task with Id: \S+ \((?P<what>Installing|Staging) VSP component of type: (?P<c>\S+) and version: (?P<v>[^)]+)\)',
     'noise', lambda m, s: f'Checking progress: {m["what"].lower()} the {friendly_component(m["c"])} {m["v"]} on the VCF services runtime.'),
    (r'Attempting to get task with Id: \S+ \((?P<d>.+)\)\s*$', 'noise', lambda m, s: f'Checking progress: {tidy(m["d"])}.'),
    (r'^Task: \{"id"', 'progress', _vsp_task, 'VspComponentService'),
    (r'Propagated trusted certificate alias=(?P<a>\S+) to VSP', 'progress', lambda m, s: f'Shared the trusted certificate "{tidy(m["a"])}" with the VCF services runtime.'),
    (r'Successfully set trusted certs configuration', 'progress', 'Updated the list of trusted certificates.'),
    (r'Updating the state of task with ID \S+ to (?P<s>\w+)', 'progress', lambda m, s: f'A task is now {m["s"].replace("_", " ").lower()}.'),
    (r'Creating Transport Zone with name .(?P<n>[^\'"]+)', 'progress', 'Creating the NSX transport zone {n}.'),
    (r'Fleet LCM task created', 'progress', 'Handed a task to the Fleet lifecycle service.'),
    (r'Authentication failed for (?P<h>\S+) in', 'watch',
     'SSH login to {h} failed. This can happen once right after the installer changes that appliance\'s password; it matters if it repeats.'),
    (r'registered SDDC Manager as Relying Party', 'watch',
     'SDDC Manager is not registered with the identity broker yet. Expected until the management services are deployed.'),
    (r'Key \{"host".* is not supported|Trusted key specs not found', 'noise', 'Skipped an SSH host key type it does not use (normal).'),
    (r'The input key \S+ was not passed into the workflow', 'noise', 'An optional workflow setting was not given, so the default is used (normal).'),
    (r'is not a valid ipv6 address', 'noise', 'Checked whether an address is IPv6. It is IPv4, so this is expected.'),
    (r'No functionalities for .* found', 'noise', 'Feature check for an API call with no feature switch (normal).'),
    (r'execution \S+ does not exist', 'noise', 'Looked up a workflow run that has already been cleaned up (normal).'),
    (r'Ignoring failures in the VCF Integration tasks prior to DomainInventory', 'noise', 'Ignoring early results until the domain is in the inventory (normal).'),
    (r'No VLAN Id is defined for portgroup|No VDS config or no LACP group', 'noise', 'Skipped a switch or port group setting that does not apply here (normal).'),
    (r'Renew VAPI session|creating new session to vCenter|known host|clusterInfoMap|Periodic secret refresher|TaskRegistration|Registering the task|Response Body|Using locale',
     'noise', 'Housekeeping (sessions, SSH host keys, task registration).'),
    (r'The VSP API responded successfully after waiting (?P<n>\d+)', 'progress', 'The VCF services runtime API answered (after {n} s).'),
    (r'Failed to fetch system information', 'noise', 'Could not read system information yet (a later retry usually works).'),
    (r'Failed getSystemInfo API call on attempt (?P<a>\d+) of (?P<b>\d+)', 'watch',
     'Could not reach an appliance API yet (attempt {a} of {b}). Normal while an appliance is starting; a problem if it goes on for more than about 30 minutes.'),
    (r'All (?P<n>\d+) retry attempts failed for getSystemInfo', 'watch',
     'All {n} attempts to reach an appliance API failed this round. It will try again; worry only if this keeps repeating.'),

    # --- orchestration ------------------------------------------------------------------------------
    (r'Execution Processing start: (?P<x>\S+)', 'progress', lambda m, s: f'Started: {words(m["x"].split("_")[0])}.'),
    (r'End of Orchestration for Execution ID', 'progress', 'A workflow run finished.'),
    (r'Processing State completed with failure', 'problem', 'A workflow step finished with a failure. Look at the lines just before this one.'),
    (r'Received orchestrator event|Ignoring unknown OrchestratorMessage|No spec can be found for execution|Skipping updating task corresponding',
     'noise', 'Internal bookkeeping between the workflow engine and its listeners.'),
    (r'Excluding recipe|Cannot find excluded components', 'noise', 'Working out which steps apply to this deployment.'),
    (r'SDDC Manager OV', 'progress', 'Deploying the SDDC Manager appliance from its OVA.', 'DeploySddcManagerAction'),
    (r'waitForSddcManagerToBeReady|Waiting for SDDC Manager', 'progress', 'Waiting for the new SDDC Manager to start.'),
    (r'Validat\w+ SDDC Manager API', 'progress', 'Checking that the SDDC Manager API answers.'),
    (r'Initialize SDDC', 'progress', 'Initialising the new SDDC Manager.'),

    # --- checks that look alarming but are expected ------------------------------------------------
    (r'Unable to open any port \[[^\]]*\] for (?P<ip>\S+)', 'noise', 'Checked that {ip} is free: nothing answered, which is what it wants before using that address.'),
    (r'IP address (?P<ip>\S+) is neither reachable or within the predeployed', 'progress', '{ip} is free to use (nothing is using it yet).'),
    (r'Local Command Failed with exit code (?P<n>\d+)', 'noise',
     'A helper command returned exit code {n}. This happens dozens of times in a healthy converge; it only matters if a step fails right after it.'),
    (r'Using auto-generated passwords|hasnt been provided as input|has been provided as input\. A default', 'noise',
     'Filled in a default value because the JSON spec did not set it (logged as ERROR, but harmless).'),
    (r"Could not find VCF Operations '(?P<c>[^']+)' capability", 'noise', "VCF Operations does not offer '{c}' (not used in this lab)."),
    (r'Component (?P<c>\S+) is not part of the release SKU', 'noise', '{c} is not part of VCF 9.1, so it is ignored.'),
    (r'Trusting server', 'noise', 'Accepted another appliance\'s certificate without checking it (normal inside the installer).'),
    (r'entropy source stuck', 'noise', 'The Java random-number generator was slow for a moment (harmless).'),
    (r'loadKeys\(/dev/null', 'noise', 'No SSH key file to load, so it uses the password instead (normal).'),
    (r'requestNewKeysExchange|Initiating key re-exchange', 'noise', 'An SSH session refreshed its encryption keys (normal).'),
    (r'Generated a strong password|password validation of type', 'noise', 'Generated or checked a password for an account it is creating.'),
    (r'Successfully logged in to https://(?P<h>[^:/]+)\S* as (?P<u>\S+)', 'noise', 'Logged in to {h} as {u}.'),
    (r'Closed session for user', 'noise', 'Logged out of a vCenter session.'),
    (r'Slow query execution', 'noise', 'An inventory query took longer than usual (common in a nested lab; harmless on its own).'),
    (r'Query with errors detected|vimResultSet items is null', 'noise', 'An inventory query came back partly empty (usually harmless).'),
    (r'Management domain NSX is included in result for sharing', 'noise', 'Listing which NSX instances can be shared (bookkeeping).'),
    (r'connection timeout is \d+ ms', 'noise', 'Opened an internal API client.'),
    (r'Cache refresh|\[(Vcenter|Cluster)Cache\]|Refreshing resource cache', 'noise', 'Refreshing its cached copy of the vCenter inventory.'),
    (r'metrics', 'noise', 'Internal performance counters.'),
    (r'Validating NSX Version|NSX version validation succeeded', 'noise', 'Checked that the NSX version is one it supports.'),
    (r'Found cluster with source ID \S+ and name (?P<c>\S+)', 'noise', 'Found cluster {c} in vCenter.'),

    # --- SDDC Manager upgrades (lifecycle log) and configuration updates -----------------------------
    (r'pollUpgradeStatus, received (?P<s>\w+) status while polling for upgrade Status of stage (?P<st>\S+)', 'progress',
     lambda m, s: f'SDDC Manager upgrade: stage "{m["st"].replace("_", " ")}" is {m["s"].replace("_", " ").lower()}.'),
    (r'received COMPLETED_WITH_SUCCESS while polling', 'progress', 'An SDDC Manager upgrade stage finished successfully.'),
    (r'Setting environment variables required for SDDC migration app is finished', 'progress', 'Prepared the SDDC Manager upgrade (migration app).'),
    (r'Setting environment variables required for SDDC migration app', 'progress', 'Preparing the SDDC Manager upgrade (migration app).'),
    (r'Created directory \S*thirdparty/upgrades/\S+?/(?P<p>[\w-]+/[\w-]+) for third party', 'progress',
     lambda m, s: f'Started the SDDC Manager upgrade step {m["p"]}.'),
    (r'vLCM updateStepStatus = (?P<s>\w+)', 'progress',
     lambda m, s: 'vSphere Lifecycle Manager finished an upgrade step successfully.' if m['s'].endswith('SUCCESS')
     else f'vSphere Lifecycle Manager reported an upgrade step as {m["s"].replace("_", " ").lower()}.'),
    (r'user action is required to schedule the upgrade after prechecks', 'watch',
     'The prechecks are done. The NSX or ESX upgrade now waits for you: schedule it (Upgrade Now) in SDDC Manager.'),
    (r'Executing pre-check poll', 'noise', 'Checking on the NSX upgrade prechecks.'),
    (r'RDU is applicable for the vCenter|Supported Upgrade Mechanisms: \[ReducedDowntime', 'progress',
     'vCenter will be upgraded with the reduced-downtime upgrade (a new appliance, then a short switchover).'),
    (r'Executing check : (?P<c>\w+)', 'progress', 'Running the {c} check.'),
    (r'Successfully updated system users list on ESXi host (?P<h>\S+)', 'progress', 'Updated the service account on {h}.'),
    (r'Starting parent reconciliation task \S+ to apply (?P<n>\d+) drifts', 'progress', 'Started applying {n} configuration update(s).'),
    (r'Child reconciliation task \S+ status (?P<s>\w+)', 'progress',
     lambda m, s: f'A configuration update is {m["s"].replace("_", " ").lower()}.'),
    (r'Updating the status (?P<s>\w+) for drift', 'progress', lambda m, s: f'A configuration update is now {m["s"].replace("_", " ").lower()}.'),
    (r'Reconciling config for drift id', 'progress', 'Applying a configuration update.'),
    (r'No applicable drifts were found|There are no applicable drifts', 'noise', 'No configuration update was needed for this resource (normal).'),
    (r'Seeded the reconciliations|Drilled down granular resources|In ApplyADriftOnGranularResourcesAction|Lambda cookbook location|'
     r'Recipe to apply a drift|All lambda executions took|config-drift|Fetching configs with param|Getting all applicable drifts|'
     r'Retrieved \d+ applicable drifts|Registering parent reconciliation task|Version update status|Passing orch locale|'
     r'Waiting for \d+ seconds before polling status of child', 'noise', 'Configuration update bookkeeping.'),
    (r'Initiating remediation for', 'progress', 'Refreshing SDDC Manager\'s service account login on vCenter.'),
    (r'(?i)changeset|databasechangelog|change log lock', 'noise', 'Updating its database layout (part of an upgrade).'),
    (r'is already higher than the target SDDC-Manager version', 'noise', 'Checked whether SDDC Manager itself needs an upgrade (it does not).'),
    (r'Started orchestrating upgrade', 'noise', 'Checked on a scheduled or running upgrade (scheduler loop).'),
    (r'In UpgradeController, get upgrade|get all downloaded manifests|In UpgradeActivityController|most recent bundle download|'
     r'mismatchedProductVersion|list of cluster IDs if the upgrade|Upgrade Task ID - |API Gateway base URL|Get SDDC-M Upgrades|'
     r'to fetch SDDC-M Upgrades|get version compliance matrix|get images for productType|Get SupportedUpgradeMechanisms|'
     r'CheckSet GET|UPF is enabled|Updated check set data|Received request to get Component version', 'noise',
     'The SDDC Manager UI or API asked for the upgrade status (routine).'),
    (r'MP upgrade status is (?P<s>\w+)', 'noise', 'Checked the NSX Manager upgrade status: {s}.'),
    (r'Fetching admin credentials for NSX|No credential provided and no remediate', 'noise', 'Looked up credentials (routine).'),
    (r'session based auth|creating new LCM session|vcApplianceEndpoint is', 'noise', 'Opened a session to vCenter.'),
    (r'system-level lock|Deployment or system lock is already acquired|Resource lock for|resource lock', 'noise', 'Checked or took the lock that stops two operations running at once.'),
    (r'NSX Cluster Connection|Upgrade Coordinator Connection|Successfully fetched NSX cluster|ESX VIM connection for', 'noise', 'Opened a connection (routine).'),
    (r'Bundle Info|isEdgeUpgradeAfterHost|VersionMatrix|required bundles order|Upgrade path from|customized target|Async patch bundle|'
     r'custom bundle|bundle order applicability|[Cc]ompatib|inter-op|will be available\. Rest|cross domain bundles|No target product versions set|'
     r'isAvailableOnVumCapability|projected target version|flexible\.bom|is not upgradable to|incompatible upgrade paths|Upgrade path for domain', 'hush',
     'Worked out which upgrade bundles apply and in which order (routine checks; it lists versions that do not apply too).'),
    (r'NSX Audit|Start audit|Audit Completed|from the Active Audits|auditedNsxtCluster', 'noise', 'Routine check of NSX Manager (audit).'),
    (r'domainCapabilities|VUM operations supporting info', 'noise', 'Read what vSphere Lifecycle Manager supports (routine).'),
    (r'[Hh]ardware [Ss]upport inventory|HSM & packages', 'noise', 'Read the hardware support (vendor add-on) inventory.'),
    (r'Fetching component drift details|Getting (?:current|domain|Domain|Nsxt|Vcenter)\b.*from inventory|Are hosts and clusters on mixed version|'
     r'^Esx version:|current version of nsx cluster from the inventory|Current Version is', 'noise', 'Compared the installed versions with the target versions.'),
    (r'Connection Pool Stats|refresh trigger|Enable Refresh Trigger|Refresh cache with update spec|VersionComparator|"\." in given string|'
     r'Could not find build number', 'noise', 'Internal bookkeeping.'),
    (r'Product type not found for id|Unknown interopProdType', 'hush', 'Skipped a product ID it does not know in the compatibility data (harmless, very common).'),
    (r'BROWNFIELD_IMPORT_FEATURE', 'noise', 'Feature check (routine).'),
    (r'Base image sync failed|Exception while syncing Base Install Images', 'hush', 'Could not sync the base install images at start-up (harmless in this lab).'),
    (r'Resource type is unknown THIRD_PARTY', 'hush', 'Skipped an add-on it does not track (harmless).'),
    (r'Failed to find bundle manifest json for bundleId', 'hush', 'Looked for a bundle that is not downloaded (normal in this offline lab).'),
    (r'Download Progress of : (?P<f>\S+) : (?P<mb>[\d.]+) MB', 'noise', 'Downloading {f}: {mb} MB so far.'),
    (r'Error occured while fetching FTT detail', 'hush', 'Could not read the vSAN failures-to-tolerate setting of a datastore (harmless in this lab).'),
    (r'Failed to generate \w+ Metrics data|Telemetry was blocked', 'noise', 'Could not produce a usage or telemetry figure (harmless).'),
    (r'ignored client requested version', 'hush', 'Agreed an API version with vCenter (routine).'),
    (r'NSX version \S+ is not supported for addVi', 'hush', 'Checked whether NSX could be shared with a new workload domain (not used here).'),
    (r'When the query was interrupted/timed out|Query execution timed out', 'watch',
     'An inventory query to vCenter timed out. Expected while vCenter restarts or switches over during its upgrade; a problem if it goes on.'),
    (r'Failed to connect to https://(?P<h>[^:/]+)\S* as', 'watch',
     'Could not log in to {h}. Expected for a few minutes while it restarts during an upgrade; SDDC Manager retries.'),
    (r'https://(?P<h>[^:/\s]+)\S* responded with status (?P<c>\d+); request will be automatically re-executed', 'watch',
     '{h} answered with HTTP {c}; the request is retried by itself (normal while it restarts).'),
    (r"Keep alive query for '\w+' property execution failed for datasource: (?P<h>\S+)", 'watch',
     'Lost the connection to {h} for a moment (normal while it restarts during an upgrade).'),

    # --- VCF Operations lifecycle (fleet services on the VCF services runtime) -----------------------
    (r'task started with ID: (?P<id>\S+)', 'progress', lambda m, s: f'{tidy(s.split(" task started")[0])} task started.'),
    (r'Updating SDDC service task: \S+ .*with status: (?P<st>\w+)', 'progress', lambda m, s: f'An SDDC lifecycle service task is now {m["st"].replace("_", " ").lower()}.'),
    (r'SDDC service task \S+ completed successfully', 'progress', 'An SDDC lifecycle service task finished successfully.'),
    (r'unsupported NamedGroup|VCFA component not found in component repository|No VCFA details found', 'hush', 'Routine check (harmless).'),
    (r'Fleet Depot|FDS|svc accounts|truststore', 'noise', 'Routine depot and certificate housekeeping.'),

    # --- vCenter logs (vSphere scenarios) ------------------------------------------------------------
    (r'\[VpxLRO\] -- BEGIN (?P<t>task-\d+) -- (?P<e>\S*) -- (?P<op>[\w.]*(?:enterMaintenanceMode|exitMaintenanceMode|reboot|rebootHost|shutdownHost|powerOn|powerOff|PowerOnVm|PowerOffVm|reconnect|disconnect|relocate|migrate|ImportVApp|ConfigureHost|ConfigureCluster|applyHA|Remediate|reconfigureDas|destroy|register)[\w.]*)', 'progress',
     lambda m, s: f'vCenter task started: {vc_op(m["op"])}' + (f' on {m["e"]}' if m['e'] and not m['e'].startswith('session') else '') + f' ({m["t"]}).'),
    (r'\[VpxLRO\] -- ERROR (?P<t>task-\d+) -- .*-- (?P<op>[\w.]+): (?P<f>\S+)', 'problem',
     lambda m, s: f'vCenter task failed: {vc_op(m["op"])} ({m["t"]}): {m["f"].split(".")[-1]}.'),
    (r'\[VpxLRO\] -- (?:BEGIN|FINISH|ERROR) (?:lro|task)-', 'hush', 'Internal vCenter call or task (routine).'),
    (r'label:PHASE:(?P<ph>\w+)\) completed', 'progress', lambda m, s: f'vCenter upgrade: the {RDU_PHASES.get(m["ph"], m["ph"])} phase finished.'),
    (r'Appliance deployment state: (?P<st>\w+)', 'progress', lambda m, s: f'Setting up the new vCenter appliance: {m["st"].replace("_", " ").lower()}.'),
    (r'Remaining file data: (?P<n>\d+)', 'progress', 'Copying data to the new vCenter: {n} bytes left.'),
    (r'Successfully replicated|filereplication/watcher|Ignoring rsync failure due to missing file', 'hush', 'Copied files to the new vCenter (data replication).'),
    (r'retry: done after|Running command|Done running command|Run command \[|Got session for|Get VMOMI client|Release VMOMI client|'
     r'Using service account HOK signer|wfstatedb', 'hush', 'Upgrade orchestrator housekeeping.'),
    (r'STS Issue HOK for service account failed|Failed to get HOK signer|Request to TES failed|Error during token exchange|'
     r'Authentication failed: a general error occurred while exchanging the token|Unable to authenticate user', 'hush',
     'A service-account login failed and is retried (seen during a normal vCenter upgrade in this lab).'),
    (r'InventoryMonitor|Downloaded and validated vibs|RequireAdminUserAuthz|ExtractSessionInfo|Activation opId|AuthzServiceHelper|PrivilegeChecker|'
     r'TelemetryVapiProvider|SamlAsyncApiProvider|Service Account Client|SvcAcctClient|SAML token|Token expiry|Threshold expiry|token from cache|'
     r'Getting Management IP|PrivateKey from VECS|SSL context using machine certificate|STS key resolver|GetCachedDepotBaseImage|'
     r'GetSoftwareInfoForSpec|GetBaseImageInfoForSpec|telemetry data queue|HTTP Connection has timed out while waiting for further requests', 'hush',
     'vSphere Lifecycle Manager housekeeping (routine).'),
    (r"Can't find OAuthSessionObject|OptionValue with key:\S+ has empty value|No managed dpus present|Unable to findcounter id for dpu|"
     r'Failed to cast authentication token helper|VMOMI activation LRO failed|vim.fault.InvalidName', 'hush', 'vCenter housekeeping (routine, harmless).'),

    # --- things worth watching ----------------------------------------------------------------------
    (r'certificate chain mismatch', 'watch',
     'The installer is replacing its own certificate with one signed by vc-mgmt-a\'s VMCA. From here the VCF Installer web page '
     'changes certificate: if Firefox blocks it, close and reopen Firefox (the lab trusts that root at boot). This monitor keeps working.'),
    (r'Failed to install certificates on VCF Installer appliance', 'watch',
     'Installing the new certificate on the installer did not work first time. It is retried; watch for the same line again.'),
    (r'Host key validation failed|Server key did not validate|presented wrong key type|Fail to get host key', 'watch',
     'The SSH key of an appliance was not the one it expected. Normal straight after that appliance is deployed or redeployed; it re-learns the key.'),
    (r'Error while changing file (?P<f>\S+) permission', 'watch', 'Could not change the permissions of {f} on the depot share. Usually harmless.'),
    (r'certificate_unknown|SSL handshake .* failed|SSL exception connecting to (?P<h>\S+)', 'watch',
     'A connection was refused because a certificate is not trusted yet. Expected for a few minutes after certificates are replaced; it retries.'),
    (r'Attempting retrust|Failed to retrust|Failed to perform the retrust', 'watch', 'Re-reading the certificate of an appliance that just changed it.'),
    (r'Retrying request to \{s\}->https://(?P<h>[^:/]+)', 'watch', 'Retrying a request to {h}.'),
    (r'Test connection to (?P<h>\S+) failed\. class java\.net\.(?P<e>\w+)', 'watch',
     'Could not connect to {h} ({e}). Normal while that appliance is still starting.'),
    (r'ComputeManager not found for vc (?P<v>\S+)', 'watch',
     'NSX does not list {v} as a compute manager yet. Expected before NSX is configured; after that, see the lessons learned on duplicate root certificates.'),
    (r'VSP Cluster ID is unknown', 'watch', 'The runtime cluster ID is not known yet (only matters if a clean-up is needed after a failure).'),
    (r'DataService call failed for following data sources', 'watch', 'An inventory query to one source failed (often NSX while it restarts).'),
    (r'(?i)relocat\w* .*vMotion|vMotion .*fail', 'watch',
     'A vMotion during the relocate step. In this nested lab vCenter self-vMotions are reported as failed and retried; see the lessons learned.'),
    (r'ValidateVsanEsaHclCompatibility', 'watch', 'The vSAN HCL check. It enables and disables vSAN on the listed hosts (see the lessons learned).'),
    (r'(?i)\btimed? ?out\b', 'watch', 'Something timed out. Fine if the next lines show a retry that works.', 'GENERIC'),
    (r'VCF_ERROR_INTERNAL_SERVER_ERROR for validation aggregation', 'noise', 'Collecting validation results (bookkeeping, despite the word ERROR).'),
    (r'VCF_ERROR_(?P<code>\w+)', 'problem', lambda m, s: f'Error {m["code"].replace("_", " ").lower()}: {tidy(s)[:220]}'),
]
RULES = [(re.compile(r[0]), r[1], r[2], r[3] if len(r) > 3 else None) for r in RULES]


RDU_PHASES = {'Initialize': 'start', 'Precheck': 'precheck', 'DownloadOva': 'download', 'Stage': 'stage', 'Expand': 'expand',
              'ScheduleWait': 'wait for switchover', 'SourceSwitchover': 'source switchover', 'DbReplication': 'database copy',
              'TargetSwitchover': 'target switchover', 'Contract': 'clean-up', 'PostUpgrade': 'post-upgrade'}


def vc_op(op):
    """'vim.HostSystem.enterMaintenanceMode' -> 'Host System: enter maintenance mode'."""
    parts = op.split('.')
    known = {'ImportVAppLRO': 'deploy an appliance (import vApp)', 'ConfigureCluster': 'configure the cluster',
             'ConfigureHost': 'configure the host', 'powerOn': 'power on', 'powerOff': 'power off'}
    meth = known.get(parts[-1]) or words(parts[-1][:1].upper() + parts[-1][1:]).lower()
    obj = words(parts[-2]) if len(parts) > 1 else ''
    obj = {'Das Config': 'vSphere HA', 'Host System': 'Host', 'Virtual Machine': 'VM', 'Resource Pool': 'Resource pool',
           'Cluster Compute Resource': 'Cluster'}.get(obj, obj)
    return f'{obj}: {meth}' if obj else meth


def tidy(msg):
    msg = UUID_RE.sub('<id>', msg)
    msg = re.sub(r'-----BEGIN CERTIFICATE-----\S*', '<certificate>', msg)
    return msg.strip()


def template(text):
    t = UUID_RE.sub('#', text)
    t = re.sub(r'\b[0-9a-z]{20,}\b', '#', t)
    return re.sub(r'\d+', '#', t)[:140]


VERY_QUIET = {'vum', 'vpxd', 'vlcm'}   # vCenter: hundreds of routine warnings/errors an hour; failures show in the task list
QUIET_SOURCES = {'lcm', 'vum', 'vpxd', 'vlcm', 'fleetupgrade', 'fleetbuild', 'sddcupgrade', 'sddcbuild'}   # logs that are mostly polling: unknown INFO lines there count as routine


def explain(level, cls, msg, quiet=False, very_quiet=False):
    """-> (severity, plain-English text)."""
    short = cls.split('.')[-1]
    if short == 'InMemoryLogger':
        return 'progress', _bootstrap(None, msg)
    for rx, sev, out, only in RULES:
        if only == 'GENERIC':
            if very_quiet:
                continue       # catch-all rules are too broad for the very noisy vCenter logs
        elif only and short != only:
            continue
        m = rx.search(msg)
        if m:
            text = out(m, msg) if callable(out) else out.format(**{k: (v or '') for k, v in m.groupdict().items()})
            return sev, text
    who = words(cls) or 'It'
    st = STEP_RE.match(msg)
    if st and level == 'INFO':
        return 'progress', f'{st["st"].replace("_", " ").capitalize()}: {tidy(st["m"])[:240]}'
    body = tidy(msg)
    if len(body) > 240:
        body = body[:240] + ' ...'
    if very_quiet:
        return 'hush', f'{who}: {body}'
    if level == 'ERROR':
        if re.search(r'(?i)exception|fail|error|unable|cannot|could not', msg):
            return 'problem', f'{who} reported an error: {body}'
        return 'watch', f'{who} logged this as an error: {body}'
    if level in ('WARN', 'WARNING'):
        return 'watch', f'{who} warns: {body}'
    if short in CHATTY or quiet:
        return 'noise', f'{who}: {body}'
    if short.endswith('Action') or 'Orchestrat' in short:
        return 'progress', f'{who}: {body}'
    return 'info', f'{who}: {body}'


class LogFeed:
    """Parses log lines into explained entries; folds repeats; keeps the latest KEEP_ENTRIES."""

    def __init__(self, name, keep=None):
        self.name = name
        self.keep = keep or KEEP_ENTRIES
        self.lock = threading.Lock()
        self.entries = collections.OrderedDict()
        self.seq = 0
        self.rev = 0
        self.last = None
        self.last_ts = ''
        self.status = 'not connected yet'
        self.connected = False

    def feed(self, line, skip_until=None, src=''):
        line = line.rstrip('\n').rstrip('\r')
        if not line.strip():
            return
        m = parse_line(line)
        with self.lock:
            if not m:
                # Continuation of the previous entry (stack trace, JSON, multi-line message).
                if self.last is not None and len(self.last['more']) < 40:
                    self.last['more'].append(line[:300])
                    self.rev += 1
                    self.last['rev'] = self.rev
                return
            ts, level, cls, msg = m
            if skip_until and ts <= skip_until:
                self.last = None
                return
            if level in ('DEBUG', 'TRACE'):
                self.last = None
                return
            self.last_ts = ts
            sev, text = explain(level, cls, msg, quiet=src in QUIET_SOURCES, very_quiet=src in VERY_QUIET)
            hush = sev == 'hush'
            if hush:
                sev = 'noise'
            hl = sev != 'noise'
            if level in ('ERROR', 'WARN', 'WARNING') and sev in ('noise', 'progress', 'info'):
                # Looks like a failure in the raw log but is known to be harmless.
                # ERROR lines stay in Highlights (they are the ones that worry people);
                # routine WARNING lines and settings-block fragments do not.
                hl = not hush and not text.startswith('Part of a settings block') and (level == 'ERROR' or sev != 'noise')
                sev = 'benign'
            # Fold on what is shown: lines that read the same in plain English are one row.
            key = (src, sev, template(text))
            self.rev += 1
            prev = None
            for i, cand in enumerate(reversed(self.entries.values())):
                if i >= FOLD_WINDOW:
                    break
                if cand['key'] == key:
                    prev = cand
                    break
            if prev:
                prev['count'] += 1
                prev['last'] = ts
                prev['rev'] = self.rev
                prev['raw'] = msg[:RAW_MAX]
                prev['level'] = level
                prev['text'] = text
                prev['more'] = []
                prev['sev'], prev['hl'] = sev, hl
                self.last = prev
                return
            self.seq += 1
            e = {'seq': self.seq, 'rev': self.rev, 'ts': ts, 'last': ts, 'level': level, 'cls': cls.split('.')[-1],
                 'sev': sev, 'hl': hl, 'text': text, 'raw': msg[:RAW_MAX], 'more': [], 'count': 1, 'key': key, 'src': src}
            self.entries[self.seq] = e
            self.last = e
            while len(self.entries) > self.keep:
                self.entries.popitem(last=False)

    def since(self, rev):
        """Copies of the entries changed after rev, the oldest seq still kept, and the new rev."""
        with self.lock:
            out = [dict(e, more=list(e['more'])) for e in self.entries.values() if e['rev'] > rev]
            first = next(iter(self.entries), 0)
            return out, first, self.rev


# ---------------------------------------------------------------------------
# Log tail over SSH
# ---------------------------------------------------------------------------

class LogSource:
    """One log file on one appliance, with its daily rotated copies (for reading past time windows)."""

    def __init__(self, key, title, short, host, user, password, directory, current, rotated, local_file=None, ordered=True, drop=None):
        # rotated: shell patterns; {d} is replaced by each day (YYYY-MM-DD) of the window.
        # ordered=False: the files are not named by date, so read them all (oldest first) and do not stop early.
        self.ordered = ordered
        self.drop = drop      # extended regex: lines to leave out before they are sent (huge, routine lines)
        self.key, self.title, self.short = key, title, short
        self.host, self.user, self.password = host, user, password
        self.directory, self.current, self.rotated = directory, current, rotated
        self.local_file = local_file

    history_note = ''

    @property
    def path(self):
        return f'{self.directory}/{self.current}'

    def tail_command(self, n):
        if self.drop:
            return f"tail -n {n * 20} -F {self.path} 2>/dev/null | grep --line-buffered -v -E -e '{self.drop}'"
        return f'tail -n {n} -F {self.path} 2>/dev/null'

    def window_command(self, start, end, max_lines):
        return None   # use the files


class KubeLogSource(LogSource):
    """A pod's log on the VCF services runtime, read with kubectl (as root, via sudo) on its control-plane node."""
    history_note = ('The VCF services runtime only keeps about the last hour of these logs (and starts again when '
                    'a pod restarts), so older lines are gone.')

    def __init__(self, key, title, short, node, password, namespace, match):
        super().__init__(key, title, short, node, 'vmware-system-user', password, '', '', [])
        self.namespace, self.match = namespace, match

    @property
    def path(self):
        return f'{self.namespace}/{self.match} (kubectl logs)'

    def _wrap(self, args):
        import base64
        inner = ('export KUBECONFIG=/etc/kubernetes/admin.conf; '
                 f'P=$(kubectl get pods -n {self.namespace} -o name | grep {self.match} | grep -v -- -db- | head -1); '
                 f'kubectl logs -n {self.namespace} $P {args}')
        b64 = base64.b64encode(inner.encode()).decode()
        return f"echo '{self.password}' | sudo -S -p '' bash -c 'echo {b64} | base64 -d | bash' 2>/dev/null"

    def tail_command(self, n):
        return self._wrap(f'-f --tail={n}')

    def window_command(self, start, end, max_lines):
        return (self._wrap(f'--since-time={start[:19]}Z')
                + f" | awk -v b='{end}' '/^20[0-9][0-9]-/{{ if ($1>b) exit }} 1' | head -n {max_lines + 1}")


def ssh_connect(src):
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        import paramiko
    import logging
    logging.getLogger('paramiko').setLevel(logging.CRITICAL)
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect(src.host, username=src.user, password=src.password, timeout=15, banner_timeout=30,
                auth_timeout=30, look_for_keys=False, allow_agent=False)
    return cli


def tail_worker(feed, src, backlog, stop):
    if src.local_file:   # testing: replay a saved log
        opener = gzip.open if src.local_file.endswith('.gz') else open
        with opener(src.local_file, 'rt', errors='replace') as f:
            for line in f:
                feed.feed(line, src=src.key)
        feed.status, feed.connected = f'replaying {src.local_file}', True
        return
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            import paramiko  # noqa: F401
    except ImportError:
        feed.status = 'paramiko is not installed (pip install paramiko)'
        return
    first = True
    while not stop.is_set():
        cli = None
        try:
            socket.getaddrinfo(src.host, 22)
            feed.status = f'connecting to {src.host}'
            cli = ssh_connect(src)
            tr = cli.get_transport()
            tr.set_keepalive(20)
            chan = tr.open_session()
            chan.settimeout(5)
            chan.exec_command(src.tail_command(backlog if first else 2000))
            skip = None if first else feed.last_ts
            first = False
            feed.connected = True
            feed.status = f'following {src.path} on {src.host}'
            buf = b''
            while not stop.is_set():
                try:
                    data = chan.recv(65536)
                except socket.timeout:
                    if not tr.is_active() or chan.exit_status_ready():
                        break
                    continue
                if not data:
                    break
                buf += data
                *lines, buf = buf.split(b'\n')
                for ln in lines:
                    feed.feed(ln.decode('utf-8', 'replace'), skip_until=skip, src=src.key)
            feed.status = f'connection to {src.host} closed, reconnecting'
        except Exception as e:
            msg = str(e) or e.__class__.__name__
            if isinstance(e, socket.gaierror):
                msg = 'not in DNS yet (not deployed yet?)'
            elif 'Authentication failed' in msg:
                msg = f'login as {src.user} was refused (password changed?)'
            elif isinstance(e, (socket.timeout, ConnectionRefusedError, OSError)):
                msg = f'not reachable yet ({msg[:60]})'
            feed.status = f'{src.host}: {msg[:120]}; trying again every 30 s'
        finally:
            feed.connected = False
            try:
                cli and cli.close()
            except Exception:
                pass
        stop.wait(30)


def fetch_window(src, start, end, max_lines=200000):
    """(lines, truncated): the lines of one log from start to end (ISO UTC times).
    Reads the rotated files for those days (oldest first), then the current log, and stops
    at the first line after end, so finished steps work too."""
    def wanted(stream):
        out, p = [], False
        for line in stream:
            if line[:2] == '20' and line[4:5] == '-':
                ts = line.split(' ', 1)[0]
                if ts > end:
                    break
                p = ts >= start
            if p:
                out.append(line.rstrip('\n'))
                if len(out) >= max_lines:
                    return out, True
        return out, False
    if src.local_file:
        opener = gzip.open if src.local_file.endswith('.gz') else open
        with opener(src.local_file, 'rt', errors='replace') as f:
            return wanted(f)
    custom = src.window_command(start, end, max_lines)
    if custom:
        cli = ssh_connect(src)
        try:
            _, out, _ = cli.exec_command(custom, timeout=180)
            lines = out.read().decode('utf-8', 'replace').splitlines()
        finally:
            cli.close()
        return lines[:max_lines], len(lines) > max_lines
    d0 = parse_ts(start).date()
    d1 = parse_ts(end).date()
    days = []
    while d0 <= d1 and len(days) < 40:
        days.append(d0.isoformat())
        d0 += datetime.timedelta(days=1)
    pats = list(dict.fromkeys(pat.format(d=d) for d in days for pat in src.rotated))
    if src.ordered:
        parts = [f'for f in $(ls -v {pat} 2>/dev/null); do zcat -f "$f"; done;' for pat in pats]
        stop = 'if ($1>b) exit; '
    else:
        parts = [f'for f in $(ls -tr {" ".join(pats)} 2>/dev/null); do zcat -f "$f"; done;']
        stop = ''
    drop = f" | grep -v -E -e '{src.drop}'" if src.drop else ''
    cmd = (f'cd {src.directory} && {{ {" ".join(parts)} cat {src.current}; }} 2>/dev/null{drop}'
           f" | awk -v a='{start}' -v b='{end}' '/^20[0-9][0-9]-/{{ {stop}p=($1>=a && $1<=b) }} p' | head -n {max_lines + 1}")
    cli = ssh_connect(src)
    try:
        _, out, _ = cli.exec_command(cmd, timeout=180)
        lines = out.read().decode('utf-8', 'replace').splitlines()
    finally:
        cli.close()
    return lines[:max_lines], len(lines) > max_lines


class SddcManagerTasks:
    """SDDC Manager's own task list (GET /v1/tasks), with each task's sub-steps."""

    def __init__(self, host, user, password):
        self.host, self.user, self.password = host, user, password
        self.token = None
        self.ctx = ssl._create_unverified_context()
        self.cache = {}
        self.tasks = []
        self.error = ''

    def _call(self, method, path, body=None):
        req = urllib.request.Request(f'https://{self.host}{path}', method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        if self.token:
            req.add_header('Authorization', f'Bearer {self.token}')
        with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
            return json.loads(r.read() or b'{}')

    def refresh(self, since):
        if self.host.startswith('file:'):      # testing: a saved copy of self.tasks
            with open(self.host[5:]) as f:
                self.tasks = json.load(f)
            return self.tasks
        try:
            if not self.token:
                # admin@local on 9.x; a 5.2 SDDC Manager may only have the SSO administrator.
                for u in dict.fromkeys([self.user, 'administrator@vsphere.local']):
                    try:
                        self.token = self._call('POST', '/v1/tokens', {'username': u, 'password': self.password})['accessToken']
                        break
                    except urllib.error.HTTPError:
                        continue
                if not self.token:
                    raise RuntimeError('login refused')
            out = []
            listed = sorted(self._call('GET', '/v1/tasks').get('elements') or [], key=lambda x: x.get('creationTimestamp') or '')
            for t in listed[-40:]:
                if since and (t.get('creationTimestamp') or '') < since:
                    continue
                c = self.cache.get(t['id'])
                if c is None or kind(c.get('status')) in ('running', 'waiting'):
                    try:
                        c = self._call('GET', f'/v1/tasks/{t["id"]}')
                    except Exception:
                        c = t
                    self.cache[t['id']] = c
                subs = [{'name': x.get('description') or x.get('name'), 'kind': kind(x.get('status')),
                         'start': x.get('creationTimestamp'), 'end': x.get('completionTimestamp'), 'errors': x.get('errors')}
                        for x in c.get('subTasks') or []]
                out.append({'id': t['id'], 'name': c.get('name') or t.get('name'), 'kind': kind(c.get('status')),
                            'type': c.get('type') or t.get('type') or '', 'status': c.get('status') or '',
                            'start': c.get('creationTimestamp'), 'end': c.get('completionTimestamp'),
                            'errors': c.get('errors'), 'tasks': subs})
            self.tasks = sorted(out, key=lambda x: x['start'] or '')
            self.error = ''
        except Exception as e:
            self.token = None   # not deployed yet, restarting, or a new certificate: try again next time
            msg = str(e)
            if isinstance(e, urllib.error.URLError) and isinstance(getattr(e, 'reason', None), socket.gaierror):
                msg = 'name not found in DNS'
            self.error = msg[:120] or e.__class__.__name__
        return self.tasks


# ---------------------------------------------------------------------------
# Installer task list
# ---------------------------------------------------------------------------

class Installer:
    def __init__(self, host, user, password, state_file=None):
        self.host, self.user, self.password = host, user, password
        self.state_file = state_file
        self.token = None
        self.ctx = ssl._create_unverified_context()
        self.state = {'status': 'Reading the installer task list...', 'at': now()}

    def _call(self, method, path, body=None):
        req = urllib.request.Request(f'https://{self.host}{path}', method=method,
                                     data=json.dumps(body).encode() if body is not None else None,
                                     headers={'Content-Type': 'application/json', 'Accept': 'application/json'})
        if self.token:
            req.add_header('Authorization', f'Bearer {self.token}')
        with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
            return json.loads(r.read() or b'{}')

    def get(self, path, retried=False):
        if not self.token:
            self.token = self._call('POST', '/v1/tokens', {'username': self.user, 'password': self.password})['accessToken']
        try:
            return self._call('GET', path)
        except urllib.error.HTTPError as e:
            if e.code == 401 and not retried:
                self.token = None
                return self.get(path, True)
            raise

    def refresh(self):
        try:
            if self.state_file:
                with open(self.state_file) as f:
                    d = json.load(f)
            else:
                d = self.get('/v1/sddcs')
            self.state = summarise(d.get('elements') or [])
        except Exception as e:
            msg = str(e)
            if isinstance(e, urllib.error.URLError) and isinstance(getattr(e, 'reason', None), socket.gaierror):
                msg = 'name not found in DNS'
            self.token = None
            self.state = dict(self.state, status=f'Could not read the VCF Installer API ({msg[:120]}). Trying again every 10 seconds.',
                              error=True, at=now())


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def kind(status):
    s = str(status or '').upper()
    if 'FAIL' in s or 'ERROR' in s:
        return 'failed'
    if 'PROGRESS' in s or 'RUNNING' in s or s in ('STARTED', 'ACTIVE'):
        return 'running'
    if 'SUCCESS' in s or s in ('COMPLETED', 'SUCCEEDED', 'SKIPPED'):
        return 'done'
    return 'waiting'


def summarise(elements):
    if not elements:
        return {'status': 'No deployment has been started on the VCF Installer yet. Upload and validate the JSON spec, '
                          'then press Deploy. This window picks it up by itself.', 'none': True, 'at': now()}
    d = max(elements, key=lambda e: e.get('creationTimestamp') or '')
    subs = d.get('sddcSubTasks') or []
    groups = collections.OrderedDict()
    for s in subs:
        groups.setdefault(s.get('milestoneTask') or 'Other', []).append(s)
    ms = []
    for m in d.get('milestones') or [{'name': n} for n in groups]:
        items = groups.get(m.get('name'), [])
        done = m.get('completedTasks')
        total = m.get('totalTasks')
        if done is None:
            done = sum(1 for s in items if kind(s.get('status')) == 'done')
            total = len(items)
        mk = kind(m.get('status')) if m.get('status') else (
            'failed' if any(kind(s.get('status')) == 'failed' for s in items) else
            'running' if any(kind(s.get('status')) == 'running' for s in items) else
            'done' if items and done == total else 'waiting')
        ms.append({'name': m.get('name'), 'kind': mk, 'status': m.get('status', ''), 'done': done, 'total': total,
                   'start': m.get('creationTimestamp'), 'end': m.get('updateTimestamp'),
                   'typical': TYPICAL.get(m.get('name'), ''),
                   'tasks': [{'name': s.get('description') or s.get('name'), 'kind': kind(s.get('status')),
                              'status': s.get('status', ''), 'start': s.get('creationTimestamp'),
                              'end': s.get('updateTimestamp'),
                              'errors': s.get('errors') or s.get('errorDescription') or s.get('error')}
                             for s in items]})
    running = [t for m in ms for t in m['tasks'] if t['kind'] == 'running']
    failed = [t for m in ms for t in m['tasks'] if t['kind'] == 'failed']
    return {'id': d.get('id'), 'name': d.get('name'), 'type': d.get('deploymentType'), 'instance': d.get('vcfInstanceName'),
            'overall': d.get('status'), 'kind': kind(d.get('status')), 'started': d.get('creationTimestamp'),
            'milestones': ms, 'running': running, 'failed': failed, 'status': 'ok', 'at': now()}


def parse_ts(ts):
    if not ts:
        return None
    try:
        return datetime.datetime.fromisoformat(str(ts).replace('Z', '+00:00'))
    except ValueError:
        return None


def local_time(ts, fmt='%H:%M:%S'):
    d = parse_ts(ts)
    return d.astimezone().strftime(fmt) if d else ''


def when(ts):
    """'Thu 13:26', or '29 Oct 13:26' when it is more than 6 days ago."""
    d = parse_ts(ts)
    if not d:
        return ''
    old = (datetime.datetime.now(datetime.timezone.utc) - d).days > 6
    return d.astimezone().strftime('%d %b %H:%M' if old else '%a %H:%M')


def dur(start, end=None):
    a = parse_ts(start)
    if not a:
        return ''
    b = parse_ts(end) or datetime.datetime.now(datetime.timezone.utc)
    mins = int((b - a).total_seconds() // 60)
    if mins < 1:
        return '<1 min'
    if mins < 60:
        return f'{mins} min'
    h, m = divmod(mins, 60)
    return f'{h} h {m} min' if m else f'{h} h'


def error_text(err):
    if not err:
        return ''
    if isinstance(err, str):
        return err
    if isinstance(err, list):
        return '\n'.join(error_text(x) for x in err)
    if isinstance(err, dict):
        for k in ('message', 'errorMessage', 'description', 'msg'):
            if err.get(k):
                extra = err.get('remediationMessage') or err.get('remediation') or ''
                return str(err[k]) + (f'\nWhat to do: {extra}' if extra else '')
        return json.dumps(err)
    return str(err)


HAS_JSON_RE = re.compile(r'[\[{]\s*["{\[]')


def tip_text(t):
    parts = [x for x in (t.get('note'), error_text(t.get('errors'))[:1500]) if x]
    return '\n\n'.join(parts)


def json_note(objs):
    """What to tell someone who wants to reuse JSON copied from the log."""
    note = ('This is how the component logged the data, so use it as a guide to the field names and structure. '
            'Passwords show as *****, and the installer can add entries of its own')
    dups = []

    def walk(o, path):
        if isinstance(o, dict):
            for k, v in o.items():
                walk(v, f'{path}.{k}' if path else k)
        elif isinstance(o, list):
            seen = [json.dumps(x, sort_keys=True) for x in o]
            if len(seen) != len(set(seen)) and path not in dups:
                dups.append(path)
            for x in o:
                walk(x, path + '[]')
    for o in objs:
        walk(o, '')
    if dups:
        note += f' (here, {", ".join(dups[:3])} has the same entry more than once; do not copy the repeat)'
    spec = any(isinstance(o, dict) and ('sddcId' in o or 'workflowType' in o) for o in objs)
    if spec:
        return (note + '. For input you can submit as it is, start from the converge spec in ~/Lab Files '
                '(VCF-9.1.1-converge-from-vSphere-9.1.1.json).')
    return note + '. Check it against the API documentation before you use it as input.'


def has_json(text):
    return bool(HAS_JSON_RE.search(text))


def find_json(text, limit=5):
    """The JSON objects and arrays written inside a log message (only ones with content)."""
    found, dec, i = [], json.JSONDecoder(), 0
    while len(found) < limit:
        starts = [p for p in (text.find('{', i), text.find('[', i)) if p >= 0]
        if not starts:
            break
        p = min(starts)
        try:
            obj, end = dec.raw_decode(text, p)
            if isinstance(obj, (dict, list)) and obj and (len(json.dumps(obj)) > 20):
                found.append(obj)
                i = end
                continue
        except ValueError:
            pass
        i = p + 1
    return found


# ---------------------------------------------------------------------------
# Scenarios: what to watch (task list + logs)
# ---------------------------------------------------------------------------
#
# Every scenario's task list is turned into the same "state" for the window:
#   card:  big / now / warn / meta text and cls ('' | 'done' | 'failed')
#   items: top-level rows (name, kind, done, total, start, end, typical, tasks, label, note)
#   group: optional extra section {title, subtitle, items}
#   numbered: whether the top-level rows are numbered (milestones are)

# Failures that are expected in this lab (no internet): shown in yellow, never as the headline.
EXPECTED_FAILURES = {
    'UMDS_SYNC': 'Expected in this lab: SDDC Manager cannot reach the online depot to download ESX metadata.',
}


def converge_state(st, sddc_tasks, interval=10):
    """The VCF Installer deployment (summarise() output) as a window state."""
    updated = f'Updated {local_time(st.get("at"))}. Refreshes every {interval} seconds.'
    if not st.get('milestones'):
        return {'error': st.get('error'), 'at': st.get('at'), 'items': [], 'group': None, 'numbered': True,
                'card': {'big': 'Cannot read the VCF Installer' if st.get('error') else 'VCF Installer',
                         'now': st.get('status', ''), 'warn': '', 'meta': updated,
                         'cls': 'failed' if st.get('error') else ''}}
    ms = st['milestones']
    idx = next((i for i, m in enumerate(ms) if m['kind'] == 'running'), -1)
    cur = ms[idx] if idx >= 0 else None
    cls, now_text, warn = '', '', ''
    if st['kind'] == 'done':
        big = f'Finished: {st["name"]} deployed successfully'
        now_text = f'Ran for {dur(st["started"], ms[-1]["end"])}.'
        cls = 'done'
    elif st['failed']:
        big = f'Stopped on a failure: {st["failed"][0]["name"]}'
        err = error_text(st['failed'][0].get('errors'))
        now_text = ('Read the red Error lines in the log on the right (filter: Errors only), fix the cause, '
                    'then use Retry in the VCF Installer.')
        if err:
            now_text += f'\n\nThe installer says: {err[:600]}'
        cls = 'failed'
    elif cur:
        big = f'Milestone {idx + 1} of {len(ms)}: {cur["name"]}'
        now_text = f'{cur["done"]} of {cur["total"]} tasks done in this milestone, running for {dur(cur["start"])}'
        now_text += f' (it took about {cur["typical"]} in this lab).' if cur['typical'] else '.'
        if st['running']:
            now_text += f'\nNow: {"; ".join(t["name"] for t in st["running"])} (for {dur(st["running"][0]["start"])}).'
    else:
        big = str(st.get('overall') or '').replace('_', ' ').capitalize()
    cert = next((t for m in ms for t in m['tasks'] if re.search(r'VMCA Certificate on SDDC Manager', t['name'] or '', re.I)), None)
    if cert and cert['kind'] == 'running':
        warn = ('\u26A0 The installer is switching to a VMCA certificate. If Firefox blocks the VCF Installer '
                'page, close and reopen Firefox. This monitor keeps working.')
    meta = (f'{st.get("instance") or ""} ({st.get("name") or ""}), started {local_time(st.get("started"), "%a %H:%M")}. '
            + updated + (f'\n{st["status"]}' if st.get('error') else ''))
    items = [dict(m, label=f'Milestone {i + 1}: {m["name"]}') for i, m in enumerate(ms)]
    group = None
    if sddc_tasks:
        group = {'title': 'SDDC Manager tasks', 'items': sddc_items(sddc_tasks, newest_first=False),
                 'subtitle': f"SDDC Manager's own steps, from its API ({len(sddc_tasks)} tasks)"}
    return {'error': st.get('error'), 'at': st.get('at'), 'items': items, 'group': group, 'numbered': True,
            'card': {'big': big, 'now': now_text, 'warn': warn, 'meta': meta, 'cls': cls}}


def sddc_items(tasks, newest_first=True):
    out = []
    for t in (reversed(tasks) if newest_first else tasks):
        subs = t.get('tasks') or []
        note = EXPECTED_FAILURES.get(t.get('type')) if t['kind'] == 'failed' else None
        out.append({'name': t['name'], 'kind': t['kind'], 'done': sum(1 for x in subs if x['kind'] == 'done'),
                    'total': len(subs), 'start': t.get('start'), 'end': t.get('end'), 'typical': '',
                    'tasks': subs, 'label': t['name'], 'errors': t.get('errors'), 'note': note})
    return out


def sddc_state(sm, host, interval):
    tasks = sm.tasks
    updated = f'Updated {local_time(now())}. Refreshes every {interval} seconds.'
    short = host.split('.')[0]
    if not tasks:
        big = f'Cannot read {short}' if sm.error else f'No tasks on {short} yet'
        now_text = (f'Could not read the SDDC Manager API ({sm.error}). Trying again.' if sm.error else
                    'Start the next step in SDDC Manager (or VCF Operations); it shows up here by itself.')
        return {'error': bool(sm.error), 'at': now(), 'items': [], 'group': None, 'numbered': False,
                'card': {'big': big, 'now': now_text, 'warn': '', 'meta': updated, 'cls': 'failed' if sm.error else ''}}
    items = sddc_items(tasks)
    running = [i for i in items if i['kind'] == 'running']
    latest = next((i for i in items if not i.get('note')), items[0])
    cls, warn = '', ''
    if running:
        r = running[-1]
        big = f'Running: {r["name"]}'
        now_text = f'Running for {dur(r["start"])}.'
        if r['total']:
            now_text = f'{r["done"]} of {r["total"]} steps done, running for {dur(r["start"])}.'
            cur = [x for x in r['tasks'] if x['kind'] == 'running']
            if cur:
                now_text += f'\nNow: {"; ".join(x["name"] for x in cur)} (for {dur(cur[0]["start"])}).'
        if len(running) > 1:
            now_text += f'\nAlso running: {"; ".join(x["name"] for x in running[:-1])}.'
    elif latest['kind'] == 'failed':
        big = f'Stopped on a failure: {latest["name"]}'
        err = error_text(latest.get('errors'))
        now_text = ('Read the red Error lines in the logs (filter: Errors only), fix the cause, then use Retry '
                    'in SDDC Manager.' + (f'\n\nSDDC Manager says: {err[:600]}' if err else ''))
        cls = 'failed'
    else:
        big = f'Nothing is running on {short}'
        at = when(latest.get('end') or latest.get('start'))
        now_text = (f'Last task: {latest["name"]} ({latest["kind"]}{", " + at if at else ""}).\n'
                    'Start the next step in SDDC Manager (or VCF Operations); it shows up here by itself.')
        cls = 'done' if latest['kind'] == 'done' else ''
    if sm.error:
        updated += f'\nLast refresh failed ({sm.error}); showing what was read before.'
    return {'error': False, 'at': now(), 'items': items, 'group': None, 'numbered': False,
            'card': {'big': big, 'now': now_text, 'warn': warn, 'meta': f'{host}, newest task first. ' + updated, 'cls': cls}}


class ConvergeWatch:
    """Region A converge: the VCF Installer deployment, plus SDDC Manager A's own tasks."""
    interval = 10

    def __init__(self, o):
        self.inst = Installer(o.installer, o.api_user, o.password, o.state_file)
        self.sm = None
        if o.sddc_tasks_file:
            self.sm = SddcManagerTasks('file:' + o.sddc_tasks_file, '', '')
        elif o.sddc_manager != 'none' and not o.state_file:
            self.sm = SddcManagerTasks(o.sddc_manager, o.api_user, o.password)
        self.n = 0

    def refresh(self):
        self.inst.refresh()
        st = self.inst.state
        if self.sm and st.get('milestones') and self.n % 3 == 0:
            self.sm.refresh(st.get('started'))
        self.n += 1
        return converge_state(st, self.sm.tasks if self.sm else [], self.interval)

    def detect(self):
        self.inst.refresh()
        st = self.inst.state
        if st.get('error'):
            return False, 'Installer not reachable'
        if not st.get('milestones'):
            return False, 'No deployment started yet'
        if st['kind'] == 'running':
            idx = next((i for i, m in enumerate(st['milestones']) if m['kind'] == 'running'), 0)
            return True, f'Running now: milestone {idx + 1} of {len(st["milestones"])}'
        if st['failed']:
            return True, 'Stopped on a failure'
        return False, f'Finished {local_time(st["milestones"][-1].get("end"), "%a %d %b %H:%M")}'


class SddcWatch:
    """SDDC Manager's task list: upgrades, Deploy VCF Management Components, imports, configuration updates."""
    interval = 15

    def __init__(self, o, host):
        self.host = host
        self.sm = SddcManagerTasks('file:' + o.sddc_tasks_file if o.sddc_tasks_file else host, o.api_user, o.password)

    def refresh(self):
        self.sm.refresh(None)
        return sddc_state(self.sm, self.host, self.interval)

    def detect(self):
        tasks = self.sm.refresh(None)
        if not tasks:
            return False, 'Not reachable' if self.sm.error else 'No tasks yet'
        run = [t for t in tasks if t['kind'] == 'running']
        if run:
            return True, f'Running now: {run[-1]["name"]}'
        t = tasks[-1]
        return False, f'Last: {t["name"]} ({t["kind"]}, {when(t.get("end") or t.get("start"))})'


class VcApi:
    """vCenter REST: session login, GET with the session header."""

    def __init__(self, host, user, password):
        self.host, self.user, self.password = host, user, password
        self.sid = None
        self.ctx = ssl._create_unverified_context()

    def _req(self, method, path, headers):
        req = urllib.request.Request(f'https://{self.host}{path}', method=method, headers={'Accept': 'application/json', **headers})
        with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
            return json.loads(r.read() or b'null')

    def get(self, path, retried=False):
        import base64
        if not self.sid:
            auth = base64.b64encode(f'{self.user}:{self.password}'.encode()).decode()
            self.sid = self._req('POST', '/api/session', {'Authorization': f'Basic {auth}'})
        try:
            return self._req('GET', path, {'vmware-api-session-id': self.sid})
        except urllib.error.HTTPError as e:
            if e.code == 401 and not retried:
                self.sid = None
                return self.get(path, True)
            raise


VLCM_OPS = {'apply': 'Remediate', 'scan': 'Check compliance', 'check': 'Run prechecks', 'stage': 'Stage',
            'export': 'Export the image', 'commit': 'Save the image draft'}
RDU_STEPS = {'precheck': 'Prechecks', 'preparation': 'Deploy the new vCenter and copy the data',
             'switchover': 'Switchover to the new vCenter', 'post_upgrade_tasks': 'Post-upgrade tasks'}


def msg_text(m):
    if isinstance(m, str):
        return m
    m = m or {}
    return m.get('default_message') or m.get('defaultMessage') or m.get('localized') or m.get('localizedMessage') or ''


class VsphereWatch:
    """A vCenter: its reduced-downtime upgrade (RDU) status and vSphere Lifecycle Manager operations."""
    interval = 15

    def __init__(self, o, vc, user):
        self.vc = vc
        self.api = VcApi(vc, user, o.password)
        self.error = ''
        self.items = []
        self.clusters = {}
        self.rdu = None

    def read(self):
        items = []
        try:
            r = self.api.get('/api/vcenter/lcm/deployment/migration-upgrade/status')
        except Exception:
            r = None
        self.rdu = r
        if r and not r.get('status'):
            ks = [kind((x or {}).get('status')) for x in (r.get('subtasks') or {}).values()]
            r['status'] = ('FAILED' if 'failed' in ks else 'RUNNING' if 'running' in ks else
                           'SUCCEEDED' if ks and all(k == 'done' for k in ks) else 'PENDING')
            subs0 = [x for x in (r.get('subtasks') or {}).values() if x]
            r.setdefault('start_time', min((x.get('start_time') or '9') for x in subs0) if subs0 else '')
            if r['status'] in ('SUCCEEDED', 'FAILED'):
                r.setdefault('end_time', max((x.get('end_time') or '') for x in subs0) if subs0 else '')
        if r and r.get('status') not in (None, 'PENDING') and (r.get('start_time') or '')[:4] not in ('', '0001', '9'):
            subs = []
            for k in r.get('subtask_order') or list((r.get('subtasks') or {}).keys()):
                x = (r.get('subtasks') or {}).get(k) or {}
                pr = x.get('progress') or {}
                name = RDU_STEPS.get(k, k.replace('_', ' ').capitalize())
                if pr.get('message') and kind(x.get('status')) == 'running':
                    name += f': {msg_text(pr["message"])} ({pr.get("completed", 0)}%)'
                subs.append({'name': name, 'kind': kind(x.get('status')), 'start': x.get('start_time'), 'end': x.get('end_time'),
                             'errors': msg_text(x.get('error')) if isinstance(x.get('error'), dict) else x.get('error')})
            to = (r.get('upgrade_info') or {}).get('upgrade_to') or ''
            pr = r.get('progress') or {}
            items.append({'name': f'vCenter upgrade (reduced downtime){" to " + to if to else ""}', 'kind': kind(r.get('status')),
                          'done': sum(1 for x in subs if x['kind'] == 'done'), 'total': len(subs), 'start': r.get('start_time'),
                          'end': r.get('end_time'), 'typical': '', 'tasks': subs, 'label': 'vCenter upgrade (reduced downtime)',
                          'errors': msg_text(pr.get('message')) if kind(r.get('status')) == 'failed' else None, 'note': None,
                          'message': msg_text(pr.get('message'))})
        if not self.clusters:
            try:
                self.clusters = {c['cluster']: c['name'] for c in self.api.get('/api/vcenter/cluster')}
            except Exception:
                pass
        tasks = self.api.get('/rest/cis/tasks?filter_spec.services.1=com.vmware.esx.settings.clusters.software&result_spec.return_all=true')
        tasks = tasks.get('value', tasks) if isinstance(tasks, dict) else tasks
        since = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=3)).strftime('%Y-%m-%dT%H:%M:%S')
        rows = []
        for t in tasks or []:
            v = t.get('value', t)
            if v.get('parent') or (v.get('start_time') or '') < since:
                continue      # child scans of a remediation, or old
            tgt = (v.get('target') or {}).get('id', '')
            op = v.get('operation', '').split('$')[0]
            notes = []
            for level in ('info', 'warnings', 'errors'):
                for n in (v.get('notifications') or {}).get(level) or []:
                    notes.append((n.get('time') or '', level, msg_text(n.get('message'))))
            notes.sort()
            subs = []
            for i, (tm, level, text) in enumerate(notes):
                nxt = notes[i + 1][0] if i + 1 < len(notes) else v.get('end_time')
                k = 'failed' if level == 'errors' else 'done'
                if i == len(notes) - 1 and kind(v.get('status')) == 'running':
                    k = 'running'
                subs.append({'name': text, 'kind': k, 'start': tm, 'end': nxt, 'errors': text if level == 'errors' else None,
                             'note': text if level == 'warnings' else None})
            pr = v.get('progress') or {}
            name = f'{VLCM_OPS.get(op, op.capitalize())}: cluster {self.clusters.get(tgt, tgt)}'
            rows.append({'name': name, 'kind': kind(v.get('status')), 'done': pr.get('completed', 0) if pr.get('total') else 0,
                         'total': pr.get('total', 0) or 0, 'start': v.get('start_time'), 'end': v.get('end_time'), 'typical': '',
                         'tasks': subs, 'label': name, 'note': None, 'message': msg_text(pr.get('message')),
                         'errors': '\n'.join(n[2] for n in notes if n[1] == 'errors') or None})
        rows.sort(key=lambda x: x['start'] or '', reverse=True)
        items += rows[:25]
        items.sort(key=lambda x: x['start'] or '', reverse=True)
        return items

    def refresh(self):
        try:
            self.items = self.read()
            self.error = ''
        except Exception as e:
            self.api.sid = None
            self.error = str(e)[:120] or e.__class__.__name__
        return vsphere_state(self)

    def detect(self):
        items = self.read()
        run = [i for i in items if i['kind'] == 'running']
        if run:
            return True, f'Running now: {run[0]["name"]}'
        if items:
            i = items[0]
            return False, f'Last: {i["name"]} ({i["kind"]}, {when(i.get("end") or i.get("start"))})'
        return False, 'No upgrades or remediations in the last 3 days'


def vsphere_state(w):
    items = w.items
    updated = f'Updated {local_time(now())}. Refreshes every {w.interval} seconds.'
    short = w.vc.split('.')[0]
    if not items:
        big = f'Cannot read {short}' if w.error else f'Nothing to show on {short} yet'
        now_text = (f'Could not read the vCenter API ({w.error}). Trying again.' if w.error else
                    'No vCenter upgrade or vSphere Lifecycle Manager remediation in the last 3 days. '
                    'Start one (from SDDC Manager or the vSphere Client); it shows up here by itself.')
        return {'error': bool(w.error), 'at': now(), 'items': [], 'group': None, 'numbered': False,
                'card': {'big': big, 'now': now_text, 'warn': '', 'meta': updated, 'cls': 'failed' if w.error else ''}}
    run = [i for i in items if i['kind'] == 'running']
    latest = items[0]
    cls, warn = '', ''
    if run:
        r = run[0]
        big = f'Running: {r["name"]}'
        now_text = f'Running for {dur(r["start"])}.'
        if r.get('message'):
            now_text += f'\nNow: {r["message"]}'
        elif r['tasks']:
            now_text += f'\nLatest step: {r["tasks"][-1]["name"]}'
        if r['label'].startswith('vCenter upgrade'):
            warn = ('\u26A0 During the switchover the vCenter is unavailable for a few minutes, so this list and its '
                    'logs pause and reconnect by themselves.')
    elif latest['kind'] == 'failed':
        big = f'Stopped on a failure: {latest["name"]}'
        err = error_text(latest.get('errors'))
        now_text = ('Read the red Error lines in the logs, fix the cause, then run it again.'
                    + (f'\n\nvCenter says: {err[:600]}' if err else ''))
        cls = 'failed'
    else:
        big = f'Nothing is running on {short}'
        now_text = (f'Last: {latest["name"]} ({latest["kind"]}, {when(latest.get("end") or latest.get("start"))}).\n'
                    'Start the next upgrade or remediation; it shows up here by itself.')
        cls = 'done' if latest['kind'] == 'done' else ''
    if w.error:
        updated += f'\nLast refresh failed ({w.error}); showing what was read before.'
    return {'error': False, 'at': now(), 'items': items, 'group': None, 'numbered': False,
            'card': {'big': big, 'now': now_text, 'warn': warn, 'meta': f'{w.vc}, newest first. ' + updated, 'cls': cls}}


class FleetWatch:
    """VCF Operations lifecycle: the fleet lifecycle service's tasks (installs, upgrades, scale-outs) with their stages."""
    interval = 15

    def __init__(self, o, fleet):
        self.fleet, self.password = fleet, o.password
        self.token = None
        self.ctx = ssl._create_unverified_context()
        self.cache = {}
        self.items = []
        self.error = ''

    def _req(self, path, data=None, form=False):
        hdr = {'Accept': 'application/json'}
        if form:
            hdr['Content-Type'] = 'application/x-www-form-urlencoded'
        elif self.token:
            hdr['Authorization'] = f'Bearer {self.token}'
        req = urllib.request.Request(f'https://{self.fleet}{path}', data=data, headers=hdr, method='POST' if data else 'GET')
        with urllib.request.urlopen(req, context=self.ctx, timeout=30) as r:
            return json.loads(r.read() or b'null')

    def get(self, path, retried=False):
        if not self.token:
            body = urllib.parse.urlencode({'grant_type': 'password', 'username': 'admin@vsp.local', 'password': self.password}).encode()
            self.token = self._req('/api/v1/identity/token', body, form=True)['access_token']
        try:
            return self._req(path)
        except urllib.error.HTTPError as e:
            if e.code == 401 and not retried:
                self.token = None
                return self.get(path, True)
            raise

    def item(self, t):
        d = self.cache.get(t['id'])
        if d is None or kind(t.get('status')) in ('running', 'waiting') or d.get('status') != t.get('status'):
            try:
                d = self.get(f'/fleet-lcm/v1/tasks/{t["id"]}')
            except Exception:
                d = t
            self.cache[t['id']] = d
        seen, subs = set(), []
        for st in d.get('stages') or []:
            if st.get('id') in seen:
                continue
            seen.add(st.get('id'))
            errs = '\n'.join(m.get('message', '') for m in st.get('messages') or [] if str(m.get('level', '')).upper() == 'ERROR')
            nm = msg_text(st.get('description')) or re.sub(r'[_ ]ref(?:__\d+)?$', '', st.get('name', '')).replace('_', ' ').capitalize()
            subs.append({'name': nm, 'kind': kind(st.get('status')),
                         'start': st.get('startTime'), 'end': st.get('endTime'), 'errors': errs or None})
        errs = '\n'.join(m.get('message', '') for m in d.get('messages') or [] if str(m.get('level', '')).upper() == 'ERROR')
        name = msg_text(d.get('description')) or d.get('name', '').replace('_', ' ').capitalize()
        return {'name': name, 'kind': kind(d.get('status')), 'done': sum(1 for x in subs if x['kind'] == 'done'),
                'total': len(subs), 'start': d.get('startTime') or d.get('createTime'), 'end': d.get('endTime'), 'typical': '',
                'tasks': subs, 'label': name, 'note': None, 'errors': errs or None,
                'message': next((x['name'] for x in subs if x['kind'] == 'running'), '')}

    def read(self):
        r = self.get('/fleet-lcm/v1/tasks?pageSize=25')
        tasks = sorted(r.get('elements') or [], key=lambda x: x.get('startTime') or x.get('createTime') or '', reverse=True)
        return [self.item(t) for t in tasks[:20]]

    def refresh(self):
        try:
            self.items = self.read()
            self.error = ''
        except Exception as e:
            self.token = None
            self.error = str(e)[:120] or e.__class__.__name__
        w = types_ns(vc=self.fleet, items=self.items, error=self.error, interval=self.interval)
        st = vsphere_state(w)
        if not self.items and not self.error:
            st['card']['now'] = ('No VCF Operations lifecycle tasks yet. Start an upgrade or install from VCF Operations '
                                 '(Build > Lifecycle); it shows up here by itself.')
        st['card']['warn'] = ''
        return st

    def detect(self):
        r = self.get('/fleet-lcm/v1/tasks?status=RUNNING&pageSize=5')
        run = r.get('elements') or []
        if run:
            return True, f'Running now: {msg_text(run[0].get("description")) or run[0].get("name")}'
        items = self.read()
        if items:
            i = items[0]
            return False, f'Last: {i["name"]} ({i["kind"]}, {when(i.get("end") or i.get("start"))})'
        return False, 'No tasks yet'


def types_ns(**kw):
    import types
    return types.SimpleNamespace(**kw)


class Scenario:
    def __init__(self, sid, title, desc, watch, sources, tasks_title='Milestones and tasks'):
        self.id, self.title, self.desc = sid, title, desc
        self.make_watch, self.sources, self.tasks_title = watch, sources, tasks_title


def build_scenarios(o):
    replay = dict(x.split('=', 1) for x in (o.log_file or []))
    pw, ssh = o.password, o.ssh_user

    def dm(key, title, short, host):
        return LogSource(key, title, short, host, ssh, pw, DM_DIR, 'domainmanager.log',
                         ['domainmanager.{d}.*.log.gz'], replay.get(key))

    def lcm(key, host):
        return LogSource(key, 'Upgrade log (lifecycle)', 'Lifecycle', host, ssh, pw, LCM_DIR, 'lcm-debug.log',
                         ['lcm.{d}.*.log-debug.gz'], replay.get(key))
    out = [
        Scenario('converge-a', 'Region A converge (VCF Installer)',
                 'Lab 2: vSphere 9.1.1 to VCF 9.1.1 with the VCF Installer.',
                 lambda: ConvergeWatch(o),
                 [dm('installer', 'VCF Installer log', 'Installer', o.installer)]
                 + ([dm('sddcm', 'SDDC Manager log', 'SDDC Mgr', o.sddc_manager)] if o.sddc_manager != 'none' else [])),
    ]
    for sid, region, host in (('sddc-a', 'A', o.sddc_manager if o.sddc_manager != 'none' else SDDC_MANAGER),
                              ('sddc-b', 'B', SDDC_MANAGER_B)):
        out.append(Scenario(
            sid, f'Region {region}: SDDC Manager upgrades and deployments',
            f'Upgrades, Deploy VCF Management Components, imports and configuration updates run by {host.split(".")[0]}.'
            + (' Region B labs 3 and 4.' if region == 'B' else ''),
            (lambda h=host: SddcWatch(o, h)),
            [dm('dm', 'SDDC Manager log (domain manager)', 'Domain mgr', host), lcm('lcm', host)],
            tasks_title='SDDC Manager tasks'))

    for sid, region, fleet, node in (('fleet-a', 'A', 'fleet-01a.site-a.vcf.lab', '10.1.1.142'),
                                     ('fleet-b', 'B', 'fleet-01b.site-b.vcf.lab', '10.2.1.142')):
        out.append(Scenario(
            sid, f'Region {region}: VCF Operations lifecycle (management components)',
            'Upgrades and installs from VCF Operations (Build > Lifecycle): VCF Operations, Automation and the fleet services.',
            (lambda f=fleet: FleetWatch(o, f)),
            [KubeLogSource('fleetupgrade', 'Fleet upgrade service', 'Fleet upg', node, pw, 'vcf-fleet-lcm', 'fleetupgrade'),
             KubeLogSource('fleetbuild', 'Fleet build service', 'Fleet bld', node, pw, 'vcf-fleet-lcm', 'fleetbuild'),
             KubeLogSource('sddcupgrade', 'SDDC lifecycle upgrade service', 'SDDC upg', node, pw, 'vcf-sddc-lcm', 'sddcupgrade'),
             KubeLogSource('sddcbuild', 'SDDC lifecycle build service', 'SDDC bld', node, pw, 'vcf-sddc-lcm', 'sddcbuild')],
            tasks_title='VCF Operations lifecycle tasks'))

    def vclog(key, title, short, vc, directory, current, rotated, drop=None):
        return LogSource(key, title, short, vc, 'root', pw, directory, current, rotated, replay.get(key), ordered=False, drop=drop)
    for sid, vc, user, region in (('vsphere-mgmt-a', 'vc-mgmt-a.site-a.vcf.lab', 'administrator@vsphere.local', 'Region A'),
                                  ('vsphere-mgmt-b', 'vc-mgmt-b.site-b.vcf.lab', 'administrator@vsphere.local', 'Region B'),
                                  ('vsphere-wld01-b', 'vc-wld01-b.site-b.vcf.lab', 'administrator@wld.sso', 'Region B workload domain')):
        out.append(Scenario(
            sid, f'{region}: vSphere upgrades on {vc.split(".")[0]}',
            'vCenter upgrade (including reduced downtime) and ESX remediation with vSphere Lifecycle Manager images.',
            (lambda v=vc, u=user: VsphereWatch(o, v, u)),
            [vclog('vum', 'vSphere Lifecycle Manager log', 'vLCM', vc, '/var/log/vmware/vmware-updatemgr/vum-server',
                   'vmware-vum-server.log', ['vmware-vum-server-*.log.gz', 'pre-rdu-vmware-vum-server.log'],
                   drop=' (DEBUG|VERBOSE|TRIVIA) |InventoryMonitor'),
             vclog('vpxd', 'vCenter tasks (vpxd)', 'vpxd', vc, '/var/log/vmware/vpxd', 'vpxd.log',
                   ['vpxd-[0-9]*.log.gz', 'pre-rdu-vpxd.log'], drop='-- (BEGIN|FINISH) lro-| (verbose|trivia|DEBUG|VERBOSE) '),
             vclog('vlcm', 'vCenter upgrade log (vlcm)', 'vlcm', vc, '/var/log/vmware/vlcm', 'vlcm.log', ['pre-rdu-vlcm.log'])],
            tasks_title='Upgrades and remediations'))
    return out


def load_config():
    import os
    try:
        with open(os.path.expanduser(CONFIG_FILE)) as f:
            return json.load(f)
    except Exception:
        return {}


def save_config(cfg):
    import os
    try:
        path = os.path.expanduser(CONFIG_FILE)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, 'w') as f:
            json.dump(cfg, f)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# The window
# ---------------------------------------------------------------------------

CSS = b'''
.card { background-color: #16294A; border-radius: 8px; padding: 12px 14px; }
.card.failed { background-color: #5A2230; }
.card.done { background-color: #1C4A36; }
.card label { color: #FFFFFF; }
.card .big { font-size: 15pt; font-weight: bold; }
.card .muted { color: #B7C3D4; }
.card .warn { color: #E8D44D; }
.pane-title { font-weight: bold; color: #9FB0C8; }
.legend { padding: 2px 6px; }
.status { color: #9FB0C8; padding: 2px 4px; }
.details { font-family: monospace; }
treeview.view.progressbar { background-image: none; background-color: #34A56F; border-color: #34A56F; color: #FFFFFF; }
treeview.view.trough { background-image: none; background-color: #1F3354; border-color: #1F3354; color: #FFFFFF; }
'''

FILTERS = [('hl', 'Highlights'), ('warn', 'Warnings and errors'), ('err', 'Errors only'), ('all', 'Everything')]


def esc(s):
    return html.escape(str(s), quote=False)


def run_gui(a):
    import gi
    gi.require_version('Gtk', '3.0')
    gi.require_version('Gdk', '3.0')
    from gi.repository import Gdk, Gio, GLib, Gtk, Pango

    GLYPH = {'done': '\u2714', 'running': '\u25B6', 'failed': '\u2716', 'waiting': '\u25CB'}
    KCOL = {'done': SEV_COLOUR['progress'], 'running': SEV_COLOUR['info'], 'failed': SEV_COLOUR['problem'],
            'waiting': SEV_COLOUR['noise']}

    def remeasure(model):
        # After the wrap width changes, make every row work out its height again.
        model.foreach(lambda m, path, it: m.row_changed(path, it) and False)
        return False

    class LogTab:
        """One log: a list of explained lines that can be filtered, searched and followed."""
        # store columns
        SEQ, TIME, TYPE, TEXT, SEV, HL, SEARCH, BG, RAW = range(9)

        def __init__(self, win, key, title, feed, multi=False):
            self.win, self.key, self.title, self.feed, self.multi = win, key, title, feed, multi
            self.rev = 0
            self.iters = {}
            self.entries = {}
            self.follow = True
            self.store = Gtk.ListStore(int, str, str, str, str, bool, str, str, str)
            self.filtered = self.store.filter_new()
            self.filtered.set_visible_func(self.visible)
            tv = self.view = Gtk.TreeView(model=self.filtered)
            tv.set_headers_visible(True)
            tv.set_enable_search(False)
            tv.get_selection().connect('changed', self.on_select)
            tv.connect('key-press-event', self.on_key)
            r = Gtk.CellRendererText(foreground=SEV_COLOUR['noise'], yalign=0.0)
            c = Gtk.TreeViewColumn('Time', r, markup=self.TIME, cell_background=self.BG)
            tv.append_column(c)
            r = Gtk.CellRendererText(yalign=0.0)
            c = Gtk.TreeViewColumn('Type', r, markup=self.TYPE, cell_background=self.BG)
            tv.append_column(c)
            self.text_r = Gtk.CellRendererText(yalign=0.0, wrap_mode=Pango.WrapMode.WORD_CHAR, wrap_width=300)
            self.text_c = Gtk.TreeViewColumn('What it means', self.text_r, markup=self.TEXT, cell_background=self.BG)
            self.text_c.set_expand(True)
            tv.append_column(self.text_c)
            self.scroll = Gtk.ScrolledWindow()
            # EXTERNAL: the list gets the width of the pane and the text wraps to fit it.
            self.scroll.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)
            self.scroll.add(tv)
            self.scroll.connect('size-allocate', self.on_size)
            self.adj = self.scroll.get_vadjustment()
            self.adj.connect('value-changed', self.on_scrolled)
            self.adj.connect('changed', self.on_adj_changed)
            self.page = self.scroll
            self.tab_label = Gtk.Label()
            self.update_tab_label()

        def reset(self, feed):
            self.store.clear()
            self.iters, self.entries, self.rev, self.feed = {}, {}, 0, feed
            self.pull()

        def update_tab_label(self):
            if self.multi:
                self.tab_label.set_markup(f'<span foreground="{SEV_COLOUR["info"]}">\u25C6</span>  {esc(self.title)}')
                return
            dot = SEV_COLOUR['progress'] if self.feed.connected else SEV_COLOUR['watch']
            self.tab_label.set_markup(f'<span foreground="{dot}">\u25CF</span>  {esc(self.title)}')

        def on_size(self, sw, alloc):
            other = sum(c.get_width() for c in self.view.get_columns() if c is not self.text_c)
            w = max(200, alloc.width - other - 30)
            if abs(self.text_r.get_property('wrap-width') - w) > 8:
                self.text_r.set_property('wrap-width', w)
                GLib.idle_add(remeasure, self.filtered)

        def at_bottom(self):
            return self.adj.get_value() >= self.adj.get_upper() - self.adj.get_page_size() - 40

        def on_scrolled(self, adj):
            self.follow = self.at_bottom()
            self.win.update_jump()

        def on_adj_changed(self, adj):
            if self.follow:
                adj.set_value(adj.get_upper() - adj.get_page_size())

        def jump(self):
            self.follow = True
            self.adj.set_value(self.adj.get_upper() - self.adj.get_page_size())
            self.win.update_jump()

        def visible(self, model, it, data=None):
            sev, hl = model[it][self.SEV], model[it][self.HL]
            f = self.win.filter_id
            if f == 'hl' and not hl:
                return False
            if f == 'warn' and sev not in ('watch', 'problem', 'benign'):
                return False
            if f == 'err' and sev != 'problem':
                return False
            q = self.win.query
            return not q or q in model[it][self.SEARCH]

        def row(self, e):
            col = SEV_COLOUR[e['sev']]
            typ = f'<span foreground="{col}"><b>{SEV_LABEL[e["sev"]]}</b></span>'
            text = f'<span foreground="{col}">{esc(e["text"])}</span>'
            if e['count'] > 1:
                text += f'  <span foreground="{SEV_COLOUR["noise"]}">\u00D7{e["count"]}, last at {local_time(e["last"])}</span>'
            js = has_json(e['raw'])
            if js:
                text += f'  <span foreground="{SEV_COLOUR["info"]}" size="smaller"><b>{{ }} JSON</b></span>'
            if self.win.show_raw:
                raw = e['raw'] if len(e['raw']) < 400 else e['raw'][:400] + ' ...'
                text += f'\n<span foreground="{SEV_COLOUR["noise"]}" size="smaller">{esc(e["level"])} {esc(e["cls"])}: {esc(raw)}</span>'
            search = (e['text'] + ' ' + e['raw'][:3000] + ' ' + e['cls'] + (' json' if js else '')).lower()
            tm = esc(local_time(e['ts']))
            if self.multi:
                tm += f'\n<span size="smaller" foreground="{SEV_COLOUR["noise"]}">{esc(self.win.src_short.get(e.get("src"), ""))}</span>'
            return [e['seq'], tm, typ, text, e['sev'], e['hl'], search,
                    SEV_ROW_BG.get(e['sev'], 'rgba(0,0,0,0)'), e['raw']]

        def pull(self):
            entries, first, rev = self.feed.since(self.rev)
            self.rev = rev
            for e in entries:
                self.entries[e['seq']] = e
                vals = self.row(e)
                it = self.iters.get(e['seq'])
                if it is not None:
                    self.store.set(it, list(range(len(vals))), vals)
                else:
                    self.iters[e['seq']] = self.store.append(vals)
            # Drop rows the feed no longer keeps.
            while self.iters and min(self.iters) < first:
                s = min(self.iters)
                self.store.remove(self.iters.pop(s))
                self.entries.pop(s, None)
            self.update_tab_label()
            return bool(entries)

        def redraw(self):
            for seq, it in self.iters.items():
                vals = self.row(self.entries[seq])
                self.store.set(it, list(range(len(vals))), vals)

        def on_key(self, w, ev):
            if ev.state & Gdk.ModifierType.CONTROL_MASK and Gdk.keyval_to_lower(ev.keyval) == Gdk.KEY_c:
                self.win.copy_line()
                return True
            return False

        def on_select(self, sel):
            model, it = sel.get_selected()
            if it is None:
                return
            e = self.entries.get(model[it][self.SEQ])
            if e:
                self.win.show_details(e)

    class MonitorWindow(Gtk.ApplicationWindow):
        def __init__(self, app, opts, scenario, size=None):
            super().__init__(application=app, title=f'VCF Upgrade Monitor: {scenario.title}')
            self.app, self.opts, self.scenario = app, opts, scenario
            self.stop = threading.Event()
            feeds = collections.OrderedDict((src.key, LogFeed(src.title)) for src in scenario.sources)
            self.src_short = {src.key: src.short for src in scenario.sources}
            self.filter_id = opts.filter
            self.query = ''
            self.show_raw = False
            self.state_sig = None
            self.task_iters = {}
            self.ms_kinds = {}
            self.feeds = feeds
            self.step_tab = None
            self.step_gen = 0
            self.step_req = None

            # Size: most of the screen, whatever its resolution. It is a normal window: move, resize or maximise it.
            try:
                disp = Gdk.Display.get_default()
                mon = disp.get_primary_monitor() or disp.get_monitor(0)
                wa = mon.get_workarea()
                W, H = int(wa.width * 0.8), int(wa.height * 0.85)   # bigger than this and Ubuntu maximises it
            except Exception:
                W, H = 1180, 720
            if size:
                W, H = size
            self.set_default_size(W, H)

            hosts = list(dict.fromkeys(src.host for src in scenario.sources))
            hb = Gtk.HeaderBar(show_close_button=True, title=scenario.title, subtitle='  and  '.join(hosts))
            self.set_titlebar(hb)
            pick = Gtk.Button(label='Change scenario...')
            pick.set_tooltip_text('Watch something else: the converge, SDDC Manager upgrades in Region A or B, ...')
            pick.connect('clicked', lambda b: self.app.show_picker(self))
            hb.pack_start(pick)
            self.search = Gtk.SearchEntry(placeholder_text='Search the log')
            self.search.set_width_chars(22)
            self.search.connect('search-changed', self.on_search)
            hb.pack_end(self.search)

            paned = Gtk.Paned(orientation=Gtk.Orientation.HORIZONTAL, wide_handle=True)
            paned.set_position(int(W * 0.45))
            self.add(paned)

            # ---- left: tasks
            left = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin=10)
            card = self.card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            card.get_style_context().add_class('card')
            self.big = Gtk.Label(xalign=0, wrap=True)
            self.big.get_style_context().add_class('big')
            self.now = Gtk.Label(xalign=0, wrap=True)
            self.warn = Gtk.Label(xalign=0, wrap=True)
            self.warn.get_style_context().add_class('warn')
            self.meta = Gtk.Label(xalign=0, wrap=True)
            self.meta.get_style_context().add_class('muted')
            for w in (self.big, self.now, self.warn, self.meta):
                w.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
                w.set_max_width_chars(30)   # wrap to the pane, not the other way round
                card.pack_start(w, False, False, 0)
            left.pack_start(card, False, False, 0)
            t = Gtk.Label(xalign=0)
            t.set_markup(f'{esc(scenario.tasks_title.upper())}   <span weight="normal" size="smaller">Click one to see its log lines</span>')
            t.get_style_context().add_class('pane-title')
            left.pack_start(t, False, False, 0)
            # glyph, name, pct, pct text, show pct, time, errors, start, end, kind, label
            self.tstore = Gtk.TreeStore(str, str, int, str, bool, str, str, str, str, str, str)
            tv = self.tview = Gtk.TreeView(model=self.tstore, headers_visible=False)
            tv.set_tooltip_column(6)
            tv.set_enable_search(False)
            tv.get_selection().connect('changed', self.on_tree_select)
            tv.append_column(Gtk.TreeViewColumn('', Gtk.CellRendererText(yalign=0.0), markup=0))
            self.tname_r = Gtk.CellRendererText(wrap_mode=Pango.WrapMode.WORD_CHAR, wrap_width=200, yalign=0.0)
            self.tname_c = Gtk.TreeViewColumn('Step', self.tname_r, markup=1)
            self.tname_c.set_expand(True)
            tv.append_column(self.tname_c)
            pr = Gtk.CellRendererProgress(yalign=0.0)
            pc = Gtk.TreeViewColumn('Progress', pr, value=2, text=3, visible=4)
            pc.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            pc.set_fixed_width(80)
            tv.append_column(pc)
            tc = Gtk.TreeViewColumn('Time', Gtk.CellRendererText(xalign=1.0, yalign=0.0), markup=5)
            tc.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
            tc.set_fixed_width(92)
            tv.append_column(tc)
            ts = Gtk.ScrolledWindow(vexpand=True)
            ts.set_policy(Gtk.PolicyType.EXTERNAL, Gtk.PolicyType.AUTOMATIC)   # names wrap to fit the pane
            ts.add(tv)
            ts.connect('size-allocate', self.on_task_size)
            left.pack_start(ts, True, True, 0)
            paned.pack1(left, resize=False, shrink=False)

            # ---- right: logs
            right = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin=10)
            legend = Gtk.Box(spacing=14)
            for s in ('progress', 'watch', 'benign', 'problem', 'info', 'noise'):
                lab = Gtk.Label()
                lab.set_markup(f'<span foreground="{SEV_COLOUR[s]}">\u25A0</span> <b>{SEV_LABEL[s]}</b>')
                lab.set_tooltip_text(SEV_HELP[s])
                lab.get_style_context().add_class('legend')
                legend.pack_start(lab, False, False, 0)
            right.pack_start(legend, False, False, 0)
            # Filter: a row of buttons (one click, no drop-down menu), then "Original lines".
            frow = Gtk.Box(spacing=10)
            lab = Gtk.Label(label='Show:')
            lab.get_style_context().add_class('pane-title')
            frow.pack_start(lab, False, False, 0)
            seg = Gtk.Box()
            seg.get_style_context().add_class('linked')
            group = None
            tips = {'hl': 'Everything except the grey routine lines', 'warn': 'Orange, yellow and red lines',
                    'err': 'Red lines only', 'all': 'Every line, including routine housekeeping'}
            for fid, name in FILTERS:
                b = Gtk.RadioButton.new_with_label_from_widget(group, name)
                group = group or b
                b.set_mode(False)            # look like a button, not a radio circle
                b.set_tooltip_text(tips[fid])
                b.set_active(fid == self.filter_id)
                b.connect('toggled', self.on_filter, fid)
                seg.pack_start(b, False, False, 0)
            frow.pack_start(seg, False, False, 0)
            self.raw_btn = Gtk.ToggleButton(label='Original lines')
            self.raw_btn.set_tooltip_text('Also show the original log line under each explanation')
            self.raw_btn.connect('toggled', self.on_raw)
            frow.pack_end(self.raw_btn, False, False, 0)
            right.pack_start(frow, False, False, 0)
            self.nb = Gtk.Notebook()
            self.tabs = []
            for key, feed in feeds.items():
                tab = LogTab(self, key, feed.name, feed)
                self.tabs.append(tab)
                self.nb.append_page(tab.scroll, tab.tab_label)
            self.nb.connect('switch-page', lambda nb, page, n: GLib.idle_add(self.update_status))
            vp = Gtk.Paned(orientation=Gtk.Orientation.VERTICAL, wide_handle=True)
            vp.pack1(self.nb, resize=True, shrink=False)
            detbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            dbar = Gtk.Box(spacing=6)
            self.json_note = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            self.json_note.get_style_context().add_class('status')
            dbar.pack_start(self.json_note, True, True, 0)
            self.save_btn = Gtk.Button(label='Save JSON...')
            self.save_btn.set_tooltip_text('Save the formatted JSON to a file')
            self.save_btn.connect('clicked', self.on_save_json)
            dbar.pack_end(self.save_btn, False, False, 0)
            self.line_btn = Gtk.Button(label='Copy line')
            self.line_btn.set_tooltip_text('Copy the selected log line: the explanation and the original line (or press Ctrl+C)')
            self.line_btn.connect('clicked', lambda b: self.copy_line())
            self.copy_btn = Gtk.Button(label='Copy JSON')
            self.copy_btn.set_tooltip_text('Copy the formatted JSON, ready to paste into a script or a spec file')
            self.copy_btn.connect('clicked', self.on_copy_json)
            dbar.pack_end(self.copy_btn, False, False, 0)
            dbar.pack_end(self.line_btn, False, False, 0)
            detbox.pack_start(dbar, False, False, 0)
            det = Gtk.ScrolledWindow(vexpand=True)
            self.details = Gtk.TextView(editable=False, cursor_visible=False, wrap_mode=Gtk.WrapMode.WORD_CHAR,
                                        left_margin=6, right_margin=6, top_margin=4)
            self.details.get_style_context().add_class('details')
            buf = self.details.get_buffer()
            buf.create_tag('head', weight=Pango.Weight.BOLD, foreground='#9FB0C8')
            buf.create_tag('key', foreground=SEV_COLOUR['info'])
            buf.create_tag('str', foreground=SEV_COLOUR['progress'])
            buf.create_tag('num', foreground=SEV_COLOUR['watch'])
            buf.create_tag('mask', foreground=SEV_COLOUR['benign'], weight=Pango.Weight.BOLD)
            buf.create_tag('note', foreground=SEV_COLOUR['benign'], family='sans', wrap_mode=Gtk.WrapMode.WORD)
            buf.set_text('Click a log line to see the original line from the log (and any stack trace) here. '
                         'Lines marked { } JSON also show the JSON in them, formatted, ready to copy.')
            det.add(self.details)
            detbox.pack_start(det, True, True, 0)
            self.json_text = ''
            self.json_name = ''
            self.sel_entry = None
            vp.pack2(detbox, resize=False, shrink=True)
            vp.set_position(int(H * 0.5))
            right.pack_start(vp, True, True, 0)
            bar = Gtk.Box(spacing=8)
            self.status = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END)
            self.status.get_style_context().add_class('status')
            bar.pack_start(self.status, True, True, 0)
            self.jump_btn = Gtk.Button(label='Jump to newest')
            self.jump_btn.connect('clicked', lambda b: self.cur_tab().jump())
            self.jump_btn.set_no_show_all(True)
            bar.pack_end(self.jump_btn, False, False, 0)
            right.pack_start(bar, False, False, 0)
            paned.pack2(right, resize=True, shrink=False)

            # Keyboard: Ctrl+F search, Ctrl+Q quit.
            acc = Gtk.AccelGroup()
            self.add_accel_group(acc)
            acc.connect(Gdk.keyval_from_name('f'), Gdk.ModifierType.CONTROL_MASK, 0, lambda *x: self.search.grab_focus() or True)
            acc.connect(Gdk.keyval_from_name('q'), Gdk.ModifierType.CONTROL_MASK, 0, lambda *x: self.close() or True)

            self.show_all()
            self.set_json('', '')
            keys = [t.key for t in self.tabs]
            if opts.tab in keys:
                self.nb.set_current_page(keys.index(opts.tab))
            self.warn.hide()
            self.show_state({'items': [], 'group': None, 'card': {'big': scenario.title, 'now': 'Reading the task list...',
                                                                    'warn': '', 'meta': '', 'cls': ''}})
            GLib.timeout_add(1000, self.pull_logs)
            # Start reading: one thread per log, one for the task list. All stop when the window closes.
            for src in scenario.sources:
                threading.Thread(target=tail_worker, args=(feeds[src.key], src, opts.backlog, self.stop), daemon=True).start()
            watch = scenario.make_watch()

            def poll():
                while not self.stop.is_set():
                    try:
                        st = watch.refresh()
                    except Exception as e:   # keep going; show the problem on the card
                        st = {'items': None, 'card': {'big': 'Cannot read the task list', 'now': str(e)[:200],
                                                      'warn': '', 'meta': '', 'cls': 'failed'}}
                    GLib.idle_add(self.show_state, st)
                    self.stop.wait(watch.interval)
            threading.Thread(target=poll, daemon=True).start()
            self.connect('destroy', lambda w: self.stop.set())

        # -- helpers
        def cur_tab(self):
            page = self.nb.get_nth_page(max(0, self.nb.get_current_page()))
            return next((t for t in self.all_tabs() if t.page is page), self.tabs[0])

        def all_tabs(self):
            return self.tabs + ([self.step_tab] if self.step_tab else [])

        def update_jump(self):
            self.jump_btn.set_visible(not self.cur_tab().follow)

        def update_status(self):
            t = self.cur_tab()
            dot = SEV_COLOUR['progress'] if t.feed.connected or t.multi else SEV_COLOUR['watch']
            shown = t.filtered.iter_n_children(None)
            self.status.set_markup(f'<span foreground="{dot}">\u25CF</span> {esc(t.feed.status)}   '
                                   f'<span foreground="{SEV_COLOUR["noise"]}">{shown} of {len(t.iters)} lines shown</span>')
            self.update_jump()
            return False

        def pull_logs(self):
            for t in self.tabs:
                t.pull()
            self.update_status()
            return True

        def refilter(self):
            for t in self.all_tabs():
                t.filtered.refilter()
                if t.follow:
                    GLib.idle_add(t.jump)
            self.update_status()

        def on_filter(self, btn, fid):
            if btn.get_active() and self.filter_id != fid:
                self.filter_id = fid
                self.refilter()

        def on_search(self, entry):
            self.query = entry.get_text().strip().lower()
            self.refilter()

        def on_raw(self, btn):
            self.show_raw = btn.get_active()
            for t in self.all_tabs():
                t.redraw()

        def on_task_size(self, sw, alloc):
            other = sum(c.get_width() for c in self.tview.get_columns() if c is not self.tname_c)
            w = max(120, alloc.width - other - 70)   # 70: expander arrow and the indent of the task rows
            if abs(self.tname_r.get_property('wrap-width') - w) > 8:
                self.tname_r.set_property('wrap-width', w)
                GLib.idle_add(remeasure, self.tstore)

        def clip(self, text):
            cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            cb.set_text(text, -1)
            cb.store()

        def copy_line(self):
            e = self.sel_entry
            if not e:
                self.json_note.set_markup(f'<span foreground="{SEV_COLOUR["watch"]}">Click a log line first.</span>')
                return
            parts = [f'{local_time(e["ts"])} {SEV_LABEL[e["sev"]]}: {e["text"]}', '',
                     f'{e["ts"]} {e["level"]} {e["cls"]}: {e["raw"]}'] + e['more']
            if self.json_text:
                parts += ['', self.json_text]
            self.clip('\n'.join(parts))
            self.json_note.set_markup(f'<span foreground="{SEV_COLOUR["progress"]}">Copied the line. Paste it with Ctrl+V.</span>')

        def show_details(self, e):
            self.sel_entry = e
            buf = self.details.get_buffer()
            buf.set_text('')
            it = buf.get_end_iter()
            head = f'{local_time(e["ts"])}  {e["level"]}  {e["cls"]}   ({SEV_LABEL[e["sev"]]})'
            if e['count'] > 1:
                head += f'   seen {e["count"]} times, last at {local_time(e["last"])}'
            buf.insert_with_tags_by_name(it, head + '\n\n', 'head')
            objs = find_json(e['raw']) or (find_json('\n'.join(e['more'])) if e['more'] else []) \
                or (find_json(e['raw'] + '\n'.join(e['more'])) if e['more'] else [])
            pretty = ''
            text = e['text']
            if objs:
                pretty = '\n\n'.join(json.dumps(o, indent=2, ensure_ascii=False) for o in objs)
                buf.insert_with_tags_by_name(buf.get_end_iter(), 'JSON in this line, formatted' +
                                             (f' ({len(objs)} blocks)' if len(objs) > 1 else '') + '\n', 'head')
                buf.insert_with_tags_by_name(buf.get_end_iter(), json_note(objs) + '\n\n', 'note')
                self.insert_json(buf, pretty)
                buf.insert(buf.get_end_iter(), '\n\n')
                if len(text) > 200:
                    text = text[:200] + ' ...'
            buf.insert_with_tags_by_name(buf.get_end_iter(), 'In plain English\n', 'head')
            buf.insert(buf.get_end_iter(), text + '\n\n')
            buf.insert_with_tags_by_name(buf.get_end_iter(), 'Original log line\n', 'head')
            raw = e['raw'] if len(e['raw']) < 20000 else e['raw'][:20000] + ' ...'
            buf.insert(buf.get_end_iter(), raw + '\n')
            if e['more']:
                buf.insert_with_tags_by_name(buf.get_end_iter(), '\nLines after it (stack trace or data)\n', 'head')
                buf.insert(buf.get_end_iter(), '\n'.join(e['more']))
            self.details.scroll_to_iter(buf.get_start_iter(), 0, False, 0, 0)
            name = re.sub(r'[^A-Za-z0-9]+', '-', words(e['cls'])).strip('-').lower() or 'log'
            stamp = (parse_ts(e['ts']) or datetime.datetime.now(datetime.timezone.utc)).astimezone().strftime('%Y%m%d-%H%M%S')
            self.set_json(pretty, f'{name}-{stamp}.json')

        def insert_json(self, buf, text):
            # Colour keys, strings and numbers; masked passwords (*****) stand out.
            tok = re.compile(r'("(?:[^"\\]|\\.)*")(\s*:)?|(\b-?\d+(?:\.\d+)?\b|\btrue\b|\bfalse\b|\bnull\b)')
            pos = 0
            for m in tok.finditer(text):
                buf.insert(buf.get_end_iter(), text[pos:m.start()])
                if m.group(1):
                    tag = 'key' if m.group(2) else ('mask' if set(m.group(1)[1:-1]) == {'*'} else 'str')
                    buf.insert_with_tags_by_name(buf.get_end_iter(), m.group(1), tag)
                    if m.group(2):
                        buf.insert(buf.get_end_iter(), m.group(2))
                else:
                    buf.insert_with_tags_by_name(buf.get_end_iter(), m.group(3), 'num')
                pos = m.end()
            buf.insert(buf.get_end_iter(), text[pos:])

        def set_json(self, text, name):
            self.json_text, self.json_name = text, name
            self.copy_btn.set_sensitive(bool(text))
            self.save_btn.set_sensitive(bool(text))
            if text:
                masked = '*****' in text
                self.json_note.set_markup(f'<span foreground="{SEV_COLOUR["info"]}">{{ }} This line has JSON in it.</span>'
                                          + (f' <span foreground="{SEV_COLOUR["benign"]}">Passwords show as ***** in the log: '
                                             'put real values in before you use it.</span>' if masked else ''))
                self.json_note.set_tooltip_text('Passwords are masked as ***** in the log. Replace them with real values '
                                                'before you use this as input.' if masked else None)
            else:
                self.json_note.set_markup(f'<span foreground="{SEV_COLOUR["noise"]}">Details of the selected line</span>')

        def on_copy_json(self, btn):
            if not self.json_text:
                return
            self.clip(self.json_text)
            self.json_note.set_markup(f'<span foreground="{SEV_COLOUR["progress"]}">Copied the JSON '
                                      f'({len(self.json_text.splitlines())} lines). Paste it with Ctrl+V.</span>')

        def on_save_json(self, btn):
            if not self.json_text:
                return
            dlg = Gtk.FileChooserDialog(title='Save the JSON', parent=self, action=Gtk.FileChooserAction.SAVE)
            dlg.add_buttons('Cancel', Gtk.ResponseType.CANCEL, 'Save', Gtk.ResponseType.OK)
            dlg.set_do_overwrite_confirmation(True)
            import os
            dlg.set_current_folder(os.path.expanduser('~'))
            dlg.set_current_name(self.json_name or 'log.json')
            if dlg.run() == Gtk.ResponseType.OK:
                path = dlg.get_filename()
                try:
                    with open(path, 'w') as f:
                        f.write(self.json_text + '\n')
                    self.json_note.set_markup(f'<span foreground="{SEV_COLOUR["progress"]}">Saved to {esc(path)}</span>')
                except OSError as ex:
                    self.json_note.set_markup(f'<span foreground="{SEV_COLOUR["problem"]}">Could not save: {esc(ex)}</span>')
            dlg.destroy()

        # -- the log lines of one milestone or task
        def on_tree_select(self, sel):
            model, it = sel.get_selected()
            if it is None:
                return
            start, end, k, label = model[it][7], model[it][8], model[it][9], model[it][10]
            if k == 'group':
                return
            if not start:
                self.details.get_buffer().set_text(f'{label}\n\nThis step has not started yet, so there are no log lines for it.')
                return
            self.show_step(label, start, end if k in ('done', 'failed') and end else None)

        def ensure_step_tab(self):
            if self.step_tab:
                return self.step_tab
            t = self.step_tab = LogTab(self, 'step', 'Selected step', LogFeed('Selected step', keep=10 ** 6), multi=True)
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            head = Gtk.Box(spacing=8, margin=4)
            self.step_label = Gtk.Label(xalign=0, wrap=True)
            self.step_label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
            self.step_label.set_max_width_chars(40)
            head.pack_start(self.step_label, True, True, 0)
            self.step_spin = Gtk.Spinner()
            head.pack_end(self.step_spin, False, False, 0)
            self.step_refresh = Gtk.Button(label='Refresh')
            self.step_refresh.set_tooltip_text('Read the log lines again (useful while the step is still running)')
            self.step_refresh.connect('clicked', lambda b: self.step_req and self.show_step(*self.step_req))
            head.pack_end(self.step_refresh, False, False, 0)
            box.pack_start(head, False, False, 0)
            box.pack_start(t.scroll, True, True, 0)
            t.page = box
            box.show_all()
            self.nb.append_page(box, t.tab_label)
            return t

        def show_step(self, label, start, end):
            t = self.ensure_step_tab()
            self.step_req = (label, start, end)
            self.step_gen += 1
            gen = self.step_gen
            # A little either side, so the lines that start and finish the step are included.
            a = (parse_ts(start) - datetime.timedelta(seconds=30)).strftime('%Y-%m-%dT%H:%M:%S')
            b_dt = parse_ts(end) + datetime.timedelta(seconds=30) if end else datetime.datetime.now(datetime.timezone.utc)
            b = b_dt.strftime('%Y-%m-%dT%H:%M:%S.999Z')
            span = f'{local_time(a + "Z")} to {local_time(b) if end else "now"} (the step, plus 30 seconds either side)'
            self.step_label.set_markup(f'<b>{esc(label)}</b>\n<span foreground="{SEV_COLOUR["noise"]}">Reading the logs from {span}...</span>')
            self.step_spin.start()
            self.nb.set_current_page(self.nb.page_num(t.page))
            def work():
                records, notes, cut = [], [], False
                for src in self.scenario.sources:
                    try:
                        lines, trunc = fetch_window(src, a, b)
                    except Exception as e:
                        notes.append(f'{src.title} not read ({str(e)[:80] or e.__class__.__name__})')
                        continue
                    cut = cut or trunc
                    if not lines and src.history_note:
                        notes.append(f'{src.title}: no lines. {src.history_note}')
                    cur = None
                    for ln in lines:
                        m = parse_line(ln)
                        if m:
                            cur = [m[0], src.key, [ln]]
                            records.append(cur)
                        elif cur and len(cur[2]) < 41:
                            cur[2].append(ln)
                records.sort(key=lambda r: r[0])
                feed = LogFeed('Selected step', keep=10 ** 6)
                for ts, src, lines in records:
                    for ln in lines:
                        feed.feed(ln, src=src)
                GLib.idle_add(done, feed, len(records), notes, cut)

            def done(feed, n, notes, cut):
                if gen != self.step_gen:
                    return False
                self.step_spin.stop()
                feed.status = f'log lines for: {label}'
                t.reset(feed)
                extra = ''.join(f'\n{esc(x)}' for x in notes)
                if cut:
                    extra += '\nVery long step: only the first 200,000 lines of each log were read.'
                self.step_label.set_markup(f'<b>{esc(label)}</b>\n<span foreground="{SEV_COLOUR["noise"]}">'
                                           f'{n} log lines from {span}, '
                                           + ('both logs merged in time order. ' if len(self.scenario.sources) > 1 else '') +
                                           f'The filter and search apply here too.{extra}</span>')
                t.follow = False
                GLib.idle_add(lambda: t.adj.set_value(0) or False)
                self.update_status()
                return False

            threading.Thread(target=work, daemon=True).start()

        # -- task list
        def show_state(self, st):
            c = st['card']
            ctx = self.card.get_style_context()
            for k in ('failed', 'done'):
                ctx.remove_class(k)
            if c.get('cls'):
                ctx.add_class(c['cls'])
            self.big.set_text(c.get('big', ''))
            self.now.set_text(c.get('now', ''))
            self.meta.set_text(c.get('meta', ''))
            self.warn.set_text(c.get('warn', ''))
            self.warn.set_visible(bool(c.get('warn')))
            if st.get('items') is None:      # a refresh failed: keep the list as it was
                return False
            if not st['items'] and not st.get('group'):
                if len(self.tstore):
                    self.tstore.clear()
                self.state_sig = None
                return False
            self.fill_tasks(st['items'], st.get('group'), st.get('numbered', True))
            return False

        def task_vals(self, t):
            col = KCOL[t['kind']]
            name = esc(t['name'] or '')
            if t['kind'] == 'waiting':
                name = f'<span foreground="{SEV_COLOUR["noise"]}">{name}</span>'
            elif t['kind'] == 'failed':
                name = f'<span foreground="{col}"><b>{name}</b></span>'
            elif t['kind'] == 'running':
                name = f'<b>{name}</b>'
            if t['kind'] == 'waiting' or not t.get('start'):
                tm = ''
            elif t['kind'] == 'running':
                tm = dur(t['start'])
            else:
                tm = dur(t['start'], t['end']) if t.get('end') else ''
            return [f'<span foreground="{col}">{GLYPH[t["kind"]]}</span>', name, 0, '', False,
                    f'<span foreground="{SEV_COLOUR["noise"]}">{esc(tm)}</span>', tip_text(t),
                    t.get('start') or '', t.get('end') or '', t['kind'], t['name'] or '']

        def ms_vals(self, i, m, numbered=True):
            col = SEV_COLOUR['benign'] if m.get('note') else KCOL[m['kind']]
            pct = int(round(100 * m['done'] / m['total'])) if m['total'] else 0
            if m['kind'] == 'running':
                tm = f'{dur(m["start"])}' + (f'\nusually {m["typical"]}' if m['typical'] else '')
            elif m['kind'] == 'waiting':
                tm = f'usually {m["typical"]}' if m['typical'] else ''
            else:
                tm = dur(m['start'], m['end']) if m.get('end') else ''
            num = f'{i + 1}. ' if numbered else ''
            name = f'<b>{num}{esc(m["name"])}</b>'
            if m['kind'] == 'waiting':
                name = f'<span foreground="{SEV_COLOUR["noise"]}">{num}{esc(m["name"])}</span>'
            elif m['kind'] == 'failed' and not m.get('note'):
                name = f'<span foreground="{col}"><b>{num}{esc(m["name"])}</b></span>'
            if not numbered and m.get('start'):
                name += f'\n<span size="smaller" foreground="{SEV_COLOUR["noise"]}">{when(m["start"])}</span>'
            return [f'<span foreground="{col}" size="large">{GLYPH[m["kind"]]}</span>', name, pct,
                    f'{m["done"]}/{m["total"]}', bool(m['total']), f'<span foreground="{SEV_COLOUR["noise"]}">{esc(tm)}</span>',
                    tip_text(m), m.get('start') or '', m.get('end') or '', m['kind'], m.get('label') or m['name']]

        def group_vals(self, group):
            tasks = group['items']
            busy = any(t['kind'] == 'running' for t in tasks)
            failed = any(t['kind'] == 'failed' for t in tasks)
            k = 'running' if busy else 'done'
            name = (f'<b>{esc(group["title"])}</b>\n<span size="smaller" foreground="{SEV_COLOUR["noise"]}">'
                    f'{esc(group.get("subtitle", ""))}' + ('; some failed: hover for the error' if failed else '') + '</span>')
            return [f'<span foreground="{KCOL[k]}" size="large">\u25A3</span>', name, 0, '', False, '', '',
                    '', '', 'group', '']

        def fill_tasks(self, ms, group=None, numbered=True):
            sd = group['items'] if group else []
            sig = (tuple((m['name'], m.get('start'), len(m['tasks'])) for m in ms),
                   tuple((t['name'], t.get('start'), len(t['tasks'])) for t in sd))
            cols = list(range(11))
            if sig != self.state_sig:
                self.tstore.clear()
                self.task_iters = {}
                self.ms_kinds = {}
                for i, m in enumerate(ms):
                    mi = self.tstore.append(None, self.ms_vals(i, m, numbered))
                    self.task_iters[('m', i)] = mi
                    for j, t in enumerate(m['tasks']):
                        self.task_iters[('t', i, j)] = self.tstore.append(mi, self.task_vals(t))
                if sd:
                    g = self.task_iters['g'] = self.tstore.append(None, self.group_vals(group))
                    for i, t in enumerate(sd):
                        ti = self.task_iters[('s', i)] = self.tstore.append(g, self.ms_vals(i, t, False))
                        for j, x in enumerate(t['tasks']):
                            self.task_iters[('ss', i, j)] = self.tstore.append(ti, self.task_vals(x))
                    self.tview.expand_row(self.tstore.get_path(g), False)
                self.state_sig = sig
            else:
                for i, m in enumerate(ms):
                    self.tstore.set(self.task_iters[('m', i)], cols, self.ms_vals(i, m, numbered))
                    for j, t in enumerate(m['tasks']):
                        self.tstore.set(self.task_iters[('t', i, j)], cols, self.task_vals(t))
                if sd:
                    self.tstore.set(self.task_iters['g'], cols, self.group_vals(group))
                    for i, t in enumerate(sd):
                        self.tstore.set(self.task_iters[('s', i)], cols, self.ms_vals(i, t, False))
                        for j, x in enumerate(t['tasks']):
                            self.tstore.set(self.task_iters[('ss', i, j)], cols, self.task_vals(x))
            # Open the running and failed rows when they get there; the rest stay as you left them.
            for i, m in enumerate(ms):
                if self.ms_kinds.get(i) != m['kind']:
                    path = self.tstore.get_path(self.task_iters[('m', i)])
                    if m['kind'] in ('running', 'failed') and m['tasks'] and not m.get('note'):
                        self.tview.expand_row(path, False)
                    elif m['kind'] == 'done' and self.ms_kinds.get(i) is not None:
                        self.tview.collapse_row(path)
                    self.ms_kinds[i] = m['kind']

    class Picker(Gtk.Window):
        """Choose what to watch. Checks each scenario in the background and suggests the one that is active."""

        def __init__(self, app, scenarios, current, on_choose, first):
            super().__init__(application=app, title='VCF Upgrade Monitor: what do you want to watch?')
            self.set_default_size(680, -1)
            self.set_position(Gtk.WindowPosition.CENTER)
            self.on_choose, self.scenarios = on_choose, scenarios
            self.touched = False
            self.auto = True
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin=16)
            head = Gtk.Label(xalign=0, wrap=True)
            head.set_max_width_chars(70)
            head.set_markup('<span size="large"><b>What do you want to watch?</b></span>\n'
                            '<span foreground="#9FB0C8">Pick the part of the lab you are doing. The monitor checks what is '
                            'running and selects it for you. You can change it later with "Change scenario...".</span>')
            box.pack_start(head, False, False, 0)
            sw = Gtk.ScrolledWindow()
            sw.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            sw.set_propagate_natural_height(True)
            try:
                disp = Gdk.Display.get_default()
                wa = (disp.get_primary_monitor() or disp.get_monitor(0)).get_workarea()
                sw.set_max_content_height(int(wa.height * 0.62))
            except Exception:
                sw.set_max_content_height(480)
            lst = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
            sw.add(lst)
            box.pack_start(sw, True, True, 0)
            self.radios, self.status = {}, {}
            group = None
            for sc in scenarios:
                rb = Gtk.RadioButton.new_from_widget(group)
                group = group or rb
                v = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, margin_start=6)
                lab = Gtk.Label(xalign=0, wrap=True)
                lab.set_max_width_chars(70)
                lab.set_markup(f'<b>{esc(sc.title)}</b>\n<span size="smaller" foreground="#B7C3D4">{esc(sc.desc)}</span>')
                st = Gtk.Label(xalign=0)
                st.set_markup(f'<span size="smaller" foreground="{SEV_COLOUR["noise"]}">Checking...</span>')
                v.pack_start(lab, False, False, 0)
                v.pack_start(st, False, False, 0)
                rb.add(v)
                rb.connect('toggled', self.on_toggle)
                lst.pack_start(rb, False, False, 4)
                self.radios[sc.id], self.status[sc.id] = rb, st
            sel = current if current in self.radios else scenarios[0].id
            self.radios[sel].set_active(True)
            bar = Gtk.Box(spacing=8)
            go = Gtk.Button(label='Watch')
            go.get_style_context().add_class('suggested-action')
            go.connect('clicked', self.on_go)
            bar.pack_end(go, False, False, 0)
            cancel = Gtk.Button(label='Quit' if first else 'Cancel')
            cancel.connect('clicked', lambda b: self.destroy())
            bar.pack_end(cancel, False, False, 0)
            box.pack_start(bar, False, False, 6)
            self.add(box)
            self.show_all()
            self.auto = False
            go.grab_focus()
            for sc in scenarios:
                threading.Thread(target=self.check, args=(sc,), daemon=True).start()

        def on_toggle(self, rb):
            if not self.auto:
                self.touched = True

        def check(self, sc):
            try:
                active, text = sc.make_watch().detect()
            except Exception as e:
                t = str(e)
                if re.search(r'No route|timed out|Name or service|nodename|Connection refused|unreachable', t, re.I):
                    active, text = False, 'Not reachable (not deployed yet, or switched off)'
                elif re.search(r'401|403|Unauthorized|Forbidden', t):
                    active, text = False, 'Login refused (password changed?)'
                else:
                    active, text = False, f'Could not check ({t[:60]})'
            GLib.idle_add(self.show_check, sc.id, active, text)

        def show_check(self, sid, active, text):
            col = SEV_COLOUR['progress'] if active else SEV_COLOUR['noise']
            dot = '\u25CF ' if active else ''
            self.status[sid].set_markup(f'<span size="smaller" foreground="{col}">{dot}{esc(text)}</span>')
            if active and not self.touched:
                self.auto = True
                self.radios[sid].set_active(True)
                self.auto = False
                self.touched = True     # suggest only the first active one
            return False

        def on_go(self, b):
            sid = next(k for k, rb in self.radios.items() if rb.get_active())
            self.on_choose(next(sc for sc in self.scenarios if sc.id == sid))
            self.destroy()

    class App(Gtk.Application):
        def __init__(self, opts):
            super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
            self.opts = opts
            self.win = None
            self.picker = None
            self.scenarios = build_scenarios(opts)

        def do_activate(self):
            if self.win or self.picker:
                (self.win or self.picker).present()
                return
            settings = Gtk.Settings.get_default()
            if settings:
                settings.set_property('gtk-application-prefer-dark-theme', True)
            prov = Gtk.CssProvider()
            prov.load_from_data(CSS)
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), prov, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
            sc = next((x for x in self.scenarios if x.id == self.opts.scenario), None)
            if sc:
                self.open(sc)
            else:
                self.show_picker(None)

        def show_picker(self, from_win):
            if self.picker:
                self.picker.present()
                return
            current = from_win.scenario.id if from_win else load_config().get('scenario')
            self.picker = Picker(self, self.scenarios, current, lambda sc: self.open(sc, from_win), first=from_win is None)
            if from_win:
                self.picker.set_transient_for(from_win)
            self.picker.connect('destroy', lambda w: setattr(self, 'picker', None))

        def open(self, sc, old=None):
            size = tuple(old.get_size()) if old else None
            self.win = MonitorWindow(self, self.opts, sc, size)
            self.win.connect('destroy', lambda w: setattr(self, 'win', None) if self.win is w else None)
            save_config({'scenario': sc.id})
            if old:
                old.destroy()

    app = App(a)
    import signal
    GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signal.SIGINT, lambda: app.quit() or False)   # Ctrl+C in the terminal
    return app.run([sys.argv[0]])


# ---------------------------------------------------------------------------
# --explain: translate a saved log in the terminal
# ---------------------------------------------------------------------------

ANSI = {'progress': '\033[32m', 'watch': '\033[38;5;208m', 'benign': '\033[33m', 'problem': '\033[31m', 'noise': '\033[90m', 'info': '\033[36m'}


def run_explain(path, show_all, color):
    feed = LogFeed('file', keep=10 ** 9)
    opener = gzip.open if path.endswith('.gz') else open
    with opener(path, 'rt', errors='replace') as f:
        for line in f:
            feed.feed(line)
    for e in feed.entries.values():
        if not show_all and not e['hl']:
            continue
        on, off = (ANSI[e['sev']], '\033[0m') if color else ('', '')
        count = f'  (x{e["count"]})' if e['count'] > 1 else ''
        print(f'{local_time(e["ts"])}  {on}{SEV_LABEL[e["sev"]]:<8}{off}  {on}{e["text"]}{off}{count}')


def main():
    p = argparse.ArgumentParser(description='Watch a lab upgrade or deployment: task list and plain-English logs in one window.')
    p.add_argument('--scenario', choices=SCENARIO_IDS,
                   help='what to watch, without asking: ' + ', '.join(SCENARIO_IDS) + ' (default: ask, suggesting what is running)')
    p.add_argument('--installer', default=INSTALLER, help=f'VCF Installer FQDN (default {INSTALLER})')
    p.add_argument('--sddc-manager', default=SDDC_MANAGER, help=f'SDDC Manager FQDN, or "none" (default {SDDC_MANAGER})')
    p.add_argument('--password', default=LAB_PASSWORD, help='password for the API user and the SSH user (default: the lab password)')
    p.add_argument('--api-user', default='admin@local', help='VCF Installer API user (default admin@local)')
    p.add_argument('--ssh-user', default='vcf', help='SSH user that can read the log (default vcf)')
    p.add_argument('--backlog', type=int, default=1500, help='log lines to read back when it starts (default 1500)')
    p.add_argument('--filter', choices=[f for f, _ in FILTERS], default='hl', help='log lines to show at start (default hl = Highlights)')
    p.add_argument('--tab', help='log tab to show at start (installer, sddcm, dm, lcm)')
    p.add_argument('--explain', metavar='LOGFILE', help='translate a saved domainmanager.log (or .gz) in the terminal and exit')
    p.add_argument('--all', action='store_true', help='with --explain: include the routine lines')
    p.add_argument('--state-file', help=argparse.SUPPRESS)              # testing: task list from a saved /v1/sddcs JSON
    p.add_argument('--log-file', action='append', help=argparse.SUPPRESS)  # testing: installer=PATH or sddcm=PATH
    p.add_argument('--sddc-tasks-file', help=argparse.SUPPRESS)       # testing: SDDC Manager tasks from a saved JSON
    a = p.parse_args()
    if a.explain:
        run_explain(a.explain, a.all, sys.stdout.isatty())
        return 0
    try:
        return run_gui(a)
    except (ImportError, ValueError) as e:
        print(f'Cannot open the window: {e}. This needs GTK 3 (python3-gi) and a desktop session.', file=sys.stderr)
        return 1


if __name__ == '__main__':
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)
