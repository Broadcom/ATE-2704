<#
.SYNOPSIS
  Randomly breaks two or three lab exercises for students to troubleshoot.

.DESCRIPTION
  Picks a random, compatible set of exercises from LabConfig.json and runs their
  Break scripts. The student is not told which ones were chosen.

  Rules:
  - Refuses to start while any earlier fault is still active (run Repair-AllLabFaults.ps1).
  - High-impact exercises (10 workload BGP, 12 vCenter certificate) are left out
    unless -IncludeHighImpact is given.
  - Never combines 09 with 10, two security exercises (11/12), or 12 with any
    Region A exercise.
  - A security exercise (11/12) is always broken first, because it refuses to start
    while another fault is active.
  - If any Break fails, everything this run already broke is repaired again.

  The instructor can see the chosen exercises with Get-LabFaultState.ps1 or in
  State/Runs/<RunId>.json.

.EXAMPLE
  pwsh ./Start-RandomLabFaults.ps1                 # 2 or 3 random faults
  pwsh ./Start-RandomLabFaults.ps1 -WhatIf -ShowSelection
  pwsh ./Start-RandomLabFaults.ps1 -Count 3 -IncludeHighImpact
  pwsh ./Start-RandomLabFaults.ps1 -Exclude 11-NsxAdminPasswordExpired
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'High')]
param(
    [ValidateRange(0, 5)][int]$Count = 0,   # 0 = random 2 or 3
    [string[]]$Exclude = @(),
    [string[]]$Only = @(),                  # restrict the pool to these IDs
    [switch]$IncludeHighImpact,
    [switch]$PromptPerTarget,               # separate credential prompts instead of one lab password
    [switch]$ShowSelection                  # instructor: print the chosen exercises
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'LabOrchestrator.psm1') -Force

$active = Get-OrchActiveState
if ($active.Count) {
    throw "Faults from an earlier run are still active or incomplete ($(($active.Id) -join ', ')). Run ./Repair-AllLabFaults.ps1 first."
}

$all = @(Get-OrchScenarios)
foreach ($id in @($Exclude) + @($Only)) { if ($id -notin $all.Id) { throw "Unknown exercise ID: $id" } }
$pool = @($all | Where-Object {
        ($Only.Count -eq 0 -or $_.Id -in $Only) -and $_.Id -notin $Exclude -and ($IncludeHighImpact -or -not $_.HighImpact) -and
        ($_.Random -or $_.Id -in $Only)
    })
if ($Count -eq 0) { $Count = Get-Random -Minimum 2 -Maximum 4 }
$selection = @(Select-OrchCompatibleSet $pool $Count)
if (-not $selection.Count) { throw 'No exercises available with the current filters.' }
if ($selection.Count -lt $Count) { Write-Warning "Only $($selection.Count) compatible exercises could be combined with these filters." }

# Security exercise first (it refuses to start while another fault is active).
$selection = @($selection | Sort-Object @{ Expression = { -not $_.Security } }, { Get-Random })

if ($ShowSelection -or $WhatIfPreference) {
    Write-Host 'Selected exercises:'
    $selection | ForEach-Object { Write-Host ("  {0}  {1}  ({2})" -f $_.Id, $_.Title, $_.Target) }
}
if ($WhatIfPreference) {
    foreach ($s in $selection) { Invoke-OrchStep $s Break @{} -WhatIfOnly }
    return
}
if (-not $PSCmdlet.ShouldProcess("$($selection.Count) randomly chosen lab exercises", 'Break')) { return }

$lock = Enter-OrchLock
try {
    $creds = Get-OrchCredentials (Get-OrchCredentialKeys $selection) -PromptPerTarget:$PromptPerTarget
    $log = [ordered]@{ RunId = [guid]::NewGuid().ToString(); StartedAtUtc = [datetime]::UtcNow.ToString('o'); Selected = @($selection.Id); Results = @() }
    $done = [System.Collections.Generic.List[object]]::new()
    $failed = $null
    foreach ($s in $selection) {
        Write-Host ("Applying exercise {0} of {1}..." -f ($done.Count + 1), $selection.Count)
        try {
            $null = Invoke-OrchStep $s Break $creds -AllowMultiple
            $done.Add($s); $log.Results += @{ Id = $s.Id; Result = 'Broken' }
        } catch {
            $failed = $s; $log.Results += @{ Id = $s.Id; Result = 'Failed'; Error = $_.Exception.Message }
            break
        }
    }
    if ($failed) {
        Write-Warning "Could not apply one exercise ($($failed.Id)): rolling back this run."
        $rollback = @(Get-OrchFixOrder (@($done) + @($failed)))
        foreach ($s in $rollback) {
            if (@(Get-OrchActiveState | Where-Object Id -eq $s.Id).Count) {
                try { $null = Invoke-OrchStep $s Fix $creds; $log.Results += @{ Id = $s.Id; Result = 'RolledBack' } }
                catch { $log.Results += @{ Id = $s.Id; Result = 'RollbackFailed'; Error = $_.Exception.Message } }
            }
        }
        $log.FinishedAtUtc = [datetime]::UtcNow.ToString('o'); $path = Save-OrchRunLog $log
        throw "Run aborted and rolled back. Details: $path. Check with ./Get-LabFaultState.ps1 and run ./Repair-AllLabFaults.ps1 if anything is still active."
    }
    $log.FinishedAtUtc = [datetime]::UtcNow.ToString('o'); $null = Save-OrchRunLog $log
    Write-Host ''
    Write-Host "$($done.Count) problems have been introduced into the lab. Start troubleshooting!"
    if ($ShowSelection) { $done | ForEach-Object { Write-Host "  - $($_.Id)" } }
} finally {
    $lock.Dispose()
}
