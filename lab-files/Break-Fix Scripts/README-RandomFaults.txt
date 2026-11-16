RANDOM FAULTS AND REPAIR-ALL

Two wrapper scripts on top of the existing Break/Fix pack. They only call the
existing Break/Fix scripts, so every original safety check and saved-state rule
still applies. Credentials are asked for once per run and never saved.

STUDENT: BREAK A FEW THINGS
  pwsh ./Start-RandomLabFaults.ps1
Picks 2 or 3 random, compatible exercises and breaks them without saying which.
Asks for the lab password once (or use -PromptPerTarget for separate prompts)
and asks for confirmation (add -Confirm:$false to skip).

INSTRUCTOR: REPAIR EVERYTHING
  pwsh ./Repair-AllLabFaults.ps1
  pwsh ./Repair-AllLabFaults.ps1 -CheckDrift
Fixes every exercise whose State/<ID>.json is not 'Restored': active faults,
half-applied ones and failed fixes. Order: 12 first (Region A scripts cannot
connect while the vCenter certificate is broken), then 11, 10, 09, then the rest.
One failure does not stop the others; a summary table is printed and the exit
code is 1 if anything needs attention.
-CheckDrift also compares every exercise, read-only, with the known-good baseline
in Validation-results.json and reports anything a student changed by hand. Drift
that has no saved state is reported, not changed. Repair it manually.

OPTIONS (Start-RandomLabFaults.ps1)
  -Count 3                  exact number (default: random 2 or 3)
  -IncludeHighImpact        allow 10 (workload BGP off) and 12 (vCenter certificate)
  -Exclude <ID,...>         leave exercises out, e.g. -Exclude 11-NsxAdminPasswordExpired
  -Only <ID,...>            choose only from these exercises
  -ShowSelection            print which exercises were chosen (instructor)
  -WhatIf                   show the selection and what would run; changes nothing

COMBINATION RULES
  - Refuses to start while any earlier fault is active: run Repair-AllLabFaults first.
  - 10 and 12 are excluded unless -IncludeHighImpact.
  - Never 09 with 10 (10 drops every BGP session and hides 09).
  - At most one security exercise (11 or 12); it is always broken first because
    the security module refuses to start while another fault is active.
  - Never 12 with any Region A exercise (01-08): with the vCenter certificate
    broken, those scripts cannot connect to break or fix.
  - If any Break fails, the run is rolled back: everything it broke is fixed again.

WHERE TO SEE WHAT WAS BROKEN
  pwsh ./Get-LabFaultState.ps1
  State/Runs/<RunId>.json    selection and result of each random run

FILES
  Start-RandomLabFaults.ps1, Repair-AllLabFaults.ps1, LabOrchestrator.psm1 (shared helpers)

TESTED
  Parsed; selection rules checked over 1,000 random draws with no violations;
  both scripts run with -WhatIf. No fault was actually broken or fixed.
