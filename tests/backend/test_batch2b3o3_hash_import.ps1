#requires -Version 7.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'batch2b3o_vm_workflow.ps1')
$root = Join-Path ([IO.Path]::GetTempPath()) ('sovereign-hash-test-' + [guid]::NewGuid().ToString('N'))
[IO.Directory]::CreateDirectory($root) | Out-Null
$passed = 0; $failed = 0
function Test-Case($name, $action) {
    try { & $action; $script:passed++; Write-Host "PASS $name" }
    catch { $script:failed++; Write-Host "FAIL $name" }
}
function Expect-Failure($action) { $failedAsExpected = $false; try { & $action } catch { $failedAsExpected = $true }; if (-not $failedAsExpected) { throw 'expected failure' } }
try {
    $names = @('ServiceFoundation.exe','ServiceFoundation.dll','ServiceFoundation.deps.json','ServiceFoundation.runtimeconfig.json','SecurityFoundation.exe','SecurityFoundation.dll','SecurityFoundation.deps.json','SecurityFoundation.runtimeconfig.json')
    $lines = foreach ($name in $names) { [IO.File]::WriteAllText((Join-Path $root $name), $name); ((Get-FileHash (Join-Path $root $name)).Hash.ToLowerInvariant() + '  ' + $name) }
    $inventory = Join-Path $root 'inventory.txt'
    function Write-Inventory($text) { [IO.File]::WriteAllText($inventory, $text, [Text.UTF8Encoding]::new($false)); return (Get-FileHash $inventory).Hash.ToLowerInvariant() }
    $text = ($lines -join "`n") + "`n"
    $pin = Write-Inventory $text
    Test-Case 'eight pinned hashes and binary verification' {
        $locks = [Collections.Generic.List[IDisposable]]::new()
        try { $pins = Read-OLifecycleArtifactPins $inventory $pin $locks; if ($pins.Count -ne 8) { throw 'count' }; foreach ($name in $names) { Open-OPin (Join-Path $root $name) $pins[$name] $locks } }
        finally { foreach ($lock in $locks) { $lock.Dispose() } }
    }
    Test-Case 'incorrect inventory pin denied' { $locks = [Collections.Generic.List[IDisposable]]::new(); try { Expect-Failure { Read-OLifecycleArtifactPins $inventory ('0' * 64) $locks } } finally { foreach ($lock in $locks) { $lock.Dispose() } } }
    Test-Case 'duplicate and unexpected entries denied' { foreach ($extra in @($lines[0], (('0' * 64) + '  unexpected.exe'))) { $bad = Write-Inventory ($text + $extra + "`n"); $locks = [Collections.Generic.List[IDisposable]]::new(); try { Expect-Failure { Read-OLifecycleArtifactPins $inventory $bad $locks } } finally { foreach ($lock in $locks) { $lock.Dispose() } } } }
    Test-Case 'missing inventory entry denied' { $bad = Write-Inventory (($lines[0..6] -join "`n") + "`n"); $locks = [Collections.Generic.List[IDisposable]]::new(); try { Expect-Failure { Read-OLifecycleArtifactPins $inventory $bad $locks } } finally { foreach ($lock in $locks) { $lock.Dispose() } } }
    Test-Case 'tampered and missing binary denied' { $locks = [Collections.Generic.List[IDisposable]]::new(); try { Expect-Failure { Open-OPin (Join-Path $root $names[0]) ('0' * 64) $locks }; Expect-Failure { Open-OPin (Join-Path $root 'missing.exe') ('0' * 64) $locks } } finally { foreach ($lock in $locks) { $lock.Dispose() } } }
} finally { Remove-Item -LiteralPath $root -Recurse -Force }
Write-Host "Hash import: $passed passed, $failed failed"
if ($failed) { exit 1 }
