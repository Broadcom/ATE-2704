# VCF 5.2.1 management NSX: nsx-mgmt-01b (10.2.1.21), via vc-mgmt-b.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param([pscredential]$Credential,[pscredential]$GuestCredential)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'SecurityFaults.psm1') -Force
Invoke-LabSecurityFault -Id '11-NsxAdminPasswordExpired' -Action Fix -Credential $Credential -GuestCredential $GuestCredential -ApprovalContext $PSCmdlet
