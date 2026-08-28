# Sovereign On-Premise Agentic AI Workbench — Dev Server Script (Windows)
# Starts both backend and frontend dev servers.
# Usage: .\scripts\dev.ps1

$ErrorActionPreference = "Stop"

Write-Host "`n=== Sovereign AI Workbench — Dev Servers ===" -ForegroundColor Cyan

# Start backend
Write-Host "`n--- Starting Backend (FastAPI) on port 8000 ---" -ForegroundColor Cyan
$backendJob = Start-Job -ScriptBlock {
    Set-Location $using:PWD
    & backend\venv\Scripts\python -m uvicorn backend.main:app --reload --port 8000
}
Write-Host "[OK] Backend started (Job ID: $($backendJob.Id))" -ForegroundColor Green

# Start frontend (only if package.json exists)
if (Test-Path "frontend\package.json") {
    Write-Host "`n--- Starting Frontend (Vite) on port 5173 ---" -ForegroundColor Cyan
    $frontendJob = Start-Job -ScriptBlock {
        Set-Location "$using:PWD\frontend"
        npm run dev
    }
    Write-Host "[OK] Frontend started (Job ID: $($frontendJob.Id))" -ForegroundColor Green
} else {
    Write-Host "[SKIP] No frontend/package.json — skipping frontend" -ForegroundColor Yellow
}

Write-Host "`n=== Dev servers running ===" -ForegroundColor Green
Write-Host "Backend:  http://localhost:8000"
Write-Host "Frontend: http://localhost:5173"
Write-Host "API Docs: http://localhost:8000/docs"
Write-Host "`nPress Ctrl+C to stop, then run: Get-Job | Stop-Job | Remove-Job"

# Wait and forward output
try {
    while ($true) {
        Receive-Job $backendJob -ErrorAction SilentlyContinue
        if ($frontendJob) { Receive-Job $frontendJob -ErrorAction SilentlyContinue }
        Start-Sleep -Seconds 2
    }
} finally {
    Write-Host "`nStopping dev servers..." -ForegroundColor Yellow
    Get-Job | Stop-Job
    Get-Job | Remove-Job
}
