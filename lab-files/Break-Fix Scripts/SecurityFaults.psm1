Set-StrictMode -Version Latest
$ErrorActionPreference='Stop'
$script:PackRoot=$PSScriptRoot
function Invoke-LabSecurityFault {
 param([ValidateSet('11-NsxAdminPasswordExpired','12-VcenterCertificateTrust')][string]$Id,
       [ValidateSet('Break','Fix','Inspect')][string]$Action,
       [pscredential]$Credential,[pscredential]$GuestCredential,$ApprovalContext)
 $config=Get-Content (Join-Path $script:PackRoot 'LabConfig.json') -Raw | ConvertFrom-Json -AsHashtable
 $s=@($config.Scenarios | Where-Object Id -eq $Id)
 if($s.Count -ne 1){throw 'Unknown security exercise'}
 $s=$s[0]
 if($Action -ne 'Inspect' -and -not $ApprovalContext.ShouldProcess($s.Target,"$Action lab fault $Id ($($s.Title))")){return}
 if(-not $Credential){$Credential=Get-Credential -UserName $config.Vcenters[$s.Scope].User -Message "vCenter SSO credential for $($s.Scope)"}
 if(-not $GuestCredential){$GuestCredential=Get-Credential -UserName root -Message "Separate root credential for $($s.Target); required for recovery"}
 if($GuestCredential.UserName -ne 'root'){throw 'The appliance credential must be for root.'}
 $payload=@{id=$Id;action=$Action;user=$Credential.UserName;password=$Credential.GetNetworkCredential().Password;guest_password=$GuestCredential.GetNetworkCredential().Password}
 $start=[Diagnostics.ProcessStartInfo]::new()
 $start.FileName='python3'
 $start.ArgumentList.Add((Join-Path $script:PackRoot 'Helpers/security_faults.py'))
 $start.UseShellExecute=$false;$start.RedirectStandardInput=$true;$start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
 $process=[Diagnostics.Process]::new();$process.StartInfo=$start
 try {
  [void]$process.Start()
  $stdout=$process.StandardOutput.ReadToEndAsync();$stderr=$process.StandardError.ReadToEndAsync()
  $process.StandardInput.Write(($payload | ConvertTo-Json -Compress));$process.StandardInput.Close()
  $payload.password=$null;$payload.guest_password=$null
  $process.WaitForExit()
  $output=$stdout.GetAwaiter().GetResult();$errorText=$stderr.GetAwaiter().GetResult()
  if($output){$output.Trim() | Write-Output}
  if($process.ExitCode -ne 0){throw "Security exercise failed. $errorText"}
 } finally {$payload=$null;$process.Dispose()}
}
Export-ModuleMember -Function Invoke-LabSecurityFault
