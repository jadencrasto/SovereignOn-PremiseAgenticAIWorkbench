# SovereignAI local development entry point for Windows PowerShell.
param([string]$Python)
$ErrorActionPreference = 'Stop'

if ($env:SOVEREIGNAI_RUN_PROFILE -cne 'local_dev') {
    throw 'Development launcher disabled: R5.5 independent pre-resume observer handoff, descendant cleanup, and listener ownership are not verified. Set SOVEREIGNAI_RUN_PROFILE=local_dev to opt in to foreground text chat.'
}

$root = Split-Path -Parent $PSScriptRoot
if (-not $Python) {
    $Python = @("$root\backend\venv\Scripts\python.exe", "$root\.venv\Scripts\python.exe") |
        Where-Object { Test-Path -LiteralPath $_ -PathType Leaf } | Select-Object -First 1
    if (-not $Python) { $Python = (Get-Command python.exe -CommandType Application | Select-Object -First 1).Source }
}
$Python = (Get-Item -LiteralPath $Python).FullName
if (-not (Test-Path -LiteralPath "$root\frontend\dist\index.html" -PathType Leaf)) {
    throw 'Frontend build missing. Run npm.cmd --prefix frontend run build from the repository root.'
}

Push-Location -LiteralPath $root
try {
    & $Python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 13))'
    if ($LASTEXITCODE -ne 0) { throw 'Local development requires Python 3.13. Pass its executable with -Python.' }
    & $Python -m backend
    if ($LASTEXITCODE -ne 0) { throw "SovereignAI exited with code $LASTEXITCODE. Check the startup output above." }
} finally {
    Pop-Location
}
