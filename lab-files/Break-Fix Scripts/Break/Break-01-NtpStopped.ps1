# ESXi NTP service stopped
# One host loses time synchronization; drift develops over time, not immediately.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param([pscredential]$Credential, [switch]$AllowMultiple)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabFaults.psm1') -Force
Invoke-LabFault -Id '01-NtpStopped' -Action 'Break' -Credential $Credential -AllowMultiple:$AllowMultiple -ApprovalContext $PSCmdlet
