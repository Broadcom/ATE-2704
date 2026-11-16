# Called privately by security_faults.py. Credentials and keys arrive on stdin.
$ErrorActionPreference='Stop'
$ProgressPreference='SilentlyContinue'
$c=[Console]::In.ReadToEnd() | ConvertFrom-Json
Import-Module VMware.PowerCLI.VCenter -ErrorAction Stop
Set-PowerCLIConfiguration -Scope Session -InvalidCertificateAction Fail -ParticipateInCEIP:$false -WebOperationTimeoutSeconds 600 -Confirm:$false | Out-Null
$secure=ConvertTo-SecureString $c.password -AsPlainText -Force
$vi=Connect-VIServer -Server $c.server -Credential ([pscredential]::new($c.user,$secure)) -ErrorAction Stop
try {
 if($vi.InstanceUuid -ne $c.uuid){throw 'vCenter identity mismatch'}
 switch($c.action){
  'Inspect' {
   foreach($name in 'Get-VIMachineCertificate','Set-VIMachineCertificate','Add-VITrustedCertificate','Remove-VITrustedCertificate'){Get-Command $name -ErrorAction Stop | Out-Null}
   $m=Get-VIMachineCertificate -VCenterOnly -Server $vi
   @{certificateSha256=[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($m.Certificate.RawData)).ToLowerInvariant();cmdletsAvailable=$true}|ConvertTo-Json -Compress
  }
  'Trust' {Add-VITrustedCertificate -VCenterOnly -Server $vi -PemCertificateOrChain $c.root -Confirm:$false | Out-Null}
  'Install' {Set-VIMachineCertificate -Server $vi -PemCertificate $c.cert -PemKey $c.key -Confirm:$false | Out-Null}
  'RemoveRoot' {
   $roots=@(Get-VITrustedCertificate -VCenterOnly -Server $vi | Where-Object {[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($_.Certificate.RawData)).ToLowerInvariant() -eq $c.rootSha256})
   if($roots.Count -gt 1){throw 'Unexpected duplicate exercise CA roots'}
   $roots | Remove-VITrustedCertificate -Server $vi -Confirm:$false | Out-Null
   $remaining=@(Get-VITrustedCertificate -VCenterOnly -Server $vi | Where-Object {[Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($_.Certificate.RawData)).ToLowerInvariant() -eq $c.rootSha256})
   if($remaining.Count){throw 'Exercise CA cleanup did not verify'}
  }
  default {throw 'Unknown certificate action'}
 }
} finally {Disconnect-VIServer -Server $vi -Confirm:$false -ErrorAction SilentlyContinue | Out-Null}
