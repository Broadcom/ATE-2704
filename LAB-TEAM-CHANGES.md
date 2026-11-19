# ATE-2704 – changes for the HOL core / labs team

Date: 2026-11-04. Core checked against: HOLFY27-MGR-HOLUSER commit `5be280a`
(Shutdown.py v2.8, VCFshutdown.py as on the Manager).

## 2026-11-19: Console title and HOL Admin bookmark

- `config.ini` `[VPOD] conky_title` is now "vSphere to VCF Upgrade and Import" (was "vSphere 8 / VCF 5.2.1 Upgrade Lab"). The core writes it into the console's Lab Status panel at every startup. `console/VMware.config` has the same title so the two match.
- `console/bookmarks.json`: removed the HOL Admin bookmark "vc-wld01-a Appliance Management". This lab has no `vc-wld01-a`.
- `console/bookmarks.json`: the Region A folder "VVF 9.1.1" is now "vSphere 9.1.1". It holds `vc-mgmt-a`, VCF Operations (`ops-a`) and the VCF Installer, which is Region A after Lab 1 (vSphere 9.1.1). The VVF upgrade chapter is no longer in the manual.

## 2026-11-19: Region B recovery template (no Region A)

**Why.** A second, smaller template lets students who fall behind carry on with Lab 4 (import and upgrade wld01-b) and Lab 5 (Aria Automation upgrade). It is a copy of a lab at the end of Lab 3, checked against the current Lab 3 guide on .32, with the Region A vCenter (`vc-mgmt-a`) and hosts (`esx-01a` to `esx-04a`) deleted. `user-vm-a` and `user-vm-b` go with the hosts. Labs 4 and 5 only use Region B, and Region B has no dependency on Region A: separate SSO domains, DNS and NTP on 10.2.1.1, and its own VCF Operations, Fleet, identity broker and license server. The shared services with site-a names stay in the template: the offline depot (SDDC Manager depot, VKS content libraries on port 8080, Gitea), the VCF Installer, the router (NSX and vCenter backups) and the console.

**Problem.** The repo `config.ini` serves every copy and lists the Region A hosts, vCenter, datastore, cluster and test VMs. On a lab without them the core `ESXi.py` would wait for the missing hosts and the startup would fail. Stage detection would also fail on the unreachable hosts and fall back to the baseline settings, losing the Region B upgrade stages.

**Change.**
- The recovery template's Manager has the marker file `/home/holuser/ate2704-no-region-a`. A marker is used instead of an unreachable host or a DNS check, because the Region A names stay in DNS and a slow Region A on the normal template must never be dropped.
- `Startup/ate2704stage.py` 1.5: `strip_region_a()` removes the Region A entries from `[RESOURCES]`, `[VCF]`, `[VCFFINAL]` and `[SHUTDOWN]`: hosts, vCenter, `vsan-mgmt-01a`, `cluster-mgmt-01a`, the test VMs, and their pings (10.1.1.10, 10.1.11.130, 10.1.11.131), ports and URLs. With the marker, `detect()` connects only to the Region B hosts, `apply_overlay()` strips Region A first (so the shutdown also gets it), and the stage summary says "Region A removed (Region B recovery template)".
- `Startup/ate2704stage.py` 1.6: the recovery template also drops the VCF Installer (`vcfinstaller-a`, 10.1.10.250) from the pings, ports and URLs. Labs 4 and 5 do not use it, so it stays in the template powered off and can be powered on if a test needs it.
- New `Startup/ESXi.py` wrapper: with the marker, it strips Region A from the running settings and `/tmp/config.ini` before the core `ESXi.py` checks the hosts, then runs the core module unchanged. Without the marker it only runs the core module.
- `Startup/VCFfinal.py` 1.13: with the marker, Task 0c (Firefox VMCA roots) reads only the Region B vCenters.

Labs without the marker behave exactly as before. To build the template: copy a lab at the end of Lab 3, delete `vc-mgmt-a` and `esx-01a` to `esx-04a`, then on the Manager run `touch ~/ate2704-no-region-a` before the next startup.

## 2026-11-17: lab CA signing helper for VCF certificates (sign-csr.py)

**Problem.** In VCF 9.1, the components that the VCF Installer converge (Region A) or the management services install (Region B) deploys get certificates from the management vCenter's VMCA: SDDC Manager, NSX, the VCF services runtime and fleet. The converge also re-issues the VCF Installer's own certificate about 90 minutes in. The console Firefox trusts only vcf.lab Root Authority. These sites also send HSTS, so after a VMCA certificate replaces a trusted one, Firefox blocks the page with no "Accept the risk" option. VCF Operations, its cloud proxy and the license server use their own self-signed CAs. vCenter, ESX and the in-place-upgraded Region B SDDC Manager keep their vcf.lab certificates.

The HOL CA proxy (`https://ca.vcf.lab/certsrv`) would let Fleet Management request certificates itself, but it authenticates with the holorouter's `/root/creds.txt` value. That is not the lab password, and students cannot see it.

**Change.** New `lab-files/sign-csr.py`, copied to `~/Lab Files` at boot. Students generate CSRs in VCF Operations (Fleet Management > Certificates), download them and run the script. It submits each CSR to the holorouter Vault PKI (`pki/sign/holodeck`, using the token in `~/creds.txt` the way `hol-ssl.py` does, never printed) and writes `<name>.crt` and `<name>-chain.pem`, which students import back in Fleet Management. It keeps the CSR's CN and SANs (including IP SANs), uses a 397-day TTL (the Firefox maximum), and accepts single files or a zip or tar of CSRs. Tested on .32 with a test CSR; the certificate verifies against vcf.lab Root Authority.

## 2026-11-17: Break-Fix fault 11 (NSX admin password expired) repair

`Fix-11` and `Repair-AllLabFaults.ps1` failed with "Native NSX password state did not verify". The fix cleared `password_reset_required` through the NSX node API, but NSX 4.2.1 keeps admin at PASSWORD_EXPIRED until the password changes. The earlier test only passed because the password had already been changed by hand.

`Helpers/security_faults.py` now changes the admin password to a random temporary one and back to the lab password, through the same root-only loopback API inside the NSX appliance. The passwords go to the appliance as a root-only file over the vCenter Guest Operations file transfer, never in process arguments, and the script deletes the file before it uses them. Tested on .32: break then fix, admin back to ACTIVE with the lab password.

## 2026-11-17: Lab feedback - VCF Operations thumbprint and bookmark

**Thumbprint (Lab 2, converge).** `lab-files/VCF-9.1.1-converge-from-vSphere-9.1.1.json` joins the existing VCF Operations (`ops-a`, `useExistingDeployment: true`) by its SHA-256 certificate thumbprint. Students deploy `ops-a` in Lab 1, and each deployment gets a new self-signed certificate. The thumbprint in the file came from the .32 test deployment, so the VCF Installer rejects it in every other lab. The vCenter thumbprint in the same file is fine: `vc-mgmt-a` has a lab-CA certificate that is the same in every copy.

New `lab-files/set-ops-thumbprint.sh` reads the running `ops-a` certificate and writes its thumbprint into the spec. Students run it before they upload the spec (Lab 2 Part 1). Tested on .32 with a wrong thumbprint; a second run reports it is already correct.

**Bookmark.** `console/bookmarks.json`, Region A > VVF 9.1.1: VCF Operations pointed to `ops-01a`. Lab 1 deploys `ops-a` (10.1.1.30), so it now points there. `ops-01a` (10.1.1.31) is only used by the older VVF deployment spec.

## 2026-11-17: SDDC Manager file permissions (VCFfinal 1.10, Task 0b)

**Problem.** On a healthy lab the Region B SDDC Manager precheck fails `SDDC_MANAGER_FILE_PERMISSIONS_CHECK` (`/var/log/vmware/vcf/lcm/sddcmanager-fs-check.log`):
- `application.properties`, `application-prod.properties` and `BomSpecification.yml` in `/opt/vmware/vcf/lcm/lcm-app/conf` are owner read-only;
- `/opt/vmware/vcf/commonsvcs/bin/commonsvcs` has no owner write bit;
- `/home/vcf/.ssh/known_hosts_backup` is owned by root.

SDDC Manager rewrites at least `application.properties` and `known_hosts_backup` at boot (its `set-known_hosts.sh` creates the backup as root), so fixing the template does not stay.

**Fix.** New `Startup/ate2704sddcperms.py`, run by `Startup/VCFfinal.py` 1.10 as Task 0b. It logs in to sddcmanager-b as vcf, uses `su` (root SSH is not allowed), adds the owner write bit where it is missing and gives `known_hosts_backup` to vcf. Files that are already correct are left alone, and group/other access is not changed. It never fails the lab. Setting: `[VCFFINAL] sddc_perms` (default true).

**Tested** on .32: dry run, fix (two files reverted to their template state first) and a recheck; the precheck then passed `SDDC_MANAGER_FILE_PERMISSIONS_CHECK`.

**Not fixed, known result.** `INITIALIZE_VCENTER_CHECK` and `NTP_TIME_SYNC_CHECK` (common services) still fail. SDDC Manager looks up the vc-mgmt-b SSO (PSC) record, `31c51500…`, as a vCenter ("Cannot find vCenter entity"). The database links between the vCenter (`6d26e7dc…`) and that record are correct. Region B has been upgraded to 9.1.1 on an earlier copy without these blocking it, so they stay as known precheck results.

## 2026-11-17: Break-Fix pack moved to Region B mgmt-b

`lab-files/Break-Fix Scripts`: the random fault pool is now the faults that the SDDC Manager precheck, the SoS health check or the vSphere Client can show on mgmt-b. All five were run Break and Fix on .32.

| Id | Fault | Target | Shown by |
|---|---|---|---|
| 01 | NTP stopped, startup policy manual, clock 10 minutes ahead | esx-04b | vSAN health "Time is synchronized across hosts and VC", SoS vSAN YELLOW |
| 04 | DRS manual | cluster-mgmt-01b | precheck DRS and maintenance-mode dry-run checks |
| 11 | NSX admin password expired | nsx-mgmt-01b | precheck `NSX_T_AUDIT_CHECK`, NSX login |
| 13 (new) | ESXi root password changed outside SDDC Manager | esx-03b | SoS Password Expiry Status RED |
| 14 (new) | Host left in maintenance mode | esx-02b | vSphere Client |

- `LabConfig.json`: 01 and 04 retargeted to ManagementB; new `ClockSkewMinutes`, `DriftPassword` and `SddcManager` settings; new `Random` flag (only 01, 04, 11, 13 and 14 are picked by `Start-RandomLabFaults.ps1`; the others still run with `-Only`).
- `LabFaults.psm1`: new kinds `HostPassword` (root login checked over HTTPS with the host certificate pinned to vCenter's copy; the Fix clears the password history for the reset and updates SDDC Manager's stored password) and `Maintenance`.
- `LabOrchestrator.psm1`: 04 and 14 are never picked together (leaving maintenance mode needs DRS).
- Lockout: ESXi and NSX lock the account for 15 minutes after 5 failed logins. The password check tries the expected password first so the scripts don't trigger it.

## 2026-11-16: VKS adapter heal (VCFfinal 1.9, Task 1d)

**Problem.** On fresh copies of the 2026-11-16 template (.26 and .45; not on .24 itself) the `vks-01` control plane VM on `esx-06b` powered on with its network adapter disconnected: `Failed to connect port to opaque network: Not found`. NSX Manager had the port (DOWN, unbound) and the segment, and the host's NSX control and management connections were UP, but `esx-06b`'s local NSX database (NestDB) had no entry for that VIF; on .24 it had one. ESX does not retry, so the VKS cluster API and the voting app stayed down and the startup reported Ready after the 30-minute Kubernetes wait. Restarting `nsx-opsagent` / `nsx-proxy` on the host did not help. The Supervisor blocks reconnecting or moving its VMs through vCenter (`VirtualMachine.Interact.DeviceConnection`, relocate denied).

**Fix.** `Startup/VCFfinal.py` 1.9 adds Task 1d, run before the Supervisor Services and Kubernetes checks: it finds powered-on VMs on the Supervisor vCenter whose NSX adapter should be connected but failed at power-on (`unrecoverableError`), puts their host into maintenance mode (vSAN: ensure accessibility) so DRS moves them, reconnects the adapter on the new host with `vim-cmd vmsvc/device.connection` over SSH, and exits maintenance mode. Never fails the lab. Settings: `[VCFFINAL] vks_nic_heal` (default true), `vks_nic_heal_minutes` (default 25).

**Tested** on .45: detected the failed adapter, `esx-06b` maintenance mode, VM moved to `esx-05b`, adapter reconnected, host back out in 2.5 minutes; the VKS API answered 4 minutes later and the voting app a minute after that; the lab then reached Ready. The root cause of the missing NestDB entry on deployed copies is not known yet.

## 2026-11-16: console "~/Lab Files" from the repo

New `lab-files/` folder, a mirror of the console's `~/Lab Files`, and new `Startup/ate2704labfiles.py`, called by `Startup/VCF.py` 1.1 at every startup. Each file in `lab-files/` is copied to the same path under `~/Lab Files` if it is missing or its content differs; the executable bit follows the repo. Nothing is deleted: files only in the template (large or build-only files, students' own files) stay. A failure is logged and never stops the startup.

**To change or add a student file:** edit or add it under `lab-files/` and push. Labs deployed after that get it; no template re-master.

`ate2704labfiles.py` 1.1 mirrors `lab-build/` to `~/Lab Build` the same way (build and instructor files, kept out of the students' folder). First file: `lab-build/RegionA/custom_vsan_esa_hcl_9.1.json`, the vSAN ESA HCL for 9.1 made on lab copy .47 on 2026-11-12 (23 MB; git stores it compressed, about 1.2 MB).

Contents: the files the lab guides use (VVF deployment spec, converge JSON, `wcp-legacy-zone-fix.sh`, `kb425489-endpoint-certs.sh`, `vcf.lab-root-CA.pem`, `vcfa-tenant-to-vcfsso.py`), `supervisor-dns-fix.sh`, and the `Break-Fix Scripts` pack (without its runtime `State/` folder). On the template, `~/Lab Files/LabScripts` was renamed `Break-Fix Scripts`, and the automated Region A upgrade scripts moved from `LabScripts/Upgrades` to `~/Lab Build/RegionA-Upgrades` (build and instructor tools, not for students; `~/Lab Build` is not synced).

Also on the template, everything that is not student or break/fix material moved from `~/Lab Files` to `~/Lab Build` (RegionA, RegionB, VKS-Demo, Supervisor-Certificates, Tools, Archive; list in `~/Lab Build/moved-from-Lab-Files-20261116.txt`). `lab-files/` (47 files) is identical to the template's `~/Lab Files`.

Tested 2026-11-16: template .24 dry run, 0 to copy and 49 unchanged (before the clean-up); lab copy .32, a real run, then a new and a changed file, both copied, then reverted.

## 2026-11-16: console Firefox bookmarks

`console/bookmarks.json` is new. The core `labstartup.sh` finds it at every boot and imports it with `console/import-firefox-bookmarks.sh`, so the console bookmarks no longer depend on what was saved in the template's Firefox profile. Toolbar: HOL Admin (unchanged), Region A (vSphere 8 | VVF 9.1.1 | VCF 9.1.1 > Management Domain > Hosts), Region B (VCF 5.2.1 > Management Domain > Hosts | VCF 9.1.1 > Management Domain and Workload Domain, each with Hosts), VCF Installer. License servers are left out (no login). The import replaces the Firefox bookmark database, which also clears browsing history at each boot. To change the bookmarks: edit them in Firefox, export with Bookmarks > Manage Bookmarks > Import and Backup > Backup, and replace this file.

## 2026-11-16: Region A after the VCF Installer, the converge and the overlay chapter

`Startup/ate2704stage.py` 1.3 adds three Region A stages (`region_a_vsp`, `region_a_vcf`, `region_a_vpc`, see the table below). Before this, a Region A upgraded with the VCF Installer would not start its `vsp-01a` runtime after a cold start, and a converged Region A would not start `nsx-mgmt-01a` or `sddcmanager-a`. The `add` changes of all stages are now merged per setting, so both runtimes share one power-on block (all control plane nodes, one `Pause:300`, all workers), and `[VCF] vspvms` lists both regions.

Tested 2026-11-16 on the converged lab copy (.32): detection, the new port/URL checks, `Shutdown.py --dry-run` (Region A runtime script dry run authenticated; VSP, VCF Operations, proxy, NSX and SDDC Manager phases list the Region A VMs) and `VCF.py --standalone --dry-run`. Template .24 (not upgraded): no changes. A real shutdown and cold start of a converged lab is not tested yet.

## 2026-11-15: upgrade-stage aware startup and shutdown

**Problem.** `config.ini` describes the delivered lab. After attendees upgrade
(Region A to 9.1.1, Region B to VCF 9.1.1, Aria Automation to VCF Automation
9.1.1, wld01-b to 9.1.1), a shutdown and cold start used the old settings: the
new VCF 9 VMs were not started, deleted vIDM / Suite Lifecycle VMs were still
expected, and the old `auto-01b` VM would be powered on although its IP
(10.2.12.151) now belongs to VCF Automation 9.1.

**Change (no core files touched).**

| File | Status | Purpose |
|---|---|---|
| `Startup/ate2704stage.py` | new (v1.0) | Detects the stage over direct ESXi connections (VMs that exist, ESX versions) and holds the setting changes per stage (`OVERLAY`). Run it on its own to see the result. |
| `Startup/VCF.py` | new (v1.0) | Wrapper: detect, apply the overlay to `lsf.config` and `/tmp/config.ini` (original kept as `/tmp/config.ini.base`), then run the core `~/hol/Startup/VCF.py` unchanged. Detection failure = baseline settings. |
| `Startup/VCFfinal.py` | changed (v1.6) | Stage report (vCenter versions, wld01-b import state from SDDC Manager, Supervisor version/status/services); Aria 8 steps skipped and old `auto-01b` never powered on after the Automation upgrade; new Task 3b waits for the VCF 9 UIs; Task 1c (x-region, from v1.5) keeps checking the Edge TEPs after vIDM / Suite Lifecycle are deleted, because VCF Automation 9.1 uses the same segment. |
| `Shutdown/Shutdown.py` | changed (v1.1) | Detects the stage and writes the matching settings for the core shutdown. After the Automation upgrade: no `deploy.sh`; VCF Automation 9.1 is powered off the documented way through the Region B Fleet lifecycle service, `fleet-01b` (KB 444635, = VCF Operations > Lifecycle > VCF Automation > Power off; token obtained from `vsp-01b` because the core `fleet.get_fleet_lcm_jwt()` only knows `vsp-01a`), falling back to its runtime shutdown script. Then the management services runtime is stopped with Broadcom's `vcf_services_runtime_shutdown.sh --skip-poweroff` (KB 440874). The core Phase 2b would run that script too, but it powers the VMs off with `govc`, which is not installed on the Manager. |
| `config.ini` | changed (v2.7) | Notes only; the baseline settings are unchanged. |

Stages and what they change (see `OVERLAY` in `ate2704stage.py`):

| Stage | Detected by | Startup / shutdown changes |
|---|---|---|
| `region_a_9` | VM `ops-a` or `license-server` exists (vSphere 9.1.1 / converge path), or `ops-01a` or `license-a` (VVF path, since `ate2704stage.py` 1.4) | start the license server (post-Edge), VCF Operations and `opsproxy-01a`, whichever exist; wait for that VCF Operations UI; shut down VCF Operations and its proxy in the VCF Operations phases |
| `region_a_vsp` | VM `vsp-01a-*` exists (VCF Installer path) | start the `vsp-01a` control plane node, 5 min pause, then its workers (KB 440862; shares the pause with `vsp-01b` when both exist); stop the runtime gracefully on 10.1.1.131 (KB 440874); VSP shutdown phase |
| `region_a_vcf` | VM `sddcmanager-a` exists (converged to VCF 9.1.1) | start and stop `nsx-mgmt-01a` with the other NSX Managers and `sddcmanager-a` with the other SDDC Manager; check `sddcmanager-a`, `nsx-mgmt-a` and `nsx-mgmt-01a` on 443 and their UIs |
| `region_a_vpc` | `user-vm-a` or `user-vm-b` not on `user-network-a` (optional NSX overlay chapter) | drop the 10.1.11.130/131 pings and the SSH checks of the test VMs, which now have DHCP addresses on VPC subnets |
| `region_b_9` | VM `ops-01b` or `vsp-01b-*` exists | start `license-b`; the `vsp-01b` control plane node, 5 min pause, then its workers (KB 440862); `ops-01b`, `opsproxy-01b`; wait for VCF Operations and the identity broker (the runtime needs 15-20 min to recover); shutdown phases for VCF Operations, its proxy and the VSP nodes |
| `automation_9` | VM `auto-int-b-*` exists | never start `auto-01b`; start VCF Automation 9.1 through Fleet once the management runtime is back, then check it; no `deploy.sh`; drop the 10.2.12.151 ping; shutdown powers VCF Automation off through Fleet first and waits for that to finish |
| `wld_9` | ESX 9.x on esx-05b..08b | reported only |
| any | VM no longer exists | removed from VM lists, and its TCP/URL and `xregion_*` checks (vIDM, Suite Lifecycle) |

**Test on the fully upgraded lab copy (.54, now .26), 2026-11-15, and the fix it led to:**

- Shutdown (wrapper 1.1) took 47 min and the cold start reached Ready in 24 min.
- 4 minutes after Ready, VCF Automation 9.1 was powered off by `svc-sddclcm-vc-…`.
  - Cause: the Fleet "Shutdown component VCF Automation" workflow was still running when the wrapper stopped the management services runtime. The core helper stops tracking it after about 2 min.
  - When the runtime recovered, the workflow resumed, powered the VM off, and ended FAILED. The startup had powered the VM on directly and checked it before that happened.
- Fix:
  - `Shutdown/Shutdown.py` 1.2 waits for the Fleet workflow to end (up to 45 min) before stopping the runtime.
  - `Startup/VCFfinal.py` 1.7 Task 3a starts VCF Automation through Fleet (`action=start`, the documented power-on, KB 444635) once the runtime is back, and only then runs the VCF Automation URL check. If Fleet cannot be reached, it falls back to a VM power-on.
  - `Startup/ate2704stage.py` 1.1 no longer powers `auto-int-b-*` on with the post-Edge VMs, and holds the shared Fleet helpers.

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
| `console/VMware.config` | new | Lab Status panel (conky) settings. Startup copies it to the console after the core `hol/console/VMware.config`, so it wins. Same as the core file except the position: `top_left`, `gap_x` 280, `gap_y` 275, under the "LAB CONSOLE" title on the lab wallpaper |
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

## Site B workload domain: Supervisor Services, VKS and sample app (2026-11-10)

### What is in the template now

| Item | Detail |
|---|---|
| vc-wld01-b / esx-05b-08b | vCenter 8.0 U3i (25197330), ESX 8.0 U3i (25205845) |
| Supervisor (domain-c9) | v1.32.9, VKS 3.4.2, Velero 1.8.0; API 10.2.15.2 |
| Offline libraries | Served by `supervisor-library.service` on offlinedepot:8080 (`/depot/supervisor`). VKS library `f56f762e…` is the Supervisor default (9 Kubernetes releases, all cached). The Supervisor image library `b3b34db0…` is unused on 8.0 U3 (kept for 9.x) |
| Supervisor Services | Contour 1.33.5 (Envoy LB 10.2.15.3), Harbor 2.15.2 (`https://harbor.site-b.vcf.lab`, admin / lab password, values in `~/Lab Files/harbor-data-values-site-b.yml`), Local Consumption Interface 9.1.3, Argo CD Service 1.3.0. The legacy (8.0 U3) YAMLs are used; the VCF 9.x ones are in `~/DeploymentMedia` for later |
| Namespace `vks-demo` | VKS cluster `vks-01` (v1.32.10, class builtin-generic-v3.4.0, 1 CP + 2 workers best-effort-small, trusts the Harbor CA); Argo CD instance `argocd-01` 3.4.9 (`https://argocd.site-b.vcf.lab`, admin / lab password) |
| Harbor project `voting` | Docker example voting app images (vote/result `before`+`after`, worker, redis 7.2/7.4-alpine, postgres 15-alpine), copied from Docker Hub by Harbor replication |
| Gitea | `http://offlinedepot.site-a.vcf.lab:3000/holadmin/voting-app`, `gitea.service` on the offline depot |
| Voting app | Argo CD app `vks-demo/voting-app`, auto-sync from Gitea `k8s/`, namespace `voting` on vks-01; `http://vote.site-b.vcf.lab` (10.2.15.6), `http://result.site-b.vcf.lab` (10.2.15.7) |
| DNS (holorouter) | harbor, vks-01, argocd, vote, result in site-b.vcf.lab (A + PTR) |

Upgrade exercises this leaves room for: VKS 3.4 -> 3.6 and the cluster 1.32 -> 1.33;
Argo CD 3.4.9 -> 3.5.3; the app from `before` to `after` images (and redis 7.2 -> 7.4)
by a commit to Gitea.

### Startup / config changes

- `config.ini` v2.5: Gitea (3000) and the library (8080) in `TCPServices`, Gitea in
  `URLS`; `[VPOD] new-dns-records` for the five names above; `[VCFFINAL]`
  `vks_unpause`, `supervisor_services`, `k8s_urls`, `k8s_wait_minutes` (30),
  `k8s_required` (false).
- `Startup/VCFfinal.py` v1.4:
  - **Task 1b** un-pauses VKS clusters. The core shutdown Phase 3b sets
    `spec.paused: true` on every Cluster and nothing in the core startup clears it
    (the core's own comment says so), which would leave CAPI frozen: no node
    remediation and no cluster upgrade.
  - **Task 4** waits for the four Supervisor Services to be CONFIGURED.
  - **Task 5** checks Harbor, Argo CD, the vks-01 API (`/livez`) and the vote/result
    pages. It runs after the Aria Automation wait.
  - Tasks 4/5 only warn unless `k8s_required = true`.
- Tested on 2026-11-10 against the live lab with vks-01 paused by hand: un-paused,
  all 4 services CONFIGURED, all 5 URLs OK; `--dry-run` of the whole override OK.

### Request for the core team

7. **Add a VKS un-pause step to the core startup** (VCFfinal or Kubernetes.py), to
   match the pause in VCFshutdown.py Phase 3b. This lab does it in its override.
