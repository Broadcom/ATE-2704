STUDENT BREAK / FIX PACK

Location: Console VM "~/Lab Files/Break-Fix Scripts"
Requires PowerShell 7 and the installed VMware PowerCLI modules.
The faults have NOT been executed while preparing this pack.

QUICK START
  pwsh ./Get-LabScenarios.ps1
  pwsh ./Test-LabScripts.ps1
  pwsh ./Break/Break-03-VmotionDisabled.ps1 -WhatIf
  pwsh ./Break/Break-03-VmotionDisabled.ps1
  pwsh ./Fix/Fix-03-VmotionDisabled.ps1
  pwsh ./Get-LabFaultState.ps1

Students run the named Break script from the lab manual, investigate and repair
the fault, and use the matching Fix script if they need recovery assistance.
Run these exercises after deployments and upgrades have settled.
Break scripts ask for confirmation. -WhatIf previews without connecting or
changing state. -Confirm:$false suppresses confirmation when deliberately
launching an exercise. Credentials are prompted and are never saved.

Use ONE active fault at a time. -AllowMultiple is an explicit instructor
option for independent exercises; it does not permit a second run of the
same active fault. Never run Break and Fix concurrently.

RECOVERY
Each Break script writes its original settings to State/<ID>.json BEFORE
changing anything. Keep this directory: the Fix script restores those saved
settings, not guessed defaults. An interrupted or partly failed Break can
still be recovered with its paired Fix. Changes are checked after applying.
BGP, clocks and dependent services can take longer to recover than the setting.

Fix changes only the fields owned by that exercise. Unrelated student edits
are preserved. If students delete/recreate a VM or NIC, identity checks refuse
to target a replacement. Revalidate LabConfig.json after retemplating,
replacing appliances or rotating the NSX certificate. Missing state is an
error, not a reason to reset the whole environment.

The snapshot repair removes ONLY the snapshot created by that exercise and
merges its data; it never reverts a snapshot or removes all snapshots.

NSX BGP recovery uses the management endpoint (10.2.1.24), which does not
depend on the workload Tier-0's BGP. Scenario 10 deliberately interrupts
workload and Supervisor load-balancer access. Use the Console VM for recovery.
Scenario 09 reduces uplink redundancy and preserves TEP VLAN 48.

CERTIFICATES
PowerCLI uses strict certificate checking and the supplied public lab CA in
this process. NSX HTTPS uses the exact SHA-256 certificate pin independently
verified during deployment, checks validity dates, and allows only the named
NSX host. No global certificate-validation bypass is installed.

VALIDATION
Test-LabScripts.ps1 parses every PowerShell file and runs every pair with
-WhatIf. -ReadOnly additionally inspects actual targets and original values;
it still does not create faults. The accompanying validation receipt states
what was tested. A full break/fix cycle has not been run on the live lab.

SCENARIOS
| `01-NtpStopped` | ESXi NTP service stopped | `esx-04a.site-a.vcf.lab` | One host loses time synchronization; drift develops over time, not immediately. |
| `02-WrongHostDns` | ESXi DNS points to an invalid resolver | `esx-04a.site-a.vcf.lab` | Hostname resolution and DNS-related readiness checks fail on one host. |
| `03-VmotionDisabled` | vMotion disabled on its VMkernel interface | `esx-04a.site-a.vcf.lab` | Migrations involving this host and upgrade evacuation can fail. |
| `04-DrsManual` | DRS changed to manual | `cluster-mgmt-01a` | Automatic placement and evacuation require intervention. |
| `05-VmNicDisconnected` | Disposable VM NIC disconnected | `user-vm-a` | user-vm-a loses connectivity immediately and after power cycling. |
| `06-WrongVmNetwork` | Disposable VM attached to the wrong portgroup | `user-vm-b` | The NIC stays connected but the static guest address is on the wrong VLAN. |
| `07-VmCpuLimit` | Restrictive 100 MHz VM CPU limit | `user-vm-a` | Guest becomes sluggish despite apparent host CPU headroom. |
| `08-LeftoverSnapshot` | Snapshot left for pre-upgrade hygiene review | `user-vm-b` | A new disk-only snapshot appears. This is a hygiene exercise, not a universal hardware-upgrade blocker. |
| `09-EdgeUplinkVlanMissing` | BGP uplink VLAN missing from Edge trunk | `edge-uplink1-vds01-wld01-01b` | VLAN 49 is removed from uplink 1; TEP VLAN 48 and uplink 2 remain. Expect two external BGP sessions down. |
| `10-WorkloadBgpDisabled` | Workload Tier-0 BGP disabled | `t0-wld01-b` | HIGH IMPACT: all four external BGP sessions drop. Workload north-south and Supervisor load-balancer access fail. Recover through the management NSX endpoint. |

STUDENT INVESTIGATION HINTS
01: ESXi time settings, NTP service policy/running state, configured peers.
02: ESXi DNS configuration and forward/reverse name resolution.
03: VMkernel service bindings, migration compatibility and evacuation errors.
04: Cluster DRS mode and unapplied migration recommendations.
05: VM NIC Connected/Connect at power on, then guest addressing.
06: Compare portgroup, VLAN, guest IP/subnet and gateway; link-up alone is insufficient.
07: VM resource allocation limits and CPU ready/usage; host headroom alone is insufficient.
08: Snapshot inventory, owner/purpose/age and consolidation planning. This is hygiene, not a universal hard upgrade blocker.
09: Correlate BGP neighbor state, VLAN 49, Edge data NICs and allowed trunk VLANs.
10: Check Tier-0 global BGP administrative state before investigating neighbor authentication or router reachability.

ADDITIONAL STUDENT SECURITY EXERCISES

11 - NSX ADMIN PASSWORD EXPIRED
  ./Break/Break-11-NsxAdminPasswordExpired.ps1 -WhatIf
  ./Break/Break-11-NsxAdminPasswordExpired.ps1
  ./Fix/Fix-11-NsxAdminPasswordExpired.ps1

Target: nsx-mgmt-01b.site-b.vcf.lab (10.2.1.21), the VCF 5.2.1
management-domain NSX Manager, admin account only. Cluster VIP: 10.2.1.20.
Prompts: vc-mgmt-b.site-b.vcf.lab SSO credential (administrator@vsphere.local),
then the management NSX appliance root credential.
The Break script sets the native node-user API password_reset_required flag.
This forces immediate password expiry/change-required behavior; it does not
change the password, backdate the appliance clock, or edit Linux password files.
Admin UI/API access may fail or require a password change. Existing data-plane
forwarding is not intentionally changed. SDDC Manager password checks and
operations using the NSX admin credential can fail until the account is repaired.
Do not run during VCF management operations or an upgrade.

Recovery uses root through VMware Tools and the Manager's loopback node API,
the same local transport used by nsxcli. It does not need the expired admin
login and does not enable SSH root login. Fix clears the forced-expiry flag;
it preserves a password that the student has already changed, and does not
rewind it to an old password. Password policy settings are preserved. If a
student rotates the password, update any affected integrations separately.
This local API transport is checked against NSX 4.2.1. Revalidate after upgrade.

12 - VCENTER CERTIFICATE TRUST FAULT
  ./Break/Break-12-VcenterCertificateTrust.ps1 -WhatIf
  ./Break/Break-12-VcenterCertificateTrust.ps1
  ./Fix/Fix-12-VcenterCertificateTrust.ps1

Target: vc-mgmt-a.site-a.vcf.lab (Region A), Machine SSL certificate.
Prompts: Region A vCenter SSO credential, then that VCSA's root credential.
The script backs up the ORIGINAL certificate and key in a root-only directory
/root/lab-faults/<RunId> on VCSA. The key is not saved in the Console State folder.
It creates a temporary exercise CA and a correctly named, currently valid
Machine SSL certificate. The CA is trusted by vCenter's own certificate store,
then the leaf certificate is installed with the native Set-VIMachineCertificate
cmdlet. Normal lab clients do not trust this CA, causing an actual TLS trust
failure. This is an untrusted-issuer exercise, NOT an expired-certificate test.

Students investigate the browser/API error, issuer, SANs, validity dates and
certificate chain, then restore the standards-compliant certificate using the
appliance-local backup and native certificate management. Fix automates this:
it uses a private trust bundle restricted to the recorded exercise CA, verifies
the expected live certificate and vCenter UUID, restores the original certificate
and key, verifies HTTPS using the original lab CA, then removes ONLY the unique
exercise CA from vCenter. It does not disable TLS checks or add global client trust.
The temporary CA signing key is not retained. Recovery leaves the original
root-only appliance backup available for instructor use.

If the student installs a different certificate, Fix refuses to overwrite it;
inspect that certificate and its compliance with the standards first. If VCSA
services are unavailable, use its VM console and the saved appliance backup
with the native certificate manager. The automated Fix needs the vCenter API
and VMware Tools to remain available. Keep State/<ID>.json for the RunId/path.

The certificate exercise requires standalone SSO topology and vCenter 8.0.3.
It refuses linked-mode topology and other releases until revalidated. Certificate
changes can disrupt vCenter clients and integrations; running VMs remain running.
Run this after deployments and upgrades have settled. Use only one active fault.

The security PowerShell entry points use Python 3, requests, pyVmomi and
cryptography already installed on the Console, plus its existing PowerCLI modules.
Test-LabScripts.ps1 -ReadOnly accepts RegionARootCredential and
ManagementBRootCredential for these extra appliance checks; otherwise it prompts.
Use -ManagementBCredential for the VCF 5.2.1 management vCenter SSO credential.
Offline certificate-generation and recovery-boundary tests:
  python3 ./Tests/test_security_faults.py

References:
  Native NSX node-user API (password_reset_required):
  https://developer.broadcom.com/xapis/nsx-t-data-center-rest-api/latest/method_UpdateNodeUser.html
  Native vCenter Machine SSL replacement:
  https://developer.broadcom.com/powercli/latest/vmware.vimautomation.core/commands/set-vimachinecertificate
  Native trusted-root management:
  https://developer.broadcom.com/powercli/latest/vmware.vimautomation.core/commands/add-vitrustedcertificate
  https://developer.broadcom.com/powercli/latest/vmware.vimautomation.core/commands/remove-vitrustedcertificate
