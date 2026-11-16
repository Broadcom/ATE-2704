# Disposable VM NIC disconnected
# user-vm-a loses connectivity immediately and after power cycling.
[CmdletBinding(SupportsShouldProcess=$true, ConfirmImpact='High')]
param([pscredential]$Credential, [switch]$AllowMultiple)
Import-Module (Join-Path (Split-Path $PSScriptRoot -Parent) 'LabFaults.psm1') -Force
Invoke-LabFault -Id '05-VmNicDisconnected' -Action 'Break' -Credential $Credential -AllowMultiple:$AllowMultiple -ApprovalContext $PSCmdlet
