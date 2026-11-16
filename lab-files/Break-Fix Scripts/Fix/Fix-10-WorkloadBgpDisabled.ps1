# Workload Tier-0 BGP disabled
# HIGH IMPACT: all four external BGP sessions drop. Workload north-south and Supervisor load-balancer access fail. Recover through the management NSX endpoint.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='Medium')]
param([pscredential]$Credential, [switch]$AllowMultiple)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabFaults.psm1') -Force
Invoke-LabFault -Id '10-WorkloadBgpDisabled' -Action 'Fix' -Credential $Credential -AllowMultiple:$AllowMultiple -ApprovalContext $PSCmdlet
