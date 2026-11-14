# Disposable processes only. Tests the launcher's Start-Job/Stop-Job primitive,
# not the real dev servers or network/port behavior.
$ErrorActionPreference = 'Stop'
$marker = Join-Path $env:TEMP ('a1-job-' + [guid]::NewGuid().ToString('N') + '.txt')
$other = $null; $job = $null; $childId = $null
try {
    $other = Start-Process powershell.exe -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 90' -WindowStyle Hidden -PassThru
    $job = Start-Job -ArgumentList $marker -ScriptBlock {
        param($outPath)
        $child = Start-Process powershell.exe -ArgumentList '-NoProfile','-Command','Start-Sleep -Seconds 90' -WindowStyle Hidden -PassThru
        Set-Content -LiteralPath $outPath -Value $child.Id
        Wait-Process -Id $child.Id
    }
    $deadline = [DateTime]::UtcNow.AddSeconds(15)
    while (-not (Test-Path -LiteralPath $marker)) {
        if ([DateTime]::UtcNow -gt $deadline) { throw 'Timed out waiting for test child' }
        Start-Sleep -Milliseconds 100
    }
    $childId = [int](Get-Content -LiteralPath $marker -Raw)
    Stop-Job -Job $job
    $childAlive = $null -ne (Get-Process -Id $childId -ErrorAction SilentlyContinue)
    $other.Refresh()
    if ($other.HasExited) { throw 'Unrelated test process exited unexpectedly' }
    if ($childAlive) { throw 'Stop-Job left the launched child running' }
    Write-Output 'PASS: test child stopped; unrelated test process survived'
} finally {
    if ($job) { Stop-Job -Job $job -ErrorAction SilentlyContinue; Remove-Job -Job $job -Force -ErrorAction SilentlyContinue }
    if ($childId) { Stop-Process -Id $childId -ErrorAction SilentlyContinue }
    if ($other) { $other.Refresh(); if (-not $other.HasExited) { Stop-Process -Id $other.Id -ErrorAction SilentlyContinue }; $other.Dispose() }
    Remove-Item -LiteralPath $marker -Force -ErrorAction SilentlyContinue
}
