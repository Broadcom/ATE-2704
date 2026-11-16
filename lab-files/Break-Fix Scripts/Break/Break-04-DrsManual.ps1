# DRS changed to manual
# Automatic placement and evacuation require intervention.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param([pscredential]$Credential, [switch]$AllowMultiple)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabFaults.psm1') -Force
Invoke-LabFault -Id '04-DrsManual' -Action 'Break' -Credential $Credential -AllowMultiple:$AllowMultiple -ApprovalContext $PSCmdlet
