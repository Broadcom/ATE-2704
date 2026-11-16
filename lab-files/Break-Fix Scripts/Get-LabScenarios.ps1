Import-Module (Join-Path $PSScriptRoot 'LabFaults.psm1') -Force
Get-LabScenarios | Select-Object Id, Title, Scope, Target, Impact | Format-Table -Wrap
