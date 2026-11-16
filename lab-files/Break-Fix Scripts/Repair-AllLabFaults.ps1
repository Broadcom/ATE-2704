<#
.SYNOPSIS
  Repairs every lab exercise that is broken, in a safe order.

.DESCRIPTION
  Finds every exercise whose saved state (State/<ID>.json) is not 'Restored' and
  runs its Fix script. This covers faults from Start-RandomLabFaults.ps1, from
  individual Break scripts, and from Break runs that failed half-way.

  Order: 12 (vCenter certificate) first, because the Region A scripts cannot
  connect until it is fixed. Then 11, 10, 09, and the rest. One failure does not
  stop the others.

  -CheckDrift also compares every exercise against the known-good baseline
  (Validation-results.json, read-only) to catch changes a student made by hand.
  Drift without saved state is reported, not changed: the Fix scripts only
  restore settings they recorded themselves.

.EXAMPLE
  pwsh ./Repair-AllLabFaults.ps1
  pwsh ./Repair-AllLabFaults.ps1 -CheckDrift
  pwsh ./Repair-AllLabFaults.ps1 -WhatIf
#>
[CmdletBinding(SupportsShouldProcess = $true, ConfirmImpact = 'Medium')]
param(
    [switch]$CheckDrift,
    [switch]$PromptPerTarget
)
$ErrorActionPreference = 'Stop'
Import-Module (Join-Path $PSScriptRoot 'LabOrchestrator.psm1') -Force

$all = @(Get-OrchScenarios)
$activeState = @(Get-OrchActiveState)
$unknown = @($activeState | Where-Object { $_.Id -notin $all.Id })
foreach ($u in $unknown) { Write-Warning "State file for unknown exercise '$($u.Id)' ignored: $($u.File)" }
$toFix = @(Get-OrchFixOrder @($all | Where-Object { $_.Id -in $activeState.Id }))

if ($toFix.Count) {
    Write-Host 'Exercises to repair (in order):'
    $toFix | ForEach-Object { $id = $_.Id; Write-Host ("  {0}  [{1}]  {2}" -f $_.Id, (@($activeState | Where-Object Id -eq $id)[0].Status), $_.Target) }
} else {
    Write-Host 'No active or incomplete exercises found in State/.'
}
if ($WhatIfPreference) {
    foreach ($s in $toFix) { Invoke-OrchStep $s Fix @{} -WhatIfOnly }
    if ($CheckDrift) { Write-Host 'What if: would compare all exercises against Validation-results.json (read-only).' }
    return
}
if (-not $toFix.Count -and -not $CheckDrift) { return }
if (-not $PSCmdlet.ShouldProcess("$($toFix.Count) lab exercises", 'Repair')) { return }

$lock = Enter-OrchLock
$results = [System.Collections.Generic.List[object]]::new()
try {
    $checkScope = if ($CheckDrift) { $all } else { $toFix }
    $creds = Get-OrchCredentials (Get-OrchCredentialKeys $checkScope) -PromptPerTarget:$PromptPerTarget

    foreach ($s in $toFix) {
        Write-Host "Repairing $($s.Id)..."
        try {
            $null = Invoke-OrchStep $s Fix $creds
            $status = @(Get-OrchState | Where-Object Id -eq $s.Id)[0].Status
            $results.Add([pscustomobject]@{ Id = $s.Id; Action = 'Fix'; Result = $(if ($status -eq 'Restored') { 'Repaired' } else { "Check ($status)" }); Detail = '' })
        } catch {
            $results.Add([pscustomobject]@{ Id = $s.Id; Action = 'Fix'; Result = 'FAILED'; Detail = $_.Exception.Message })
        }
    }

    if ($CheckDrift) {
        Import-Module (Join-Path $PSScriptRoot 'LabFaults.psm1') -Force
        Import-Module (Join-Path $PSScriptRoot 'SecurityFaults.psm1') -Force
        $baseline = @{}
        foreach ($c in (Get-Content (Join-Path $PSScriptRoot 'Validation-results.json') -Raw | ConvertFrom-Json -AsHashtable).checks) { $baseline[$c.Id] = $c }
        # Order matters here too: 12 first, so Region A inspections can connect.
        foreach ($s in (Get-OrchFixOrder $all)) {
            if (@($results | Where-Object { $_.Id -eq $s.Id -and $_.Result -eq 'FAILED' }).Count) { continue }
            $b = $baseline[$s.Id]
            if (-not $b) { $results.Add([pscustomobject]@{ Id = $s.Id; Action = 'Drift'; Result = 'No baseline'; Detail = '' }); continue }
            try {
                if ($s.Security) {
                    $now = (Invoke-LabSecurityFault -Id $s.Id -Action Inspect -Credential $creds[$s.Scope] -GuestCredential $creds['Root'] | Select-Object -Last 1) | ConvertFrom-Json -AsHashtable
                    $diff = if ($s.Kind -eq 'NsxPassword') {
                        if ($now.admin.status -ne 'ACTIVE' -or $now.admin.password_reset_required) { "admin status=$($now.admin.status), password_reset_required=$($now.admin.password_reset_required)" }
                    } else {
                        if ($now.certificateSha256 -ne $b.certificateSha256) { 'vCenter Machine SSL certificate differs from the baseline' }
                    }
                } else {
                    $now = (Invoke-LabFault -Id $s.Id -Action Break -Credential $creds[$s.Scope] -Inspect).Current | ConvertFrom-Json -AsHashtable
                    $was = $b.Current | ConvertFrom-Json -AsHashtable
                    $diff = @($was.Keys | Where-Object { ($now[$_] | ConvertTo-Json -Compress -Depth 10) -ne ($was[$_] | ConvertTo-Json -Compress -Depth 10) } |
                            ForEach-Object { "$_ : $($was[$_] | ConvertTo-Json -Compress -Depth 10) -> $($now[$_] | ConvertTo-Json -Compress -Depth 10)" }) -join '; '
                }
                $results.Add([pscustomobject]@{ Id = $s.Id; Action = 'Drift'; Result = $(if ($diff) { 'DRIFT' } else { 'OK' }); Detail = $diff })
            } catch {
                $results.Add([pscustomobject]@{ Id = $s.Id; Action = 'Drift'; Result = 'CHECK FAILED'; Detail = $_.Exception.Message })
            }
        }
    }
} finally {
    $lock.Dispose()
}

$results | Format-Table -AutoSize -Wrap
$bad = @($results | Where-Object { $_.Result -in 'FAILED', 'DRIFT', 'CHECK FAILED' -or $_.Result -like 'Check*' })
if ($bad.Count) {
    Write-Warning "$($bad.Count) item(s) need attention. Drift without saved state must be repaired by hand: compare with Validation-results.json."
    exit 1
}
Write-Host 'Lab exercises are repaired.'
