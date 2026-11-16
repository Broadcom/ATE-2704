$p=Join-Path $PSScriptRoot 'State'
if(Test-Path $p){ Get-ChildItem $p -Filter '*.json' | ForEach-Object {Get-Content $_.FullName -Raw | ConvertFrom-Json} | Select-Object Id,Target,Status,CreatedAtUtc,RestoredAtUtc | Format-Table -AutoSize } else { 'No fault state has been created.' }
