# ATE-2704 – changes for the HOL core / labs team

Date: 2026-11-04. Core checked against: HOLFY27-MGR-HOLUSER commit `5be280a`
(Shutdown.py v2.8, VCFshutdown.py as on the Manager).

## Summary

**No core files were modified.** `git status` in `~/hol` on the Manager is clean
apart from a new log file (`shutdown-aria.log`). Everything lab-specific
lives in this ATE-2704 repo and is driven by `config.ini`. A core update can't
overwrite it, but see "Requests for the core team": two core behaviours make
these overrides necessary.

## Files in this repo

| File | Status | Purpose |
|---|---|---|
| `config.ini` | changed (v2.4) | All lab VMs, checks, `[VCFFINAL]` and `[SHUTDOWN]` settings for an 8.x / 5.2.1 baseline |
| `Startup/VCFfinal.py` | new (v1.3) | Lab override of the core VCFfinal: only Supervisor wait, Aria Automation power-on, Aria URL wait (skips VCF 9.x-only tasks). v1.1: standalone runs load config.ini like the core modules |
| `Shutdown/Shutdown.py` | new | Wrapper: stops Aria Automation 8.x services with `deploy.sh --shutdown`, then runs the core `~/hol/Shutdown/Shutdown.py` with the same options |
| `SHUTDOWN.MD` | changed | Shutdown procedure for this lab |
| `LAB-TEAM-CHANGES.md` | new | This file |

### config.ini keys used by the shutdown

| Section / key | Value | Read by |
|---|---|---|
| `[VCF] vcfpostedgevms` | opslcm-b, vidm-01b, auto-01b | core Phase 17c |
| `[VCFFINAL] tanzucontrol` | `SupervisorControlPlaneVM.*:vc-wld01-b.site-b.vcf.lab` | core Phases 3, 3b, 3c |
| `[VCFFINAL] vravms` | `auto-01b:vc-mgmt-b.site-b.vcf.lab` | core Phase 1b |
| `[SHUTDOWN] fleet_products`, `vcf_ops_*_vms` | empty | core Phases 1, 8–10, 13 (no VCF 9 Operations here) |
| `[SHUTDOWN] vcf_identity_broker_vms` | vidm-01b | core Phase 11 |
| `[SHUTDOWN] vcf_ops_fleet_vms` | opslcm-b | core Phase 12 |
| `[SHUTDOWN] workload_vms` | user-vm-a, user-vm-b | core Phase 4 |
| `[SHUTDOWN] sddc_manager_vms` | sddcmanager-b | core Phase 16 |
| `[SHUTDOWN] vra_prep_hosts` | auto-01b.site-b.vcf.lab | **this repo's wrapper only** |
| `[SHUTDOWN] vra_prep_timeout` | 2700 | **this repo's wrapper only** |

The two `vra_prep_*` keys are new and unknown to the core, which ignores them.

## Shutdown process for this lab

Run on the Manager as holuser: `python3 /vpodrepo/2027-labs/2704/Shutdown/Shutdown.py`
(`--dry-run` to preview). The order:

| Step | What | VMs / hosts |
|---|---|---|
| Pre (wrapper) | SSH `root@auto-01b`: `/opt/scripts/deploy.sh --shutdown` (up to 45 min, usually 10–20) | auto-01b services |
| 0 / 0b | Pre-checks; Docker host (not present, skipped) | – |
| 1 | Fleet API (not present on 5.2.1, skipped) | – |
| 1b | Guest shutdown through vCenter | auto-01b |
| 2 | Connect to the three vCenters | – |
| 3 / 3b / 3c | Stop WCP, pause Supervisor clusters, power off control plane VMs | vc-wld01-b Supervisor |
| 4 | Workload VMs (+ discovered Supervisor workloads) | user-vm-a, user-vm-b |
| 5 / 6 / 7 | Workload domain Edges, NSX Manager, vCenter | edge-wld01-01b/02b, nsx-wld01-01b, vc-wld01-b |
| 11 / 12 | Identity broker, Suite Lifecycle | vidm-01b, opslcm-b |
| 14 / 15 / 16 | Management Edges, NSX Manager, SDDC Manager | edge-mgmt-01b/02b, nsx-mgmt-01b, sddcmanager-b |
| 17 | Management vCenters | vc-mgmt-b, vc-mgmt-a |
| 17b / 17c | Direct ESXi sessions; re-check post-Edge VMs (already off) | opslcm-b, vidm-01b, auto-01b |
| 18 / 19 / 19c | Host advanced settings, vSAN elevator, straggler audit | all 12 hosts |
| 20 / 21 / 22 | Host shutdown, cleanup, wait for power off | esx-01a–04a, esx-01b–08b |

Then: router `/root/shutdown.sh` (as root), then the Manager, then the console.

Verified by a full `--dry-run` on 2026-11-04: every phase ran without errors,
Phase 3b reached the Supervisor (SCP VIP 10.2.1.188).

## Startup process for this lab

No core change. At boot, `labstartup.sh` reads `vPod_SKU = ATE-2704` /
`labtype = ATE` from the console's `vPod.txt`, clones
`https://github.com/Broadcom/ATE-2704.git` into `/vpodrepo/2027-labs/2704`
(branch `dev` on a Holodeck dev pod, `main` in production) and copies its
`config.ini` to `/tmp/config.ini`. The core picks up `Startup/VCFfinal.py`
from that folder through `choose_file()`; all other modules are core.

| Module | What it does for ATE-2704 |
|---|---|
| prelim | Core preliminary tasks (desktop, DNS checks, firewall/proxy per labtype) |
| ESXi | Checks all 12 hosts and their ports |
| VCF | Exits maintenance mode, waits for vsan-mgmt-01a, vsan-mgmt-01b, vsan-wld01-01b; starts nsx-mgmt-01b, nsx-wld01-01b; the four Edges; opslcm-b, vidm-01b, auto-01b; then vc-mgmt-a, vc-mgmt-b, vc-wld01-b |
| vSphere | Connects to the three vCenters, checks datastores, starts sddcmanager-b, user-vm-a, user-vm-b |
| pings / services / urls | 21 pings, 15 TCP ports, 10 URLs from config.ini |
| **VCFfinal (lab override)** | Supervisor on vc-wld01-b running (20 min, not required); auto-01b powered on; `https://auto-01b.site-b.vcf.lab/` answers (35 min, required) |
| final | Core final tasks (ready status) |

Verified 2026-11-04 on the dev Manager: `--standalone --dry-run` of ESXi, VCF,
vSphere, pings, services, urls and the VCFfinal override all exit 0 with the
expected lists; live checks against the running lab: 21/21 pings, 15/15 TCP
ports, 11/11 URLs (incl. Aria Automation) answer. `labstartup.py --dry-run`
was not used: the flag is parsed but not acted on, so it runs a real startup.

**Repo state:** GitHub `Broadcom/ATE-2704` branch `dev` = commit `e6b92e7`
(config.ini v2.1 + VCFfinal v1.0); `main` is still the February v1.1 config
for a different layout (auto-a in Region A) with no override. config.ini v2.2,
VCFfinal v1.1, `Shutdown/`, `SHUTDOWN.MD` and this file still need to be
committed and pushed, and merged to `main` before production use.

## Lab environment changes (not scripts)

These were needed for the core shutdown to work and are part of the vPod:

| Component | Change | Why |
|---|---|---|
| All 12 ESXi hosts | SSH service on, starts with the host; `UserVars.SuppressShellWarning = 1` | Core host phases use SSH (sshpass, root password) |
| vc-mgmt-a | SSH enabled (it was off) | Core SSH to vCenter |
| vc-mgmt-a, vc-mgmt-b, vc-wld01-b | root login shell `/bin/appliancesh` → `/bin/bash` | Core Phase 3/3b run `vmon-cli -k wcp` and `decryptK8Pwd.py` over non-interactive SSH, which the appliance shell rejects |

Undo the shell change: log in as root, `shell`, `chsh -s /bin/appliancesh root`.

## First real shutdown / startup test (2026-11-04)

**Shutdown** (`Shutdown/Shutdown.py` wrapper, 12:10-12:46, 33 min): `deploy.sh --shutdown`
stopped the Aria Automation services in 181 s; all phases completed; vSAN is ESA so
the elevator was skipped; the final audit shut down 6 vCLS VMs and `demo-002` (Aria
test deployment); all 12 hosts off in 106 s.

**Startup** failed in `services`, then was fixed and completed (Ready 14:59). Two causes:

1. **Holorouter lost the Site B policy routing rules at boot.**
   `/etc/rc.d/init.d/startup_script.sh` adds rules 901-904 (10.2.12-15.0/24 → table 20)
   at boot, and 3 s later `systemd-networkd` starts and deletes all rules it did not
   create (`ManageForeignRoutingPolicyRules=yes` is the default). The x-region network
   (Aria Suite Lifecycle, vIDM, Aria Automation) and `seg-01b` were unreachable, so
   `pings` and the required `services` check failed. Rules 901/902 date from
   2026-10-29 (903/904 from 2026-11-03); they had been added at runtime and this
   was the first router reboot since.
   **Fix:** `/etc/systemd/networkd.conf.d/10-keep-lab-routing-rules.conf` with
   `ManageForeignRoutingPolicyRules=no` (directory 755 so networkd can read it).
   This is a holorouter image change; please carry it into the router template.
   Unrelated pre-existing issue: `startup_script.service` always ends "failed"
   because of six generated lines `ip link add ... eth1.Routed type vlan id Routed`
   (no VLAN ID / address). Harmless, but it hides real failures.

2. **Aria Automation services do not start by themselves after `deploy.sh --shutdown`.**
   That command removes the services; Broadcom's start procedure is to run
   `/opt/scripts/deploy.sh` after power-on (20-40 min). After a plain guest shutdown
   they restart on their own, so this follows from the shutdown wrapper.
   **Fix:** `Startup/VCFfinal.py` v1.2 adds Task 2b: wait for the appliance's Kubernetes
   node, then start `deploy.sh` in the background if no pods exist in `prelude`
   (skipped if pods exist or deploy.sh is already running). `config.ini` v2.3 adds
   `[VCFFINAL] vra_start_hosts` / `vra_node_wait_minutes`, removes `auto-01b:443` from
   `TCPServices` (closed until deploy.sh finishes) and raises `vraurl_wait_minutes`
   to 60, and `[VPOD] maxminutes` from 60 to 90 (the overall limit is checked in every module wait, so it also caps the Aria wait). In this test deploy.sh had already been started by hand, so Task 2b took
   its "already running" branch; the "start it" branch runs on the next full boot.

## Follow-up changes (2026-11-04, after the second test)

- `Startup/VCFfinal.py` v1.3, **Task 0**: enables SSH on all `[RESOURCES] vCenters`
  through the appliance API with `{"enabled": true}` where it is off (works around
  request 6 below; `[VCFFINAL] vcenter_ssh = false` turns it off). Never fails the lab.
  Tested against the live lab: all three vCenters reported "already enabled".
- `config.ini` v2.4: `demo-002` (Aria Automation demo VM, seg-01b 10.2.13.10) added to
  `[RESOURCES] VMs` (started by vSphere.py) and `[SHUTDOWN] workload_vms` (Phase 4);
  the SDDC Manager ping 10.2.1.5 removed from `Pings` (the VM starts after the ping
  check; `sddcmanager-b:443` in `TCPServices` covers it).
- Aria Automation: the Monthly Lease Policy was deleted and the demo deployment's
  lease set to Never, so `demo-002` is not destroyed in pods deployed from the template.

## Requests for the core team

1. **Shutdown overrides aren't used.** `Shutdown.py` loads `VCFshutdown.py`
   from its own folder, and nothing calls `choose_file('Shutdown', ...)`, so a
   `Shutdown/` folder in a vPod repo or labtype folder is never used. That's why
   this lab needs a wrapper that is run by its full path. A small hook in
   `Shutdown.py` (for example "run `<vpodrepo>/Shutdown/pre-shutdown.py` if it
   exists") would let labs drop the wrapper.
2. **Phase 1b assumes VCF 9.x Automation.** The Kubernetes clean-up uses fixed
   addresses 10.1.1.69–74 and `vmware-system-user`. For Aria Automation 8.x
   (`[VCFFINAL] vravms`) it would be better to run `/opt/scripts/deploy.sh --shutdown`
   as root before the guest shutdown, and to allow a longer wait than 300 s.
3. **Phase 1 only probes Region A** (`ops-a`, `opslcm-a`). A config key for the
   Fleet / Suite Lifecycle FQDN would let Site B 5.x labs use it.
4. Please keep `[VCFFINAL] tanzucontrol` / `vravms` and the `[SHUTDOWN]` keys
   above working as they do now. This lab's shutdown depends on them.
5. `labstartup.py --dry-run` is accepted but ignored; it should either be
   honoured or removed, so nobody starts a lab by accident while testing.
6. **vSphere.py cannot enable vCenter SSH on 8.0 U3.** It sends
   `PUT /api/appliance/access/ssh` with body `true` (`json=True`); vCenter 8.0 U3
   answers HTTP 400 and expects `{"enabled": true}`. On 2026-11-04 `vc-wld01-b`
   came up with SSH off, the startup could not fix it ("Failed to enable SSH
   (HTTP 400)"), and the next shutdown could not stop WCP or pause the Supervisor
   (Phase 3/3b "SSH port not reachable"); vCenter then restarted the control plane
   VMs and Phase 19c had to power them off. SSH was enabled with
   `{"enabled": true}` (HTTP 204). The bash-shell call (`access/shell`) also returns
   HTTP 500 on all three vCenters; harmless here because root's shell is already bash.

## Notes for deployment

- The core reads `/tmp/config.ini`. On the dev Manager this was a stale copy
  from VCF-91C4SB; it has been replaced with this repo's config.ini (backup:
  `/tmp/config.ini.VCF-91C4SB.bak`). Once the vPod repo points at ATE-2704,
  `labstartup.sh` copies the right file automatically.
- A staging copy of this repo's wrapper, VCFfinal override and config is at
  `~/ate2704/` on the Manager until the vPod repo is deployed from git; it
  can be deleted then.
