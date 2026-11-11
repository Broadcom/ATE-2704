# Partner Advanced Workshop: Upgrades

## About the lab

ATE-2704 is a two-region lab that starts on the VMware vSphere 8 and VMware
Cloud Foundation (VCF) 5.2.1 releases, built so attendees can upgrade it to
VCF 9.1.1. **Region A** (`site-a.vcf.lab`) is a standalone vSphere 8.0 U3
cluster: vCenter `vc-mgmt-a` and four ESX hosts on vSAN, with a VCF Installer
appliance ready for the upgrade. **Region B** (`site-b.vcf.lab`) has two parts:

- A VCF 5.2.1 management domain, `mgmt-b`. It has SDDC Manager, vCenter 8.0 U3,
  NSX 4.2.1 with an Edge cluster, and four ESX hosts on vSAN ESA. Aria Suite
  Lifecycle 8.18, VMware Identity Manager 3.3.7 and Aria Automation 8.18.1 run
  on it. Authentik is the identity provider.
- A standalone vSphere 8.0 U3i workload cluster, `vc-wld01-b`, with its own
  NSX 4.2.1 and Edges. It runs a Supervisor with Contour, Harbor, the Local
  Consumption Interface and the Argo CD Service. A VKS cluster (`vks-01`) runs a
  sample voting app, which Argo CD deploys from a local Gitea repository.

Shared services make the lab self-contained, with no internet access needed:

- An offline depot holds the VCF 9.1.1 binaries and the VKS image library.
- Technitium DNS and a Vault certificate authority run alongside the regions.
- Installation media is pre-staged in `~/DeploymentMedia` on the console.

## Lab goals

The workshop teaches partners to plan and run real-world upgrades to VCF 9.1.1
in the order Broadcom requires, and to recognise and work around the problems
they will meet in the field. Attendees:

1. **Region A:** upgrade the vSphere 8 environment to VMware vSphere Foundation
   (VVF) 9.1.1.
2. **Region B management domain:** upgrade VCF 5.2.1 to 9.1.1. They deploy VCF
   Operations, upgrade SDDC Manager, and install the new VCF management
   services. Then they upgrade NSX, vCenter (with a Reduced Downtime Upgrade),
   the ESX hosts and the Edges from VCF Operations.
3. **Aria Automation:** upgrade Aria Automation 8.18.1 to VCF Automation 9.1.1.
4. **`vc-wld01-b`:** import the existing vCenter into VCF as a workload domain.
   Then upgrade its Supervisor, NSX, vCenter, hosts and Edges, and finally the
   VKS cluster and its application.

The lab template stays at the pre-upgrade baseline, so every class starts from
the same point.

## HOLFY27 vPodRepo format

This repository uses the HOLFY27 (FY2027) vPodRepo structure.

## Directory Structure

```plain
ATE-2704/
├── config.ini              # Lab configuration (required)
├── README.txt              # This file (required)
├── holorouter/             # Router configuration overrides
│   ├── allowlist           # Additional allowed domains
│   ├── iptablescfg.sh      # Custom firewall rules (optional)
│   └── startlist           # Startup-only allowed domains
├── console/                # Console desktop overrides (copied after the core files)
│   └── VMware.config       # Lab Status panel position
├── Startup/                # Startup module overrides
│   ├── prelim.py           # Override core prelim module (optional)
│   └── VCFfinal.py         # Override core VCFfinal module (optional)
├── scripts/                # Custom scripts
│   ├── setup-demo.sh       # Example bash script (optional)
│   └── configure-app.py    # Example Python script (optional)
├── ansible/                # Ansible playbooks (optional)
│   └── configure.yml       # Example playbook (optional)
└── salt/                   # Salt states (optional)
    └── config.sls          # Example salt state (optional)
```

## Configuration

### config.ini

The `config.ini` file defines lab parameters:

- **[VPOD]**: Core settings (SKU, labtype [should set to ATE], timeouts, conky title)
- **[RESOURCES]**: Components to verify (hosts, VMs, services)
- **[VCF]**: VCF-specific component names
- **[VCFFINAL]**: Post-startup tasks
- **[CUSTOM]**: Lab-specific key-value pairs

### Startup Module Overrides

Place Python files in `Startup/` to override core modules:

```python
# Startup/prelim.py - Example override
import lsfunctions as lsf

def main():
    lsf.write_output('Running lab-specific prelim tasks...')
    
    # Your custom initialization code here
    
    lsf.write_output('Lab-specific prelim complete')

if __name__ == '__main__':
    import sys
    sys.path.insert(0, '/home/holuser/hol')
    import lsfunctions as lsf
    lsf.init(router=False)
    main()
```

### Router Configuration

Files in `holorouter/`:

- **allowlist**: Additional domains to allow through proxy (merged with core)
- **startlist**: Domains needed only during startup (temporary access)
- **iptablescfg.sh**: Custom firewall rules (replaces core rules)

## Testing

1. In development vApp, git pulls `dev` branch automatically
2. Override files take effect on labstartup
3. Use status dashboard to monitor: `/lmchol/home/holuser/startup-status.htm` or `ltail` command to tail the labstartup.log file

## Lab Startup Flow

1. Manager VM boots, mounts console and vpodrepo
2. `labstartup.sh` clones/pulls this repository
3. `config.ini` copied to `/tmp/config.ini`
4. Router files pushed via NFS to holorouter
5. `labstartup.py` runs startup sequence using LabType loader
6. Override modules from `Startup/` used when present
7. DNS records imported from `new-dns-records.csv`
8. Status dashboard updated throughout

## Support

Contact the HOL Core Team for assistance with:

- Repository creation
- Custom startup requirements
- Firewall/proxy configuration
- Debugging startup issues
