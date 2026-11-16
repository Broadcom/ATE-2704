# Student exercise: 12-VcenterCertificateTrust. Does not execute until explicitly invoked.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param([pscredential]$Credential,[pscredential]$GuestCredential)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'SecurityFaults.psm1') -Force
Invoke-LabSecurityFault -Id '12-VcenterCertificateTrust' -Action Fix -Credential $Credential -GuestCredential $GuestCredential -ApprovalContext $PSCmdlet
