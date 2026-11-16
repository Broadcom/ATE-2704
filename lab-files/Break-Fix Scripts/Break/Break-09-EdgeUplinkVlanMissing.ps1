# BGP uplink VLAN missing from Edge trunk
# VLAN 49 is removed from uplink 1; TEP VLAN 48 and uplink 2 remain. Expect two external BGP sessions down.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param([pscredential]$Credential, [switch]$AllowMultiple)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabFaults.psm1') -Force
Invoke-LabFault -Id '09-EdgeUplinkVlanMissing' -Action 'Break' -Credential $Credential -AllowMultiple:$AllowMultiple -ApprovalContext $PSCmdlet
