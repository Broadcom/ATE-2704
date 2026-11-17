# ATE-2704 Partner Advanced Workshop - VCF Upgrades

## Overview

ATE-2704 is a hands-on workshop for Broadcom partners on upgrading customer
environments to **VMware Cloud Foundation (VCF) 9.1.1**. Attendees work in a
two-region lab that starts on the releases most customers run today, vSphere 8
and VCF 5.2.1, and upgrade every part of it themselves, following the
documented Broadcom procedures in the order they must be done.

### Purpose of the class

The workshop prepares partners to plan and deliver real-world VCF 9 upgrades.
By the end, attendees can:

- choose the right upgrade path for a starting point: vSphere 8 to vSphere
  9.1.1, to VMware vSphere Foundation (VVF) 9.1.1, or to VCF 9.1.1, and VCF
  5.2.1 to VCF 9.1.1;
- deploy the new VCF 9 management components (VCF Operations, the license
  server, the VCF services runtime and identity broker) and use VCF Operations
  to plan and run upgrades;
- bring an existing vSphere environment under VCF management with an import,
  including a running Supervisor and Kubernetes workloads;
- upgrade Aria Automation 8 to VCF Automation 9 and retire Aria Suite Lifecycle
  and VMware Identity Manager;
- recognise and work around the problems they will meet in the field. The lab
  guides call these out where they happen.

### What attendees do

The lab guide is ScreenSteps manual *Partner Advanced Workshop: Upgrades*. Each
chapter is one upgrade, with an "Overview and Lab Environment" article and
numbered parts.

| Region | Chapter | From | To |
|---|---|---|---|
| A | Upgrade vSphere 8.0U3 to vSphere 9.1.1 | vSphere 8.0 U3 | vSphere 9.1.1 (VCF Operations, license server, vCenter, ESX) |
| A | Upgrade vSphere 8.0U3 to VVF 9.1.1 | vSphere 8.0 U3 | VVF 9.1.1 with the VCF Installer |
| A | Upgrade vSphere 9.1.1 to VCF 9.1.1 | vSphere 9.1.1 | VCF 9.1.1 management domain (converge) |
| A | Enable NSX Overlay Networking and VPCs (Optional) | VCF 9.1.1 | host TEPs, distributed transit gateway, VPCs, distributed firewall |
| B | Upgrade VCF 5.2.1 to VCF 9.1.1 | VCF 5.2.1 management domain | VCF 9.1.1 |
| B | Import and Upgrade vSphere 8.0U3i with Supervisor | standalone vSphere 8.0 U3i | VCF 9.1.1 workload domain, Supervisor and VKS updated |
| B | Aria Automation Upgrade | Aria Automation 8.18.1 | VCF Automation 9.1.1 with VCF SSO |

**The lab template always stays at the pre-upgrade baseline**, so every class
starts from the same point. Attendees run the upgrades; they are never run on
the template.

## Lab environment

### Region A - `site-a.vcf.lab`

A standalone **vSphere 8.0 U3** environment with no NSX and no SDDC Manager:

- vCenter `vc-mgmt-a` (10.1.1.10), datacenter `dc-a`, cluster
  `cluster-mgmt-01a`
- four ESX 8.0 U3 hosts (`esx-01a` to `esx-04a`) on vSAN ESA (`vsan-mgmt-01a`),
  managed with a vLCM image
- two test VMs, `user-vm-a` and `user-vm-b`, on `user-network-a` (VLAN 29)
- a VCF Installer appliance, `vcfinstaller-a` (10.1.10.250), ready for the
  upgrade

### Region B - `site-b.vcf.lab`

Two environments:

- **VCF 5.2.1 management domain `mgmt-b`:** SDDC Manager `sddcmanager-b`,
  vCenter 8.0 U3 `vc-mgmt-b`, NSX 4.2.1 `nsx-mgmt-b` with an Edge cluster, and
  four ESX hosts (`esx-01b` to `esx-04b`) on vSAN ESA. On its `x-region` network
  run Aria Suite Lifecycle 8.18 (`opslcm-b`), VMware Identity Manager 3.3.7
  (`vidm-01b`) and Aria Automation 8.18.1 (`auto-01b`, organization Kestrel).
- **Standalone vSphere 8.0 U3i workload cluster `vc-wld01-b`:** its own SSO
  domain (`wld.sso`), NSX 4.2.1 (`nsx-wld01-b`) with two Edges, and four ESX
  hosts (`esx-05b` to `esx-08b`). It runs a vSphere Supervisor with the Contour,
  Harbor, Local Consumption Interface and Argo CD Supervisor Services, and a VKS
  cluster, `vks-01`, with a sample voting app that Argo CD deploys from a local
  Gitea repository.

### Shared services

The lab is self-contained and needs no internet access:

- an offline depot (`offlinedepot.site-a.vcf.lab`) with the VCF 9.1.1 binaries
  and the VKS image library
- the Holorouter (DNS, NTP and routing), Technitium DNS, a Vault certificate
  authority (`ca.vcf.lab`) and authentik (`auth.vcf.lab`), the identity
  provider for the Aria and VCF Automation logins
- installation media pre-staged in `~/DeploymentMedia` on the console

The password for every system is `VMware123!VMware123!`. The vCenter SSO users
are `administrator@vsphere.local`, except for `vc-wld01-b`
(`administrator@wld.sso`).

### On the console

| Location | Contents | Source |
|---|---|---|
| `~/Lab Files` | Files the lab guides use (installer JSON specs, helper scripts) and the `Break-Fix Scripts` pack | this repository, `lab-files/`, copied at every startup |
| `~/Lab Build` | Build and instructor tools: automated Region A upgrades, vSAN HCL files, Region B bring-up spec, VKS demo source, Supervisor certificates, archive | template, plus anything in this repository's `lab-build/` (copied at every startup); not for students |
| `~/DeploymentMedia` | Installation media (vCenter, ESX, VCF Installer bundles, VKS) | template only |
| Firefox bookmarks toolbar | HOL Admin, Region A and Region B folders for each upgrade stage, VCF Installer | this repository, `console/bookmarks.json`, imported at every startup |

## How startup and shutdown follow the upgrades

`config.ini` describes the lab as delivered, before any upgrade. Once attendees
have upgraded part of the lab, a shutdown and restart with that baseline would
go wrong: the new VCF 9 appliances would not be started, and the old Aria
Automation VM would be powered on with an IP address that now belongs to VCF
Automation 9.1. So the startup and shutdown first work out how far the lab has
been upgraded, and adjust their settings to match.

### Stage detection

`Startup/ate2704stage.py` connects to every ESX host directly (vCenter is not
running yet at that point of the startup), lists the VMs that exist, and reads
the ESX versions. Each stage is detected on its own:

| Stage | Detected by | What changes |
|---|---|---|
| `region_a_9` | VM `ops-a` or `license-server` (vSphere 9.1.1 and converge path), or `ops-01a` or `license-a` (VVF path) | start the license server, VCF Operations and its cloud proxy, whichever names exist; wait for that VCF Operations UI; shut them down in the right phases |
| `region_a_vsp` | VMs `vsp-01a-*` (VCF Installer path) | start the Region A VCF services runtime (control plane node, 5-minute pause, then the workers, KB 440862); stop it gracefully before shutdown (KB 440874) |
| `region_a_vcf` | VM `sddcmanager-a` (converged to VCF) | start and stop NSX Manager `nsx-mgmt-01a` and SDDC Manager `sddcmanager-a`; check them |
| `region_a_vpc` | `user-vm-a`/`user-vm-b` no longer on `user-network-a` (optional overlay chapter) | drop the 10.1.11.x ping and SSH checks; the test VMs now have DHCP addresses on VPC subnets |
| `region_b_9` | VM `ops-01b` or `vsp-01b-*` | start the Region B license server, services runtime (KB 440862 order), VCF Operations and its cloud proxy; wait for VCF Operations and the identity broker; stop the runtime gracefully before shutdown |
| `automation_9` | VMs `auto-int-b-*` | never start the old `auto-01b` VM; start VCF Automation 9.1 through the Fleet lifecycle service once the runtime is back (KB 444635); power it off the same way at shutdown |
| `wld_9` | ESX 9.x on `esx-05b` to `esx-08b` | reported only |
| (any) | a VM no longer exists | removed from the start/stop lists, with its port and URL checks (for example vIDM and Aria Suite Lifecycle after they are retired) |

A lab that has not been upgraded gets no changes. If detection fails, the
baseline `config.ini` is used, as before.

### Startup

1. **`Startup/VCF.py`** (wraps the core module) runs as soon as the ESX hosts
   answer. It detects the stage, applies the changes to the running settings and
   to `/tmp/config.ini` (the original is kept as `/tmp/config.ini.base`), copies
   `lab-files/` to the console, then runs the core `VCF.py` unchanged.
2. The core modules start the VMs from the adjusted lists: vCenters, NSX
   Managers and Edges, then the remaining VMs, with the services runtime nodes
   in the order Broadcom documents.
3. **`Startup/VCFfinal.py`** (replaces the core module) reports the stage, the
   `wld01-b` import and upgrade state and the Supervisor. It heals the Region B
   Edge tunnels if needed, reconnects any VKS or Supervisor VM whose NSX
   network adapter failed at power-on, starts the Aria Automation 8 services or, after the
   upgrade, VCF Automation 9.1 through Fleet. It then waits for the Supervisor,
   its services, the VKS cluster and the VCF 9 user interfaces before the lab
   reports Ready.

The detected stage and every setting change are logged at the start of the
startup log, and saved in `/tmp/ate2704-stage.json` on the Manager. To see them
at any time without changing anything:

```bash
python3 /vpodrepo/2027-labs/2704/Startup/ate2704stage.py
```

### Shutdown

Run the lab's own wrapper, not the core script; [SHUTDOWN.MD](SHUTDOWN.MD) has
the full procedure:

```bash
python3 /vpodrepo/2027-labs/2704/Shutdown/Shutdown.py --dry-run   # preview
python3 /vpodrepo/2027-labs/2704/Shutdown/Shutdown.py             # shut down
```

It detects the stage and writes the matching settings for the core shutdown.
Before Aria Automation is upgraded, it stops the Aria Automation 8 services
first. After the upgrade, it powers VCF Automation off through Fleet and waits
for that to finish. It then stops each VCF services runtime gracefully, and
finally runs the core shutdown.

## Repository layout

```plain
ATE-2704/
├── README.md                  This file
├── config.ini                 Lab configuration: the pre-upgrade baseline
├── LAB-TEAM-CHANGES.md        Change log and design notes for the HOL core / labs team
├── ATE-2704-lab-team-changes.txt  Earlier (2026-11-04) version of the change notes
├── SHUTDOWN.MD                Shutdown procedure
├── Startup/
│   ├── ate2704stage.py        Upgrade-stage detection and the settings for each stage
│   ├── ate2704labfiles.py     Copies lab-files/ and lab-build/ to the console
│   ├── ate2704sddcperms.py    Fixes SDDC Manager file permissions (precheck)
│   ├── VCF.py                 Wrapper around the core VCF.py (stage + lab files)
│   └── VCFfinal.py            Lab-specific replacement for the core VCFfinal.py
├── Shutdown/
│   └── Shutdown.py            Stage-aware shutdown wrapper
├── lab-files/                 Mirror of the console's ~/Lab Files (copied at every startup)
│   ├── *.json, *.sh, *.py, *.pem    Files the lab guides use
│   └── Break-Fix Scripts/     Student break/fix exercise pack (PowerShell)
├── lab-build/                 Added to the console's ~/Lab Build (copied at every startup)
│   └── RegionA/custom_vsan_esa_hcl_9.1.json   vSAN ESA HCL for 9.1 (23 MB, about 1.2 MB in git)
├── console/
│   ├── bookmarks.json         Console Firefox bookmarks (imported at every startup)
│   └── VMware.config          Lab Status panel position
├── holorouter/
│   └── startlist              Domains allowed during startup only (template default)
├── scripts/
│   └── setup-demo.sh          HOLFY27 template example, not used
├── 91c7.config.ini            Reference: config.ini of another VCF 9.1 lab (not used)
├── 91ss.config.ini            Reference: config.ini of another VCF 9.1 lab (not used)
└── AGENTS.md                  Guidance for AI coding assistants
```

The repository uses the HOLFY27 (FY2027) vPodRepo format. At boot,
`labstartup.sh` on the Manager clones or pulls it to `/vpodrepo/2027-labs/2704`
(development vApps pull the `dev` branch), copies `config.ini` to
`/tmp/config.ini`, and uses the `Startup/` modules in place of the core ones.

## Making changes

| To change | Edit | Takes effect |
|---|---|---|
| A script or file students use in `~/Lab Files` | `lab-files/` (add, change or move files; nothing on the console is deleted) | next startup of any lab |
| A build or instructor file in `~/Lab Build` | `lab-build/` (same rules) | next startup of any lab |
| The console bookmarks | Edit them in Firefox, then Bookmarks > Manage Bookmarks > Import and Backup > Backup, and replace `console/bookmarks.json` | next startup |
| What starts and is checked before upgrades | `config.ini` | next startup |
| What starts and is checked after an upgrade | `OVERLAY` in `Startup/ate2704stage.py` | next startup and shutdown |
| Anything only the template has (`~/DeploymentMedia`, `~/Lab Build`, VMs) | the template | after re-mastering |

Keep maintainer notes out of `lab-files/`, because everything in it lands in
the students' folder.

### Testing

- Watch a startup with the status page (`/lmchol/home/holuser/startup-status.htm`)
  or `ltail`, which tails `labstartup.log` on the Manager.
- Preview the stage changes with `python3 Startup/ate2704stage.py`, the lab
  files copy with `python3 Startup/ate2704labfiles.py --dry-run`, and the
  shutdown with `Shutdown/Shutdown.py --dry-run`, all on the Manager.
- A dry run of a core startup module still updates the console's lab status.
  Set it back to Ready afterwards.

## Further reading

- [LAB-TEAM-CHANGES.md](LAB-TEAM-CHANGES.md): what changed, why, and how it was
  tested
- [SHUTDOWN.MD](SHUTDOWN.MD): shutdown procedure
- HOLFY27 core documentation (`HOLFY27-MGR-DOCS`): vPodRepo conventions, startup
  modules and `config.ini` options
- Contact the HOL Core Team for repository, startup, firewall or proxy questions.
