Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:Root = $PSScriptRoot
function Get-LabConfiguration { Get-Content (Join-Path $script:Root 'LabConfig.json') -Raw | ConvertFrom-Json -AsHashtable }
function Get-LabScenarios { (Get-LabConfiguration).Scenarios | ForEach-Object { [pscustomobject]$_ } }
function Save-LabState($Path, $State) {
    $temp = $Path + '.tmp'
    $State | ConvertTo-Json -Depth 40 | Set-Content -LiteralPath $temp -Encoding utf8
    if (-not $IsWindows) { & chmod 600 -- $temp }
    Move-Item -LiteralPath $temp -Destination $Path -Force
}
function Initialize-LabPinnedClient {
    # HttpClient that only trusts one certificate, identified by its SHA-256 hash.
    if (-not ('LabScripts.PinnedClient' -as [type])) {
        Add-Type -TypeDefinition @'
using System;
using System.Net.Http;
using System.Security.Cryptography;
namespace LabScripts {
 public static class PinnedClient {
  public static HttpClient Create(string host, string pin) {
   var h = new HttpClientHandler { UseProxy=false, AllowAutoRedirect=false };
   h.ServerCertificateCustomValidationCallback = (r,c,chain,errors) =>
    c != null && String.Equals(r.RequestUri.Host,host,StringComparison.OrdinalIgnoreCase) &&
    DateTime.UtcNow >= c.NotBefore.ToUniversalTime() && DateTime.UtcNow < c.NotAfter.ToUniversalTime() &&
    String.Equals(Convert.ToHexString(SHA256.HashData(c.RawData)),pin,StringComparison.OrdinalIgnoreCase);
   return new HttpClient(h) { Timeout=TimeSpan.FromSeconds(60) };
  }
 }
}
'@
    }
}
function Get-LabRootPassword($Context) {
    # Which password the host's root account accepts: 'Drift', 'Lab' or 'Unknown'.
    # ESXi locks root for 15 minutes after 5 failed logins, so each check tries the
    # password that is most likely right first and stops at the first success.
    $lab = $Context.Credential.GetNetworkCredential().Password
    $drift = [string]$Context.Scenario.DriftPassword
    $order = if ($Context.ContainsKey('ExpectDrift') -and $Context.ExpectDrift) { @(@('Drift',$drift), @('Lab',$lab)) } else { @(@('Lab',$lab), @('Drift',$drift)) }
    foreach ($o in $order) { if (Test-LabRootLogin $Context $o[1]) { return $o[0] } }
    return 'Unknown'
}
function Test-LabRootLogin($Context, [string]$pw) {
    # True when the host accepts root with this password. Uses basic auth on the host's
    # /folder page, trusting only the host certificate that vCenter holds for this host.
    Initialize-LabPinnedClient
    $name = $Context.Host.Name
    $pem = [Text.Encoding]::ASCII.GetString([byte[]]$Context.Host.ExtensionData.Config.Certificate)
    $der = [Security.Cryptography.X509Certificates.X509Certificate2]::CreateFromPem($pem).RawData
    $pin = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($der))
    $http = [LabScripts.PinnedClient]::Create($name, $pin)
    try {
        $req = [Net.Http.HttpRequestMessage]::new('GET', "https://$name/folder?dcPath=ha-datacenter")
        $req.Headers.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Basic', [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes("root:$pw")))
        $code = [int]$http.SendAsync($req).GetAwaiter().GetResult().StatusCode
        if ($code -eq 200) { return $true }
        if ($code -eq 401) { return $false }
        throw "Unexpected HTTP $code from $name while checking the root login."
    } finally { $http.Dispose() }
}
function Sync-LabSddcCredential($Scenario, [pscredential]$Credential) {
    # After the host password is back to the lab password, tell SDDC Manager the same password
    # (Password Management > Remediate). Needed when the student remediated SDDC Manager with the
    # drifted password in the meantime; harmless otherwise.
    if (-not $Scenario.ContainsKey('SddcManager') -or -not $Scenario.SddcManager) { return }
    $base = 'https://' + $Scenario.SddcManager
    $pw = $Credential.GetNetworkCredential().Password
    $tok = (Invoke-RestMethod -Method Post -Uri "$base/v1/tokens" -ContentType 'application/json' -Body (@{ username=$Credential.UserName; password=$pw } | ConvertTo-Json)).accessToken
    $h = @{ Authorization = "Bearer $tok" }
    $body = @{ operationType='REMEDIATE'; elements=@(@{ resourceName=$Scenario.Target; resourceType='ESXI'; credentials=@(@{ credentialType='SSH'; username='root'; password=$pw }) }) } | ConvertTo-Json -Depth 6
    $task = Invoke-RestMethod -Method Patch -Uri "$base/v1/credentials" -Headers $h -ContentType 'application/json' -Body $body
    for ($i = 0; $i -lt 60; $i++) {
        Start-Sleep -Seconds 10
        $t = Invoke-RestMethod -Uri "$base/v1/credentials/tasks/$($task.id)" -Headers $h
        if ($t.status -in 'SUCCESSFUL','FAILED','CANCELLED') { break }
    }
    if ($t.status -ne 'SUCCESSFUL') { Write-Warning "SDDC Manager credential remediation for $($Scenario.Target) ended $($t.status); check Security > Password Management." }
}
function Connect-LabTarget($Config, $Scenario, [pscredential]$Credential) {
    if ($Scenario.Kind -eq 'NsxBgp') {
        if (-not $Credential) { $Credential = Get-Credential -UserName 'admin' -Message 'NSX workload Manager credential' }
        Initialize-LabPinnedClient
        $uri = [uri]$Config.Nsx.BaseUri
        $http = [LabScripts.PinnedClient]::Create($uri.Host, $Config.Nsx.CertificateSha256)
        $bytes = [Text.Encoding]::UTF8.GetBytes($Credential.UserName + ':' + $Credential.GetNetworkCredential().Password)
        $http.DefaultRequestHeaders.Authorization = [Net.Http.Headers.AuthenticationHeaderValue]::new('Basic',[Convert]::ToBase64String($bytes))
        return @{ Http=$http; BaseUri=$Config.Nsx.BaseUri; Scenario=$Scenario; Config=$Config }
    }
    Import-Module VMware.VimAutomation.Core -ErrorAction Stop
    $ca = Join-Path $script:Root $Config.CertificateFile
    if (-not (Test-Path -LiteralPath $ca)) { throw "Lab CA certificate missing: $ca" }
    $env:SSL_CERT_FILE = $ca
    Set-PowerCLIConfiguration -Scope Session -InvalidCertificateAction Fail -ParticipateInCEIP:$false -Confirm:$false | Out-Null
    $target = $Config.Vcenters[$Scenario.Scope]
    $existingServers = Get-Variable -Name DefaultVIServers -Scope Global -ValueOnly -ErrorAction SilentlyContinue
    $vi = @($existingServers | Where-Object { $_.Name -eq $target.Server -and $_.IsConnected })
    $owned = $false
    if ($vi.Count -eq 0) {
        if (-not $Credential) { $Credential = Get-Credential -UserName $target.User -Message "Credential for $($target.Server)" }
        $vi = @(Connect-VIServer -Server $target.Server -Credential $Credential -ErrorAction Stop)
        $owned = $true
    }
    if ($vi.Count -ne 1 -or $vi[0].InstanceUuid -ne $target.InstanceUuid) { throw 'vCenter identity mismatch; refusing lab mutation.' }
    $ctx = @{ VI=$vi[0]; Owned=$owned; Scenario=$Scenario; Config=$Config; Credential=$Credential }
    switch ($Scenario.Kind) {
        { $_ -in 'Ntp','Dns','Vmotion','HostPassword','Maintenance' } {
            $ctx.Host = Get-VMHost -Name $Scenario.Target -Server $ctx.VI -ErrorAction Stop
            # The maintenance-mode exercise must still reach its host while it is in maintenance mode.
            $okStates = if ($Scenario.Kind -eq 'Maintenance') { @('Connected','Maintenance') } else { @('Connected') }
            if ([string]$ctx.Host.ConnectionState -notin $okStates) { throw 'Target host is not connected.' }
            if ($Scenario.Kind -eq 'Vmotion') {
                $ctx.Vmk = @(Get-VMHostNetworkAdapter -VMHost $ctx.Host -VMKernel | Where-Object { $_.Name -eq $Scenario.Interface })
                if ($ctx.Vmk.Count -ne 1) { throw 'Expected exactly one configured vMotion VMkernel interface.' }
                $ctx.Vmk = $ctx.Vmk[0]
            }
        }
        'Drs' { $ctx.Cluster = Get-Cluster -Name $Scenario.Target -Server $ctx.VI -ErrorAction Stop }
        'EdgeVlan' {
            $dvs = Get-VDSwitch -Name $Scenario.Switch -Server $ctx.VI -ErrorAction Stop
            if ($dvs.ExtensionData.Uuid -ne $Scenario.SwitchUuid) { throw 'Workload VDS identity mismatch.' }
            $ctx.Pg = Get-VDPortgroup -Name $Scenario.Target -VDSwitch $dvs -ErrorAction Stop
        }
        default {
            $ctx.VM = Get-VM -Name $Scenario.Target -Server $ctx.VI -ErrorAction Stop
            if ($ctx.VM.ExtensionData.Config.InstanceUuid -ne $Scenario.VmUuid) { throw 'Disposable VM identity mismatch.' }
            if ($Scenario.Kind -in 'NicDisconnected','NicWrongPg') {
                $nics = @(Get-NetworkAdapter -VM $ctx.VM -Server $ctx.VI)
                if ($nics.Count -ne 1) { throw 'This scenario requires exactly one NIC on the disposable VM.' }
                $ctx.Nic = $nics[0]
            }
        }
    }
    return $ctx
}
function Invoke-LabNsx($Context, $Method, $Body=$null) {
    $path = $Context.Config.Nsx.BgpPath
    $req = [Net.Http.HttpRequestMessage]::new([Net.Http.HttpMethod]::new($Method), $Context.BaseUri + $path)
    try {
        if ($null -ne $Body) { $req.Content = [Net.Http.StringContent]::new(($Body | ConvertTo-Json -Depth 50),[Text.Encoding]::UTF8,'application/json') }
        $resp = $Context.Http.SendAsync($req).GetAwaiter().GetResult()
        try {
            if (-not $resp.IsSuccessStatusCode) { throw "NSX request failed with HTTP $([int]$resp.StatusCode). Credentials and response body withheld." }
            $text = $resp.Content.ReadAsStringAsync().GetAwaiter().GetResult()
            if ($text) { return ($text | ConvertFrom-Json -AsHashtable) }
        } finally { $resp.Dispose() }
    } finally { $req.Dispose() }
}
function Get-LabSettings($Context) {
    $s = $Context.Scenario
    switch ($s.Kind) {
        'Ntp' {
            $svc = @(Get-VMHostService -VMHost $Context.Host | Where-Object Key -eq 'ntpd')
            if ($svc.Count -ne 1) { throw 'NTP service not found uniquely.' }
            return @{ Running=[bool]$svc[0].Running; Policy=[string]$svc[0].Policy }
        }
        'HostPassword' {
            $h = Get-AdvancedSetting -Entity $Context.Host -Name 'Security.PasswordHistory'
            return @{ History=[int]$h.Value; RootPassword=(Get-LabRootPassword $Context) }
        }
        'Maintenance' {
            $h = Get-VMHost -Id $Context.Host.Id -Server $Context.VI
            return @{ State=[string]$h.ConnectionState }
        }
        'Dns' {
            $n = Get-VMHostNetwork -VMHost $Context.Host
            return @{ Addresses=@($n.DnsAddress); FromDhcp=[bool]$n.DnsFromDhcp; DhcpDevice=[string]$n.DnsDhcpDevice }
        }
        'Vmotion' {
            $n = Get-VMHostNetworkAdapter -VMHost $Context.Host -VMKernel | Where-Object Name -eq $s.Interface
            return @{ Enabled=[bool]$n.VMotionEnabled; Name=[string]$n.Name }
        }
        'Drs' {
            $cl = Get-Cluster -Id $Context.Cluster.Id -Server $Context.VI
            return @{ Automation=[string]$cl.DrsAutomationLevel; Enabled=[bool]$cl.DrsEnabled }
        }
        'NicDisconnected' {
            $n = Get-NetworkAdapter -VM $Context.VM | Where-Object Id -eq $Context.Nic.Id
            return @{ Connected=[bool]$n.ExtensionData.Connectable.Connected; StartConnected=[bool]$n.ExtensionData.Connectable.StartConnected; Mac=[string]$n.MacAddress; Id=[string]$n.Id }
        }
        'NicWrongPg' {
            $n = Get-NetworkAdapter -VM $Context.VM | Where-Object Id -eq $Context.Nic.Id
            return @{ Network=[string]$n.NetworkName; PgKey=[string]$n.ExtensionData.Backing.Port.PortgroupKey; SwitchUuid=[string]$n.ExtensionData.Backing.Port.SwitchUuid; Mac=[string]$n.MacAddress; Id=[string]$n.Id }
        }
        'CpuLimit' {
            $r = Get-VMResourceConfiguration -VM $Context.VM
            return @{ LimitMhz=[long]$r.CpuLimitMhz; ReservationMhz=[long]$r.CpuReservationMhz }
        }
        'Snapshot' { return @{ ExistingIds=@(Get-Snapshot -VM $Context.VM | ForEach-Object Id) } }
        'EdgeVlan' {
            $pg = Get-VDPortgroup -Id $Context.Pg.Id -Server $Context.VI
            $vlan = $pg.ExtensionData.Config.DefaultPortConfig.Vlan
            if ($vlan.GetType().Name -ne 'VmwareDistributedVirtualSwitchTrunkVlanSpec' -or $vlan.Inherited) { throw 'Expected an explicitly configured VLAN trunk.' }
            return @{ Ranges=@($vlan.VlanId | ForEach-Object { @{ Start=[int]$_.Start; End=[int]$_.End } }) }
        }
        'NsxBgp' {
            $b = Invoke-LabNsx $Context GET
            if ($b.resource_type -ne 'BgpRoutingConfig' -or $b.path -ne $Context.Config.Nsx.ExpectedPolicyPath) { throw 'NSX BGP resource identity mismatch.' }
            return @{ Enabled=[bool]$b.enabled }
        }
    }
}
function Set-LabBgpEnabled($Context, [bool]$Enabled) {
    $b = Invoke-LabNsx $Context GET
    if ($b.path -ne $Context.Config.Nsx.ExpectedPolicyPath) { throw 'Wrong BGP policy resource.' }
    foreach ($key in @($b.Keys)) {
        if (($key.StartsWith('_') -and $key -ne '_revision') -or $key -in 'path','relative_path','parent_path','unique_id','realization_id','remote_path','owner_id','marked_for_delete','overridden') { $b.Remove($key) }
    }
    $b.enabled = $Enabled
    Invoke-LabNsx $Context PATCH $b | Out-Null
}
function Set-LabSettings($Context, $Original, [bool]$Break, $State) {
    $s = $Context.Scenario
    switch ($s.Kind) {
        'Ntp' {
            $svc = Get-VMHostService -VMHost $Context.Host | Where-Object Key -eq 'ntpd'
            if ($Break) {
                $svc | Stop-VMHostService -Confirm:$false | Out-Null
                $svc | Set-VMHostService -Policy Off -Confirm:$false | Out-Null
                if ($s.ContainsKey('ClockSkewMinutes') -and $s.ClockSkewMinutes) {
                    # Move the host clock so the drift is visible straight away (vSAN health:
                    # "Time is synchronized across hosts and VC"). ntpd steps it back on start.
                    $dts = Get-View $Context.Host.ExtensionData.ConfigManager.DateTimeSystem -Server $Context.VI
                    $dts.UpdateDateTime([datetime]::UtcNow.AddMinutes([int]$s.ClockSkewMinutes))
                }
            } else {
                $svc | Set-VMHostService -Policy $Original.Policy -Confirm:$false | Out-Null
                if ($Original.Running) { $svc | Start-VMHostService -Confirm:$false | Out-Null }
                else { $svc | Stop-VMHostService -Confirm:$false | Out-Null }
            }
        }
        'HostPassword' {
            # Root password changed on the host only, as if someone reset it outside SDDC Manager.
            # SDDC Manager keeps the old password, so its health check can no longer log in.
            $esxcli = Get-EsxCli -VMHost $Context.Host -V2 -Server $Context.VI
            $lab = $Context.Credential.GetNetworkCredential().Password
            $new = if ($Break) { [string]$s.DriftPassword } else { $lab }
            $hist = Get-AdvancedSetting -Entity $Context.Host -Name 'Security.PasswordHistory'
            if (-not $Break) { $hist | Set-AdvancedSetting -Value 0 -Confirm:$false | Out-Null }  # history blocks the old password
            try {
                $ok = $esxcli.system.account.set.Invoke(@{ id='root'; password=$new; passwordconfirmation=$new })
                if (-not $ok) { throw 'esxcli system account set did not report success.' }
            }
            finally { if (-not $Break) { Get-AdvancedSetting -Entity $Context.Host -Name 'Security.PasswordHistory' | Set-AdvancedSetting -Value ([int]$Original.History) -Confirm:$false | Out-Null } }
            $Context.ExpectDrift = [bool]$Break
            if (-not $Break) { Sync-LabSddcCredential $s $Context.Credential }
        }
        'Maintenance' {
            if ($Break) {
                Set-VMHost -VMHost $Context.Host -State Maintenance -VsanDataMigrationMode EnsureAccessibility -Evacuate:$true -Confirm:$false | Out-Null
            } else {
                Set-VMHost -VMHost (Get-VMHost -Id $Context.Host.Id -Server $Context.VI) -State Connected -Confirm:$false | Out-Null
            }
        }
        'Dns' {
            $network = Get-VMHostNetwork -VMHost $Context.Host
            if ($Break) { $network | Set-VMHostNetwork -DnsFromDhcp:$false -DnsAddress '203.0.113.53' -Confirm:$false | Out-Null }
            elseif ($Original.FromDhcp) { $network | Set-VMHostNetwork -DnsFromDhcp:$true -DnsDhcpDevice $Original.DhcpDevice -Confirm:$false | Out-Null }
            else { $network | Set-VMHostNetwork -DnsFromDhcp:$false -DnsAddress @($Original.Addresses) -Confirm:$false | Out-Null }
        }
        'Vmotion' {
            $enabled = if ($Break) { $false } else { [bool]$Original.Enabled }
            Get-VMHostNetworkAdapter -VMHost $Context.Host -VMKernel | Where-Object Name -eq $Original.Name | Set-VMHostNetworkAdapter -VMotionEnabled:$enabled -Confirm:$false | Out-Null
        }
        'Drs' {
            $level = if ($Break) { 'Manual' } else { $Original.Automation }
            Set-Cluster -Cluster $Context.Cluster -DrsAutomationLevel $level -Confirm:$false | Out-Null
        }
        'NicDisconnected' {
            if ($Context.Nic.MacAddress -ne $Original.Mac -or $Context.Nic.Id -ne $Original.Id) { throw 'NIC identity changed; refusing to touch another adapter.' }
            $connected = if ($Break) { $false } else { [bool]$Original.Connected }
            $start = if ($Break) { $false } else { [bool]$Original.StartConnected }
            $Context.Nic | Set-NetworkAdapter -Connected:$connected -StartConnected:$start -Confirm:$false | Out-Null
        }
        'NicWrongPg' {
            if ($Context.Nic.MacAddress -ne $Original.Mac -or $Context.Nic.Id -ne $Original.Id) { throw 'NIC identity changed.' }
            $pg = if ($Break) { Get-VDPortgroup -Name $s.WrongNetwork -Server $Context.VI }
                  else { Get-VDPortgroup -Id ('DistributedVirtualPortgroup-' + $Original.PgKey) -Server $Context.VI }
            if (-not $Break -and $pg.VDSwitch.ExtensionData.Uuid -ne $Original.SwitchUuid) { throw 'Original VDS identity mismatch.' }
            $Context.Nic | Set-NetworkAdapter -Portgroup $pg -Confirm:$false | Out-Null
        }
        'CpuLimit' {
            $limit = if ($Break) { 100 } else { [long]$Original.LimitMhz }
            Get-VMResourceConfiguration -VM $Context.VM | Set-VMResourceConfiguration -CpuLimitMhz $limit -Confirm:$false | Out-Null
        }
        'Snapshot' {
            if ($Break) {
                $snap = New-Snapshot -VM $Context.VM -Name ('LabFault-' + $s.Id + '-' + $State.RunId) -Description 'Instructor-created upgrade hygiene exercise; remove only this snapshot to repair.' -Memory:$false -Quiesce:$false -Confirm:$false
                $State.CreatedSnapshotId = $snap.Id
            } else {
                if (-not $State.CreatedSnapshotId) { throw 'Snapshot creation result was not recorded. Inspect snapshots by the saved RunId; do not guess or delete others.' }
                $snap = @(Get-Snapshot -VM $Context.VM | Where-Object Id -eq $State.CreatedSnapshotId)
                if ($snap.Count -gt 1) { throw 'Snapshot identity is not unique.' }
                if ($snap.Count -eq 1) { $snap[0] | Remove-Snapshot -RemoveChildren:$false -Confirm:$false | Out-Null }
            }
        }
        'EdgeVlan' {
            $ranges = @($Original.Ranges)
            if ($Break) {
                $remaining = [Collections.Generic.List[object]]::new()
                foreach ($r in $ranges) {
                    if ($r.Start -le 49 -and $r.End -ge 49) {
                        if ($r.Start -lt 49) { $remaining.Add(@{ Start=$r.Start; End=48 }) }
                        if ($r.End -gt 49) { $remaining.Add(@{ Start=50; End=$r.End }) }
                    } else { $remaining.Add($r) }
                }
                $ranges = @($remaining.ToArray())
            }
            if ($ranges.Count -eq 0) { throw 'Refusing an empty trunk.' }
            $strings = @($ranges | ForEach-Object { "$($_.Start)-$($_.End)" })
            $Context.Pg | Set-VDVlanConfiguration -VlanTrunkRange $strings -Confirm:$false | Out-Null
        }
        'NsxBgp' { Set-LabBgpEnabled $Context $(if ($Break) { $false } else { [bool]$Original.Enabled }) }
    }
}
function Test-LabDesired($Context, $Original, [bool]$Broken, $State) {
    $now = Get-LabSettings $Context
    switch ($Context.Scenario.Kind) {
        'Ntp' { if ($Broken) { return (-not $now.Running -and $now.Policy -eq 'off') }; return ($now.Running -eq $Original.Running -and $now.Policy -eq $Original.Policy) }
        'Dns' { if ($Broken) { return (@($now.Addresses).Count -eq 1 -and $now.Addresses[0] -eq '203.0.113.53' -and -not $now.FromDhcp) }; return ($now.FromDhcp -eq $Original.FromDhcp -and (@($now.Addresses) -join ',') -eq (@($Original.Addresses) -join ',')) }
        'HostPassword' {
            if ($Broken) { return ($now.RootPassword -eq 'Drift') }
            return ($now.RootPassword -eq 'Lab' -and $now.History -eq [int]$Original.History)
        }
        'Maintenance' { if ($Broken) { return ($now.State -eq 'Maintenance') }; return ($now.State -eq 'Connected') }
        'Vmotion' { return ($now.Enabled -eq $(if ($Broken) { $false } else { [bool]$Original.Enabled })) }
        'Drs' { return ($now.Automation -eq $(if ($Broken) { 'Manual' } else { $Original.Automation })) }
        'NicDisconnected' { if ($Broken) { return (-not $now.Connected -and -not $now.StartConnected) }; return ($now.Connected -eq $Original.Connected -and $now.StartConnected -eq $Original.StartConnected) }
        'NicWrongPg' { if ($Broken) { return ($now.Network -eq $Context.Scenario.WrongNetwork) }; return ($now.PgKey -eq $Original.PgKey -and $now.SwitchUuid -eq $Original.SwitchUuid) }
        'CpuLimit' { return ($now.LimitMhz -eq $(if ($Broken) { 100 } else { [long]$Original.LimitMhz })) }
        'Snapshot' { return (($State.CreatedSnapshotId -in @($now.ExistingIds)) -eq $Broken) }
        'EdgeVlan' {
            if ($Broken) { return (-not @($now.Ranges | Where-Object { $_.Start -le 49 -and $_.End -ge 49 }).Count) }
            return ((@($now.Ranges | ForEach-Object { "$($_.Start)-$($_.End)" }) -join ',') -eq (@($Original.Ranges | ForEach-Object { "$($_.Start)-$($_.End)" }) -join ','))
        }
        'NsxBgp' { return ($now.Enabled -eq $(if ($Broken) { $false } else { [bool]$Original.Enabled })) }
    }
}
function Invoke-LabFault {
    param([string]$Id, [ValidateSet('Break','Fix')][string]$Action, [pscredential]$Credential, $ApprovalContext, [switch]$AllowMultiple, [switch]$Inspect)
    $config = Get-LabConfiguration
    $scenario = @($config.Scenarios | Where-Object Id -eq $Id)
    if ($scenario.Count -ne 1) { throw 'Unknown or duplicate scenario ID.' }
    $scenario = $scenario[0]
    $stateDir = Join-Path $script:Root 'State'
    $statePath = Join-Path $stateDir ($Id + '.json')
    if (-not $Inspect -and -not $ApprovalContext.ShouldProcess($scenario.Target, "$Action lab fault $Id ($($scenario.Title))")) { return }
    if (-not $Inspect) {
        New-Item -ItemType Directory -Path $stateDir -Force | Out-Null
        if (-not $IsWindows) { & chmod 700 -- $stateDir }
    }
    $ctx = $null
    try {
        if ($Action -eq 'Fix' -and -not $Inspect -and -not (Test-Path -LiteralPath $statePath)) { throw 'Original state file missing. Refusing to guess recovery settings.' }
        if ($Action -eq 'Break' -and -not $Inspect -and -not $AllowMultiple) {
            $active = @(Get-ChildItem $stateDir -Filter '*.json' | ForEach-Object { Get-Content $_.FullName -Raw | ConvertFrom-Json -AsHashtable } | Where-Object { $_.Status -ne 'Restored' })
            if ($active.Count) { throw 'An earlier fault is active or incomplete. Fix it first, or explicitly use -AllowMultiple for independent scenarios.' }
        }
        $ctx = Connect-LabTarget $config $scenario $Credential
        $current = Get-LabSettings $ctx
        if ($Inspect) { [pscustomobject]@{ Id=$Id; Target=$scenario.Target; Kind=$scenario.Kind; Current=($current | ConvertTo-Json -Compress -Depth 15) }; return }
        if ($Action -eq 'Break') {
            if (Test-Path -LiteralPath $statePath) {
                $previous = Get-Content $statePath -Raw | ConvertFrom-Json -AsHashtable
                if ($previous.Status -ne 'Restored') { throw 'This fault already has active state. Fix it before starting another run.' }
                $history = Join-Path $stateDir 'History'; New-Item -ItemType Directory $history -Force | Out-Null
                Copy-Item $statePath (Join-Path $history ($Id + '-' + $previous.RunId + '.json'))
            }
            switch ($scenario.Kind) {
                'Ntp' { if (-not $current.Running) { throw 'NTP is already stopped; restore a healthy baseline first.' } }
                'Dns' { if ('203.0.113.53' -in @($current.Addresses)) { throw 'Fault DNS address already present.' }; if (-not $current.FromDhcp -and -not @($current.Addresses).Count) { throw 'No static DNS baseline to restore.' } }
                'Vmotion' { if (-not $current.Enabled) { throw 'vMotion is already disabled.' } }
                'Drs' { if (-not $current.Enabled -or $current.Automation -eq 'Manual') { throw 'DRS is already disabled or manual.' } }
                'HostPassword' {
                    if (-not $scenario.DriftPassword) { throw 'DriftPassword is missing in LabConfig.json.' }
                    if ($current.RootPassword -ne 'Lab') { throw 'The host root login with the lab password fails (wrong password or root locked out for 15 minutes); restore a healthy baseline first.' }
                }
                'Maintenance' { if ($current.State -ne 'Connected') { throw 'The host must be connected and out of maintenance mode.' } }
                'NicDisconnected' { if ($ctx.VM.PowerState -ne 'PoweredOn' -or -not $current.Connected) { throw 'Disposable VM must be powered on with its NIC connected.' } }
                'NicWrongPg' { if ($current.Network -eq $scenario.WrongNetwork) { throw 'VM is already on the fault network.' }; Get-VDPortgroup -Name $scenario.WrongNetwork -Server $ctx.VI | Out-Null }
                'CpuLimit' { if ($current.ReservationMhz -gt 100 -or $current.LimitMhz -eq 100) { throw 'CPU reservation/limit is incompatible with this exercise.' } }
                'EdgeVlan' { if (-not @($current.Ranges | Where-Object { $_.Start -le 49 -and $_.End -ge 49 }).Count -or -not @($current.Ranges | Where-Object { $_.Start -le 48 -and $_.End -ge 48 }).Count) { throw 'Expected TEP VLAN 48 and BGP VLAN 49 on the dedicated trunk.' } }
                'NsxBgp' { if (-not $current.Enabled) { throw 'BGP is already disabled.' } }
            }
            $state = @{ SchemaVersion=1; Id=$Id; Kind=$scenario.Kind; Target=$scenario.Target; Scope=$scenario.Scope; RunId=[guid]::NewGuid().ToString(); CreatedAtUtc=[datetime]::UtcNow.ToString('o'); Status='Prepared'; Original=$current; CreatedSnapshotId=$null; Identity=$(if ($scenario.Kind -eq 'NsxBgp') { $config.Nsx.CertificateSha256 } else { $ctx.VI.InstanceUuid }) }
            Save-LabState $statePath $state
            try { Set-LabSettings $ctx $state.Original $true $state; $state.Status='Active' }
            catch { $state.Status='ApplyFailed'; Save-LabState $statePath $state; throw }
        } else {
            $state = Get-Content $statePath -Raw | ConvertFrom-Json -AsHashtable
            $identity = if ($scenario.Kind -eq 'NsxBgp') { $config.Nsx.CertificateSha256 } else { $ctx.VI.InstanceUuid }
            if ($state.Id -ne $Id -or $state.Target -ne $scenario.Target -or $state.Kind -ne $scenario.Kind -or $state.Identity -ne $identity) { throw 'Saved state identity does not match the configured lab.' }
            if (-not (Test-LabDesired $ctx $state.Original $false $state)) { Set-LabSettings $ctx $state.Original $false $state }
            $state.Status='RestoreApplied'; $state.RestoredAtUtc=[datetime]::UtcNow.ToString('o')
        }
        Save-LabState $statePath $state
        $ok = $false
        for ($attempt=0; $attempt -lt 15; $attempt++) {
            if (Test-LabDesired $ctx $state.Original ($Action -eq 'Break') $state) { $ok=$true; break }
            Start-Sleep -Seconds 2
        }
        if (-not $ok) { $state.Status='VerificationFailed'; Save-LabState $statePath $state; throw 'Change did not verify. Original state is preserved; run the paired Fix script and inspect the target.' }
        if ($Action -eq 'Fix') { $state.Status='Restored'; Save-LabState $statePath $state }
        [pscustomobject]@{ Id=$Id; Action=$Action; Target=$scenario.Target; Verified=$true; StateFile=$statePath; Note='Configuration verified; allow services, clocks, or BGP sessions time to converge.' }
    } finally {
        if ($ctx -and $ctx.ContainsKey('Http')) { $ctx.Http.Dispose() }
        if ($ctx -and $ctx.ContainsKey('Owned') -and $ctx.Owned) { Disconnect-VIServer -Server $ctx.VI -Confirm:$false | Out-Null }
    }
}
Export-ModuleMember -Function Get-LabScenarios, Invoke-LabFault
