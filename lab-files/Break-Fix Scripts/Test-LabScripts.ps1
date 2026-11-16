[CmdletBinding()]
param([switch]$ReadOnly, [pscredential]$RegionACredential, [pscredential]$WorkloadBCredential, [pscredential]$NsxCredential, [pscredential]$RegionARootCredential, [pscredential]$ManagementBRootCredential, [pscredential]$ManagementBCredential)
$ErrorActionPreference='Stop'
$files=@(Get-ChildItem $PSScriptRoot -Recurse -File | Where-Object Extension -in '.ps1','.psm1')
foreach($file in $files){
 $tokens=$null; $errors=$null
 [Management.Automation.Language.Parser]::ParseFile($file.FullName,[ref]$tokens,[ref]$errors)|Out-Null
 if($errors.Count){throw "PowerShell parse failure: $($file.Name): $($errors.Message -join '; ')"}
}
Import-Module (Join-Path $PSScriptRoot 'LabFaults.psm1') -Force
Import-Module (Join-Path $PSScriptRoot 'SecurityFaults.psm1') -Force
foreach($s in Get-LabScenarios){
 & (Join-Path $PSScriptRoot ('Break/Break-'+$s.Id+'.ps1')) -WhatIf
 & (Join-Path $PSScriptRoot ('Fix/Fix-'+$s.Id+'.ps1')) -WhatIf
 if($ReadOnly){
  $credential=switch($s.Scope){RegionA {$RegionACredential}; WorkloadB {$WorkloadBCredential}; ManagementB {$ManagementBCredential}; NSX {$NsxCredential}}
  if($s.PSObject.Properties['Backend'] -and $s.Backend -eq 'Security'){
   $rootCredential=if($s.Scope -eq 'RegionA'){$RegionARootCredential}else{$ManagementBRootCredential}
   Invoke-LabSecurityFault -Id $s.Id -Action Inspect -Credential $credential -GuestCredential $rootCredential
  }else{Invoke-LabFault -Id $s.Id -Action Break -Credential $credential -Inspect}
 }
}
[pscustomobject]@{PowerShellFilesParsed=$files.Count;Scenarios=@(Get-LabScenarios).Count;ReadOnly=$ReadOnly.IsPresent;FaultsExecuted=$false}
