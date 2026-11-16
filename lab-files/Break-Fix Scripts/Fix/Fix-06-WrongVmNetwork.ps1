# Disposable VM attached to the wrong portgroup
# The NIC stays connected but the static guest address is on the wrong VLAN.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='Medium')]
param([pscredential]$Credential, [switch]$AllowMultiple)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabFaults.psm1') -Force
Invoke-LabFault -Id '06-WrongVmNetwork' -Action 'Fix' -Credential $Credential -AllowMultiple:$AllowMultiple -ApprovalContext $PSCmdlet
