# Operator-run native Windows test; never run inside the managed AppContainer.
$ErrorActionPreference = 'Stop'
$projectRoot = [System.IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..\..'))
$brokerDll = Join-Path $projectRoot 'prototype\windows_broker_dotnet\bin\Debug\net10.0\Broker.dll'
$dotnet = (Get-Command dotnet -ErrorAction Stop).Source
$pipeName = 'SovereignAI-dev-' + [Guid]::NewGuid().ToString('N')
$owned = [System.Collections.Generic.List[object]]::new()
$server = $null
Add-Type -TypeDefinition @'
using System.IO;
using System.Text;
using System.Threading.Tasks;
public static class SovereignBoundedCapture {
    public static async Task<string> Drain(StreamReader reader, int limit) {
        var result = new StringBuilder(); var chars = new char[512]; int n;
        while ((n = await reader.ReadAsync(chars, 0, chars.Length)) != 0) {
            int take = System.Math.Min(n, limit - result.Length);
            if (take > 0) result.Append(chars, 0, take);
        }
        return result.ToString();
    }
    public static async Task<string> FirstLine(StreamReader reader) {
        var result = new StringBuilder(); var one = new char[1]; int n;
        while ((n = await reader.ReadAsync(one, 0, 1)) != 0) {
            if (one[0] == '\n') return result.ToString().TrimEnd('\r');
            if (result.Length < 32) result.Append(one[0]);
            else return "INVALID_READY";
        }
        return result.ToString();
    }
}
'@

function Start-Owned([string] $Arguments, [bool] $WaitReady = $false) {
    $info = [System.Diagnostics.ProcessStartInfo]::new()
    $info.FileName = $dotnet
    $info.Arguments = '"' + $brokerDll + '" ' + $Arguments
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $info
    if (-not $process.Start()) { throw 'process_start_failed' }
    # Begin draining both streams before waiting. READY is the first stdout line.
    $entry = [pscustomobject]@{ Process=$process; Err=[SovereignBoundedCapture]::Drain($process.StandardError, 4096); Out=$null; Ready=$null }
    $owned.Add($entry)
    if ($WaitReady) { $entry.Ready = [SovereignBoundedCapture]::FirstLine($process.StandardOutput) }
    else { $entry.Out = [SovereignBoundedCapture]::Drain($process.StandardOutput, 4096) }
    return $entry
}

function Show-Diagnostics([string] $label, [string] $content) {
    # Never print framework errors or untrusted response text.
    foreach ($line in ($content -split "`r?`n")) {
        if ($line -match '^IPC stage=(server_create|server_connect|server_read|peer_verification|server_write|client_connect|client_write|client_read|response_validation) category=(timeout|access_denied|native_error|io_error|invalid_data|operation_failed|invalid_protocol) code=-?\d+$') {
            Write-Host ($label + ' ' + $line)
        }
        elseif ($label -eq 'broker' -and $line -cmatch '^peer_sid_available=(true|false) expected_sid_available=(true|false) peer_sid_matches=(true|false) cleanup_verified=(true|false) reason=(EXPECTED_SID_UNAVAILABLE|TOKEN_ACQUISITION_FAILURE|IMPERSONATION_CLEANUP_FAILURE|PEER_SID_UNAVAILABLE|SID_MISMATCH|SID_MATCH)$') {
            Write-Host ($label + ' ' + $line)
        }
    }
}

function Wait-Owned($entry, [int] $Milliseconds) {
    $p = $entry.Process
    if (-not $p.WaitForExit($Milliseconds)) {
        $p.Kill()
        if (-not $p.WaitForExit(5000)) { throw 'owned_process_termination_timeout' }
        throw 'owned_process_timeout'
    }
    # ReadToEndAsync drains concurrently, preventing redirected-stream deadlocks.
    if ($null -eq $entry.Out) { $entry.Out = [SovereignBoundedCapture]::Drain($p.StandardOutput, 4096) }
    if (-not $entry.Out.Wait(2000) -or -not $entry.Err.Wait(2000)) { throw 'output_drain_timeout' }
    return @{ ExitCode=$p.ExitCode; Output=$entry.Out.Result; Error=$entry.Err.Result }
}

function Wait-Ready($entry) {
    $ready = $entry.Ready.Wait(7000)
    $line = if ($ready) { $entry.Ready.Result } else { $null }
    Write-Host ('broker_started=true broker_ready=' + ($line -ceq 'READY').ToString().ToLowerInvariant())
    if (-not $ready -or $line -cne 'READY') { throw 'server_readiness_failed' }
    $entry.Out = [SovereignBoundedCapture]::Drain($entry.Process.StandardOutput, 4096)
}

function Test-ResponseEnvelope($response) {
    if ($null -eq $response -or $response -isnot [pscustomobject]) { return $false }
    $names = @($response.PSObject.Properties.Name)
    if ($names.Count -ne 6 -or @('version','ok','development_only','trusted_broker','result','error').Where({ $_ -cnotin $names }).Count -ne 0) { return $false }
    if (($response.version -isnot [int] -and $response.version -isnot [long]) -or $response.version -ne 1) { return $false }
    if ($response.ok -isnot [bool] -or $response.development_only -isnot [bool] -or $response.trusted_broker -isnot [bool]) { return $false }
    if ($response.ok) {
        if ($response.result -isnot [string] -or $null -ne $response.error) { return $false }
    } elseif ($null -ne $response.result -or $response.error -isnot [string]) { return $false }
    return $response.development_only -eq $true -and $response.trusted_broker -eq $false
}

function Format-ResponseDiagnostics($response) {
    $version = if (($response.version -is [int] -or $response.version -is [long]) -and $response.version -eq 1) { '1' } else { 'invalid' }
    $ok = if ($response.ok -is [bool]) { $response.ok.ToString().ToLowerInvariant() } else { 'invalid' }
    $dev = if ($response.development_only -is [bool]) { $response.development_only.ToString().ToLowerInvariant() } else { 'invalid' }
    $trusted = if ($response.trusted_broker -is [bool]) { $response.trusted_broker.ToString().ToLowerInvariant() } else { 'invalid' }
    $value = if ($null -eq $response.result) { 'null' } elseif ($response.result -is [string] -and $response.result -cin @('pong','development_only','ping,status,capabilities')) { $response.result } else { 'unrecognized' }
    $errorValue = if ($null -eq $response.error) { 'null' } elseif ($response.error -is [string] -and $response.error -cin @('DENIED','REQUEST_TOO_LARGE','INVALID_REQUEST','UNSUPPORTED_COMMAND')) { $response.error } else { 'unrecognized' }
    return "response_json_parsed=true version=$version ok=$ok development_only=$dev trusted_broker=$trusted result=$value error=$errorValue"
}

function Invoke-Client([string] $Scenario) {
    $entry = Start-Owned ('client ' + $pipeName + ' ' + $Scenario)
    $result = Wait-Owned $entry 8000
    Write-Host ('client_scenario=' + $Scenario + ' client_exit_code=' + $result.ExitCode)
    Show-Diagnostics 'client' $result.Error
    # Exit 3 means the C# client validated a structured negative response.
    if ($result.ExitCode -notin @(0,3)) { Write-Host 'response_json_parsed=false_or_not_attempted'; throw ('client_failed_' + $Scenario) }
    if ($Scenario -in @('stall','disconnect')) { return $null }
    try { $response = $result.Output | ConvertFrom-Json -ErrorAction Stop }
    catch { Write-Host 'response_json_parsed=false'; Write-Host 'IPC stage=response_validation category=invalid_json'; throw 'response_validation_failed' }
    Write-Host (Format-ResponseDiagnostics $response)
    if (-not (Test-ResponseEnvelope $response)) {
        Write-Host 'IPC stage=response_validation category=invalid_envelope'
        throw 'response_validation_failed'
    }
    if (($result.ExitCode -eq 3 -and $response.ok -ne $false) -or ($result.ExitCode -eq 0 -and $response.ok -ne $true)) { throw 'response_validation_failed' }
    return $response
}

try {
    if (-not (Test-Path -LiteralPath $brokerDll -PathType Leaf)) { throw 'build_required' }
    $server = Start-Owned ('serve ' + $pipeName + ' 60') $true
    Wait-Ready $server
    $ping = Invoke-Client 'ping'
    if (-not $ping.ok -or $ping.result -cne 'pong') { Write-Host 'IPC stage=response_validation category=unexpected_ping'; throw 'ping_failed' }
    foreach ($scenario in @('status','capabilities')) {
        $result = Invoke-Client $scenario
        if (-not $result.ok) { throw ('invalid_response_' + $scenario) }
    }
    foreach ($pair in @(@('unsupported','UNSUPPORTED_COMMAND'), @('malformed','INVALID_REQUEST'), @('oversized','REQUEST_TOO_LARGE'))) {
        $result = Invoke-Client $pair[0]
        if ($result.ok -or $result.error -cne $pair[1]) { throw ('unexpected_response_' + $pair[0]) }
    }
    $duplicate = Start-Owned ('serve ' + $pipeName + ' 2')
    $dupResult = Wait-Owned $duplicate 5000
    Show-Diagnostics 'duplicate' $dupResult.Error
    if ($dupResult.ExitCode -eq 0) { throw 'duplicate_instance_started' }
    $null = Invoke-Client 'disconnect'
    $null = Invoke-Client 'stall'
    $null = Invoke-Client 'ping'
    $finished = Wait-Owned $server 65000
    Show-Diagnostics 'broker' $finished.Error
    if ($finished.ExitCode -ne 0) { throw 'broker_shutdown_failed' }
    $server2 = Start-Owned ('serve ' + $pipeName + ' 5') $true
    Wait-Ready $server2
    $null = Invoke-Client 'ping'
    $finished2 = Wait-Owned $server2 10000
    Show-Diagnostics 'restart' $finished2.Error
    if ($finished2.ExitCode -ne 0) { throw 'restart_shutdown_failed' }
    Write-Output 'PASS: development IPC scenarios completed; server identity is not authenticated.'
    exit 0
} catch {
    $failure = $_.Exception.Message
    if ($failure -notmatch '^(process_start_failed|owned_process_termination_timeout|owned_process_timeout|output_drain_timeout|server_readiness_failed|response_validation_failed|ping_failed|build_required|duplicate_instance_started|broker_shutdown_failed|restart_shutdown_failed|client_failed_[a-z]+|invalid_response_[a-z]+|unexpected_response_[a-z]+)$') { $failure = 'unexpected_runner_error' }
    Write-Output ('FAIL category=' + $failure)
    if ($null -ne $server) {
        $p = $server.Process
        $exited = $p.HasExited
        Write-Host ('broker_exited_prematurely=' + $exited.ToString().ToLowerInvariant())
        if (-not $exited) { $p.Kill(); $null = $p.WaitForExit(5000) }
        if ($p.HasExited) {
            Write-Host ('broker_exit_code=' + $p.ExitCode)
            if ($server.Err.Wait(2000)) { Show-Diagnostics 'broker' $server.Err.Result }
        }
    } else { Write-Host 'broker_started=false broker_ready=false' }
    exit 1
} finally {
    foreach ($entry in $owned) {
        $p = $entry.Process
        try { if (-not $p.HasExited) { $p.Kill(); $null = $p.WaitForExit(5000) } } catch { }
        $p.Dispose()
    }
}
