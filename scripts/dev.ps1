# Sovereign On-Premise Agentic AI Workbench — Dev Server Script (Windows)
# Starts both backend and frontend dev servers.
# Usage: .\scripts\dev.ps1

$ErrorActionPreference = "Stop"
# Fail closed until descendant ownership and listener identity are verified.
# PowerShell jobs alone do not prove that npm/reloader descendants are stopped,
# and an HTTP response on the expected port does not identify its owner.
throw 'Development launcher disabled: child-process cleanup and listener ownership are not verified. See APPLICATION_A1_READINESS_REPORT.md.'
Set-Location (Split-Path -Parent $PSScriptRoot)
if (-not (Test-Path 'backend\venv\Scripts\python.exe')) { throw 'Backend virtual environment missing: prepare backend\venv first.' }
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { throw 'Node.js/npm is missing.' }
if (-not (Test-Path 'frontend\node_modules\vite\bin\vite.js')) { throw 'Frontend dependencies missing: install the reviewed dependencies first.' }
& backend\venv\Scripts\python.exe -c "import fastapi, uvicorn, pydantic_settings; from backend.config import settings; assert settings.backend_host == '127.0.0.1' and settings.backend_port == 8000"
if ($LASTEXITCODE -ne 0) { throw 'Backend dependencies or loopback development configuration invalid.' }
foreach ($port in @(8000, 5173)) {
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { throw "Port $port is in use; stop the existing service before retrying." }
}
Write-Host 'Ollama models are not checked or started; use Check Ollama in the application.'

Write-Host "`n=== Sovereign AI Workbench — Dev Servers ===" -ForegroundColor Cyan

$backendJob = $null
$frontendJob = $null
try {
# Start backend
Write-Host "`n--- Starting Backend (FastAPI) on port 8000 ---" -ForegroundColor Cyan
$backendJob = Start-Job -ScriptBlock {
    Set-Location $using:PWD
    & backend\venv\Scripts\python -m backend --reload
}
Write-Host "[OK] Backend started (Job ID: $($backendJob.Id))" -ForegroundColor Green

# Start frontend (only if package.json exists)
if (Test-Path "frontend\package.json") {
    Write-Host "`n--- Starting Frontend (Vite) on port 5173 ---" -ForegroundColor Cyan
    $frontendJob = Start-Job -ScriptBlock {
        Set-Location "$using:PWD\frontend"
    npm run dev -- --host 127.0.0.1 --strictPort
    }
    Write-Host "[OK] Frontend started (Job ID: $($frontendJob.Id))" -ForegroundColor Green
} else {
    Write-Host "[SKIP] No frontend/package.json — skipping frontend" -ForegroundColor Yellow
}

Write-Host "`n=== Dev servers starting; readiness not yet verified ===" -ForegroundColor Yellow
Write-Host "Backend:  http://localhost:8000"
Write-Host "Frontend: http://localhost:5173"
Write-Host "API Docs: http://localhost:8000/docs"
Write-Host "`nPress Ctrl+C to stop the jobs started by this launcher."

# Wait and forward output
    $deadline = [DateTime]::UtcNow.AddSeconds(45)
    $ready = $false
    while ([DateTime]::UtcNow -lt $deadline -and -not $ready) {
        if ($backendJob.State -ne 'Running' -or ($frontendJob -and $frontendJob.State -ne 'Running')) { throw 'A dev job stopped during startup.' }
        try {
            $backendReady = (Invoke-RestMethod 'http://127.0.0.1:8000/api/health/ready' -TimeoutSec 2).ready -eq $true
            $frontendReady = (Invoke-WebRequest 'http://127.0.0.1:5173/' -UseBasicParsing -TimeoutSec 2).StatusCode -eq 200
            $ready = $backendReady -and $frontendReady
        } catch { $ready = $false }
        if (-not $ready) { Start-Sleep -Milliseconds 500 }
    }
    if (-not $ready) { throw 'Startup timed out; check the backend and frontend job output.' }
    Write-Host 'Backend and frontend responded. Model inference is not verified.' -ForegroundColor Green
    while ($true) {
        if ($backendJob.State -ne 'Running' -or ($frontendJob -and $frontendJob.State -ne 'Running')) { throw 'A dev job stopped. Check the output above for details.' }
        Receive-Job $backendJob -ErrorAction SilentlyContinue
        if ($frontendJob) { Receive-Job $frontendJob -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host "`nStopping dev servers..." -ForegroundColor Yellow
    @($backendJob, $frontendJob) | Where-Object { $_ } | Receive-Job -ErrorAction SilentlyContinue
    @($backendJob, $frontendJob) | Where-Object { $_ } | Stop-Job -ErrorAction SilentlyContinue
    @($backendJob, $frontendJob) | Where-Object { $_ } | Remove-Job -ErrorAction SilentlyContinue
}
