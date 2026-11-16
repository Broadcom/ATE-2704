# Shared helpers for Start-RandomLabFaults.ps1 and Repair-AllLabFaults.ps1.
# Sits next to LabFaults.psm1 / SecurityFaults.psm1 and only calls the existing
# Break/Fix scripts, so all of their safety checks and saved-state handling still apply.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:Root = $PSScriptRoot
$script:StateDir = Join-Path $script:Root 'State'

# Marked HIGH IMPACT in LabConfig.json; only used when explicitly requested.
$script:HighImpact = @('10-WorkloadBgpDisabled', '12-VcenterCertificateTrust')

# Pairs that must not be active together.
$script:Conflicts = @(
    @('09-EdgeUplinkVlanMissing', '10-WorkloadBgpDisabled'),      # 10 drops every BGP session and hides 09
    @('11-NsxAdminPasswordExpired', '12-VcenterCertificateTrust') # security module allows one security fault at a time
)

# Repair order. 12 first: until vc-mgmt-a has its original certificate back, the
# Region A scripts (strict certificate checking) cannot connect. 11 next, then BGP
# (10 before 09), then everything else.
$script:FixPriority = @{
    '12-VcenterCertificateTrust' = 0; '11-NsxAdminPasswordExpired' = 1
    '10-WorkloadBgpDisabled' = 2; '09-EdgeUplinkVlanMissing' = 3
}

$script:Usernames = @{
    RegionA = 'administrator@vsphere.local'   # vc-mgmt-a
    WorkloadB = 'administrator@wld.sso'       # vc-wld01-b
    ManagementB = 'administrator@vsphere.local' # vc-mgmt-b
    NSX = 'admin'                             # nsx-wld01-b
    Root = 'root'                             # appliance root (security exercises)
}

function Get-OrchScenarios {
    $config = Get-Content (Join-Path $script:Root 'LabConfig.json') -Raw | ConvertFrom-Json -AsHashtable
    foreach ($s in $config.Scenarios) {
        [pscustomobject]@{
            Id = $s.Id; Kind = $s.Kind; Scope = $s.Scope; Target = $s.Target; Title = $s.Title
            Security = ($s.ContainsKey('Backend') -and $s.Backend -eq 'Security')
            HighImpact = ($s.Id -in $script:HighImpact)
        }
    }
}

function Test-OrchCompatible($A, $B) {
    foreach ($pair in $script:Conflicts) { if ($A.Id -in $pair -and $B.Id -in $pair) { return $false } }
    if ($A.Security -and $B.Security) { return $false }
    # With the vCenter certificate fault active, Region A scripts cannot connect.
    if (($A.Id -eq '12-VcenterCertificateTrust' -and $B.Scope -eq 'RegionA') -or
        ($B.Id -eq '12-VcenterCertificateTrust' -and $A.Scope -eq 'RegionA')) { return $false }
    return $true
}

function Select-OrchCompatibleSet($Pool, [int]$Count) {
    $best = @()
    for ($try = 0; $try -lt 100 -and $best.Count -lt $Count; $try++) {
        $picked = [System.Collections.Generic.List[object]]::new()
        foreach ($candidate in ($Pool | Get-Random -Shuffle)) {
            if ($picked.Count -ge $Count) { break }
            if (@($picked | Where-Object { -not (Test-OrchCompatible $_ $candidate) }).Count -eq 0) { $picked.Add($candidate) }
        }
        if ($picked.Count -gt $best.Count) { $best = @($picked) }
    }
    return $best
}

function Get-OrchState {
    # Only top-level State/*.json are exercise state; History/ and Runs/ are ignored.
    if (-not (Test-Path $script:StateDir)) { return @() }
    Get-ChildItem $script:StateDir -Filter '*.json' -File | ForEach-Object {
        $s = Get-Content $_.FullName -Raw | ConvertFrom-Json -AsHashtable
        [pscustomobject]@{ Id = $s.Id; Status = $s.Status; Target = $s.Target; CreatedAtUtc = $s.CreatedAtUtc; File = $_.FullName }
    }
}

function Get-OrchActiveState { @(Get-OrchState | Where-Object Status -ne 'Restored') }

function Get-OrchFixOrder($Scenarios) {
    $Scenarios | Sort-Object @{ Expression = { if ($script:FixPriority.ContainsKey($_.Id)) { $script:FixPriority[$_.Id] } else { 10 } } }, Id
}

function Get-OrchCredentials([string[]]$Keys, [switch]$PromptPerTarget) {
    # Credentials are kept in memory for this run only, never written to disk.
    $creds = @{}
    $Keys = @($Keys | Select-Object -Unique)
    if (-not $Keys.Count) { return $creds }
    if ($PromptPerTarget) {
        foreach ($k in $Keys) { $creds[$k] = Get-Credential -UserName $script:Usernames[$k] -Message "Credential for $k" }
    } else {
        $pw = Read-Host -AsSecureString "Lab password (used for: $($Keys -join ', '))"
        foreach ($k in $Keys) { $creds[$k] = [pscredential]::new($script:Usernames[$k], $pw) }
    }
    return $creds
}

function Get-OrchCredentialKeys($Scenarios) {
    foreach ($s in $Scenarios) {
        $s.Scope
        if ($s.Security) { 'Root' }
    }
}

function Invoke-OrchStep($Scenario, [ValidateSet('Break', 'Fix')][string]$Action, $Credentials, [switch]$AllowMultiple, [switch]$WhatIfOnly) {
    $script = Join-Path $script:Root ("$Action/$Action-$($Scenario.Id).ps1")
    if (-not (Test-Path -LiteralPath $script)) { throw "Missing $Action script for $($Scenario.Id)" }
    if ($Scenario.Security) {
        & $script -Credential $Credentials[$Scenario.Scope] -GuestCredential $Credentials['Root'] -Confirm:$false -WhatIf:$WhatIfOnly
    } else {
        & $script -Credential $Credentials[$Scenario.Scope] -AllowMultiple:$AllowMultiple -Confirm:$false -WhatIf:$WhatIfOnly
    }
}

function Enter-OrchLock {
    # Stops Break and Repair runs from overlapping (README: never run Break and Fix concurrently).
    New-Item -ItemType Directory -Path $script:StateDir -Force | Out-Null
    $path = Join-Path $script:StateDir 'orchestrator.lock'
    try { return [IO.File]::Open($path, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Another break or repair run is in progress. Wait for it to finish.' }
}

function Save-OrchRunLog($Log) {
    $dir = Join-Path $script:StateDir 'Runs'
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    if (-not $IsWindows) { & chmod 700 -- $dir }
    $path = Join-Path $dir ("$($Log.RunId).json")
    $Log | ConvertTo-Json -Depth 10 | Set-Content -LiteralPath $path -Encoding utf8
    if (-not $IsWindows) { & chmod 600 -- $path }
    return $path
}

Export-ModuleMember -Function Get-OrchScenarios, Test-OrchCompatible, Select-OrchCompatibleSet, Get-OrchState,
    Get-OrchActiveState, Get-OrchFixOrder, Get-OrchCredentials, Get-OrchCredentialKeys, Invoke-OrchStep, Enter-OrchLock, Save-OrchRunLog
