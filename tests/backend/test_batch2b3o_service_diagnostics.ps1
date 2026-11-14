#requires -Version 7.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'batch2b3o_vm_workflow.ps1')
$passed = 0; $failed = 0
function Check($name, $test) { try { & $test; $script:passed++; Write-Host "PASS $name" } catch { $script:failed++; Write-Host "FAIL $name" } }
Check 'failure phase code and service status preserved' {
    $lines = @(Get-OServiceDiagnosticLines "J4_DIAG phase=initial`nJ4_DIAG operation=start code=1053`nJ4_DIAG state=1 win32=1067 specific=0`nJ4_DIAG failure=start_failed`nFAIL category=start_failed" 1)
    if ($lines.Count -ne 6 -or $lines[1] -cne 'J4_DIAG phase=initial' -or $lines[2] -cne 'J4_DIAG operation=start code=1053' -or $lines[3] -cne 'J4_DIAG state=1 win32=1067 specific=0') { throw 'bad diagnostic' }
}
Check 'cleanup failure and original failure retained' {
    $lines = @(Get-OServiceDiagnosticLines "J4_DIAG failure=start_failed`nJ4_DIAG phase=cleanup`nFAIL category=cleanup_failed_snapshot_reversion_required" 1)
    if ($lines.Count -ne 4 -or $lines[-1] -cne 'FAIL category=cleanup_failed_snapshot_reversion_required') { throw 'missing cleanup' }
}
Check 'raw paths and injected diagnostic content excluded' {
    $lines = @(Get-OServiceDiagnosticLines "C:\Users\private\secret`nJ4_DIAG phase=initial C:\private`nFAIL category=secret`nJ4_DIAG operation=start code=5" 1)
    if ($lines.Count -ne 2 -or $lines[-1] -cne 'J4_DIAG operation=start code=5') { throw 'leaked output' }
}
Check 'diagnostic output bounded' {
    $lines = @(Get-OServiceDiagnosticLines ((@('J4_DIAG phase=initial') * 200) -join "`n") 1)
    if ($lines.Count -ne 100) { throw 'not bounded' }
}
Write-Host "Service diagnostics: $passed passed, $failed failed"
if ($failed) { exit 1 }
