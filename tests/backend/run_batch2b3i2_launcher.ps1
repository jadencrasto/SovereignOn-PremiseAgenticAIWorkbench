# Operator only: synthetic local development IPC. Runs only child processes it creates.
$ErrorActionPreference = 'Stop'
if ($PSVersionTable.PSVersion.Major -lt 7) { Write-Output 'FAIL: PowerShell 7 or later is required for owned process-tree cleanup.'; exit 1 }
$root = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$exe = Join-Path $root 'prototype\windows_broker_dotnet\bin\Debug\net10.0\Broker.exe'
if (-not (Test-Path -LiteralPath $exe -PathType Leaf)) { Write-Output 'FAIL category=build_required'; exit 1 }
$info = [Diagnostics.ProcessStartInfo]::new()
$info.FileName = $exe
$info.Arguments = 'inspect-launch'
$info.UseShellExecute = $false
$info.CreateNoWindow = $true
$info.RedirectStandardOutput = $true
$info.RedirectStandardError = $true
$p = [Diagnostics.Process]::new()
$p.StartInfo = $info
try {
    if (-not $p.Start()) { throw 'start_failed' }
    $out = $p.StandardOutput.ReadToEndAsync()
    $err = $p.StandardError.ReadToEndAsync()
    if (-not $p.WaitForExit(20000)) { throw 'deadline' }
    if (-not $out.Wait(2000) -or -not $err.Wait(2000)) { throw 'drain_failed' }
    if ($p.ExitCode -ne 0 -or $err.Result -match 'LAUNCH category=cleanup_failed' -or $out.Result.Trim() -cne 'LAUNCH observation=child_process_match authentication=unsupported') { throw 'inspection_failed' }
    Write-Output 'PASS: child process observed; authentication unsupported.'
    exit 0
} catch {
    Write-Output 'FAIL: launcher inspection unavailable or deadline exceeded.'
    exit 1
} finally {
    try { if (-not $p.HasExited) { $p.Kill($true); $null = $p.WaitForExit(5000) } } catch { Write-Output 'FAIL: owned process-tree cleanup failed.' }
    $p.Dispose()
}
