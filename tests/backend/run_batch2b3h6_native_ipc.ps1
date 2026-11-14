# Diagnostic preflight only. No IPC integration is run while transport is disabled.
$ErrorActionPreference = 'Stop'
$ipcPython = 'C:\Users\jason\Projects\SovereignAI-IPC-Venv\Scripts\python.exe'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$tokenDiagnostic = Join-Path $projectRoot 'tests\backend\batch2b3h5_token_diagnostic.py'

function Invoke-BoundedPython([string] $Arguments) {
    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $ipcPython
    $startInfo.Arguments = $Arguments
    $startInfo.WorkingDirectory = $projectRoot
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.EnvironmentVariables['PYTHONDONTWRITEBYTECODE'] = '1'
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    try {
        if (-not $process.Start()) { throw 'Diagnostic process did not start.' }
        if (-not $process.WaitForExit(10000)) {
            $process.Kill()
            if (-not $process.WaitForExit(5000)) { throw 'Diagnostic process did not exit.' }
            throw 'Diagnostic process timed out.'
        }
        if ($process.ExitCode -ne 0) { throw 'Diagnostic process failed.' }
        return $process.StandardOutput.ReadToEnd()
    } finally {
        $process.Dispose()
    }
}

try {
    if (-not (Test-Path -LiteralPath $ipcPython -PathType Leaf)) { throw 'IPC interpreter missing.' }
    $check = Invoke-BoundedPython '-I -c "import win32pipe,win32security,win32file,pywintypes; print(\"IMPORTS_OK\")"'
    if ($check.Trim() -ne 'IMPORTS_OK') { throw 'IPC dependency check failed.' }
    $tokenOutput = Invoke-BoundedPython ('-I "' + $tokenDiagnostic + '"')
    $token = $tokenOutput | ConvertFrom-Json
    if ($null -ne $token.server_package_sid -or $null -ne $token.client_package_sid) {
        throw 'AppContainer detected; no integration test started.'
    }
    if (-not $token.same_user -or -not $token.same_logon_sids -or -not $token.same_session) {
        throw 'Parent and child token contexts differ; no integration test started.'
    }
    Write-Output 'Interpreter and non-AppContainer token preflight passed.'
    Write-Output 'BLOCKED: bounded IPC transport is not implemented or verified; no broker was started.'
    exit 2
} catch {
    Write-Output ('BLOCKED: ' + $_.Exception.Message)
    exit 2
}
