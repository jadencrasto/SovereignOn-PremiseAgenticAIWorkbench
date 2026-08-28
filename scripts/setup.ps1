# Sovereign On-Premise Agentic AI Workbench — Setup Script (Windows)
# Run this once to bootstrap the development environment.
# Usage: .\scripts\setup.ps1

$ErrorActionPreference = "Stop"

Write-Host "`n=== Sovereign AI Workbench — Setup ===" -ForegroundColor Cyan

# --- Check prerequisites ---
$prereqs = @(
    @{ Name = "Python";  Cmd = "python --version" },
    @{ Name = "Node.js"; Cmd = "node --version" },
    @{ Name = "npm";     Cmd = "npm --version" },
    @{ Name = "Git";     Cmd = "git --version" },
    @{ Name = "Ollama";  Cmd = "ollama --version" }
)

foreach ($p in $prereqs) {
    try {
        $ver = Invoke-Expression $p.Cmd 2>&1
        Write-Host "[OK] $($p.Name): $ver" -ForegroundColor Green
    } catch {
        Write-Host "[MISSING] $($p.Name) — please install before continuing." -ForegroundColor Red
        exit 1
    }
}

# --- Create .env if not present ---
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "[OK] Created .env from .env.example" -ForegroundColor Green
} else {
    Write-Host "[SKIP] .env already exists" -ForegroundColor Yellow
}

# --- Backend: create venv & install deps ---
Write-Host "`n--- Backend Setup ---" -ForegroundColor Cyan
if (-not (Test-Path "backend\venv")) {
    python -m venv backend\venv
    Write-Host "[OK] Created Python venv at backend\venv" -ForegroundColor Green
}
& backend\venv\Scripts\pip install -r requirements.txt
Write-Host "[OK] Backend dependencies installed" -ForegroundColor Green

# --- Frontend: npm install ---
Write-Host "`n--- Frontend Setup ---" -ForegroundColor Cyan
if (Test-Path "frontend\package.json") {
    Push-Location frontend
    npm install
    Pop-Location
    Write-Host "[OK] Frontend dependencies installed" -ForegroundColor Green
} else {
    Write-Host "[SKIP] frontend/package.json not found — will be created during Phase 1" -ForegroundColor Yellow
}

# --- Ollama: check for models ---
Write-Host "`n--- Ollama Check ---" -ForegroundColor Cyan
try {
    $models = ollama list 2>&1
    Write-Host "Available Ollama models:" -ForegroundColor Cyan
    Write-Host $models
    if ($models -notmatch "llama3") {
        Write-Host "[WARN] llama3 not found. Run: ollama pull llama3" -ForegroundColor Yellow
    }
} catch {
    Write-Host "[WARN] Could not query Ollama. Make sure it is running." -ForegroundColor Yellow
}

Write-Host "`n=== Setup Complete ===" -ForegroundColor Green
Write-Host "Next steps:"
Write-Host "  1. Edit .env if needed"
Write-Host "  2. Run: .\scripts\dev.ps1  (to start dev servers)"
Write-Host ""
