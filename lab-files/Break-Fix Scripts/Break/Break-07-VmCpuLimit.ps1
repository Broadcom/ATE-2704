# Restrictive 100 MHz VM CPU limit
# Guest becomes sluggish despite apparent host CPU headroom.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param([pscredential]$Credential, [switch]$AllowMultiple)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabFaults.psm1') -Force
Invoke-LabFault -Id '07-VmCpuLimit' -Action 'Break' -Credential $Credential -AllowMultiple:$AllowMultiple -ApprovalContext $PSCmdlet
