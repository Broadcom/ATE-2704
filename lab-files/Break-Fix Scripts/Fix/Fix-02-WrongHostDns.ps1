# ESXi DNS points to an invalid resolver
# Hostname resolution and DNS-related readiness checks fail on one host.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='Medium')]
param([pscredential]$Credential, [switch]$AllowMultiple)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabFaults.psm1') -Force
Invoke-LabFault -Id '02-WrongHostDns' -Action 'Fix' -Credential $Credential -AllowMultiple:$AllowMultiple -ApprovalContext $PSCmdlet
