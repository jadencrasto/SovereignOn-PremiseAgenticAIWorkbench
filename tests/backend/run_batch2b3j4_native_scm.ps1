[CmdletBinding()]
param(
    [switch] $ApproveDisposableVm,
    [string] $Workspace,
    [string] $ExpectedVmUuid,
    [string] $ExpectedComputerName,
    [string] $ExpectedHashesJson
)

function Invoke-J4Plan {
    param([bool] $Approved, [bool] $WindowsHost, [int] $PowerShellMajor, [hashtable] $Ops)
    Write-Host 'J4_DIAG phase=prerequisites'
    if (-not $Approved) { throw 'approval_required' }
    if (-not $WindowsHost -or $PowerShellMajor -lt 7) { throw 'unsupported_platform' }
    if (-not (& $Ops.Prerequisites)) { throw 'invalid_vm_prerequisites' }
    $binary = & $Ops.ReviewBinary
    Write-Host ('J4 executable_sha256=' + $binary.Hash)
    $existing = & $Ops.Query
    if ($existing.Exists) { throw 'service_name_conflict' }

    $created = $false
    $createAttempted = $false
    $verifyOwned = {
        if (-not (& $Ops.Owned $binary)) { throw 'configuration_mismatch' }
    }
    try {
        Write-Host 'J4_DIAG phase=registration'
        $createAttempted = $true
        if ((& $Ops.Create $binary).Code -ne 0) { throw 'create_failed' }
        $created = $true
        & $verifyOwned
        Write-Host 'PASS registration and installed configuration'

        foreach ($scenario in @('initial', 'restart')) {
            Write-Host "J4_DIAG phase=$scenario"
            & $verifyOwned
            if ((& $Ops.Start '').Code -ne 0) { throw 'start_failed' }
            $null = & $Ops.Wait 4 'startup_timeout'
            & $verifyOwned
            if ((& $Ops.Stop).Code -ne 0) { throw 'stop_failed' }
            $state = & $Ops.Wait 1 'stop_timeout'
            if ($state.Win32 -ne 0 -or $state.Specific -ne 0) { throw 'unexpected_exit_status' }
            Write-Host "PASS $scenario RUNNING and normal STOPPED"
        }
        Write-Host 'J4_DIAG phase=repeated_stop'
        & $verifyOwned
        if ((& $Ops.Stop).Code -ne 1062) { throw 'unexpected_repeated_stop_status' }
        Write-Host 'PASS repeated STOP on stopped service'

        Write-Host 'J4_DIAG phase=process_exit'
        & $verifyOwned
        if ((& $Ops.Start '--j4-exit-process').Code -ne 0) { throw 'start_failed' }
        $null = & $Ops.Wait 4 'startup_timeout'
        $state = & $Ops.Wait 1 'exit_timeout'
        if ($state.Win32 -ne 1067 -or $state.Specific -ne 0) { throw 'unexpected_process_exit_status' }
        Write-Host 'PASS unexpected process exit detected'

        foreach ($case in @(
            @{ Argument = '--j4-fail-initialization'; Code = 7; Label = 'initialization failure' },
            @{ Argument = '--j4-invalid-configuration'; Code = 1; Label = 'invalid startup configuration' }
        )) {
            if ($case.Code -eq 7) { Write-Host 'J4_DIAG phase=initialization_failure' } else { Write-Host 'J4_DIAG phase=invalid_configuration' }
            & $verifyOwned
            if ((& $Ops.Start $case.Argument).Code -notin @(0, 1066, 1067)) { throw 'unexpected_start_status' }
            $state = & $Ops.Wait 1 'exit_timeout'
            if ($state.Win32 -ne 1066 -or $state.Specific -ne $case.Code) { throw 'unexpected_exit_status' }
            Write-Host ('PASS ' + $case.Label)
        }

        Write-Host 'J4_DIAG phase=initialization_pending'
        & $verifyOwned
        if ((& $Ops.Start '--j4-hold-initialization').Code -ne 0) { throw 'start_failed' }
        $null = & $Ops.Wait 2 'startup_timeout'
        & $verifyOwned
        Invoke-J4PendingStop $Ops
        Write-Host 'PASS SCM rejected STOP in START_PENDING and initialization deadline was reported'
    }
    catch {
        $reason = $_.Exception.Message
        if ($reason -cnotmatch '^(create_failed|configuration_mismatch|start_failed|stop_failed|startup_stopped|startup_timeout|stop_timeout|exit_timeout|unexpected_exit_status|unexpected_repeated_stop_status|unexpected_start_status|unexpected_pending_state|unexpected_pending_control_status|unexpected_process_exit_status|query_failed|unrecognized_scm_status|service_disappeared|sc_deadline|sc_launch_failed|sc_output_deadline|sc_cleanup_failed)$') { $reason = 'unexpected_failure' }
        Write-Host "J4_DIAG failure=$reason"
        throw
    }
    finally {
        Write-Host 'J4_DIAG phase=cleanup'
        if ($created) {
            try {
                & $verifyOwned
                $state = & $Ops.Query
                if (-not $state.Exists) { throw 'service_disappeared' }
                if ($state.State -ne 1) {
                    $null = & $Ops.Stop
                    $null = & $Ops.Wait 1 'stop_timeout'
                }
                if (-not (& $Ops.Owned $binary) -or (& $Ops.Delete).Code -ne 0) { throw 'delete_failed' }
                $null = & $Ops.Wait 0 'delete_timeout'
                Write-Host 'PASS cleanup confirmed service absent'
            }
            catch { throw 'cleanup_failed_snapshot_reversion_required' }
        }
        elseif ($createAttempted) {
            try {
                if ((& $Ops.Query).Exists) { throw 'unconfirmed_service_owner' }
            }
            catch { throw 'creation_outcome_unknown_snapshot_reversion_required' }
        }
    }
}

function Invoke-J4PendingStop([hashtable] $Ops) {
    $before = & $Ops.Query
    if (-not $before.Exists -or $before.State -ne 2) { throw 'unexpected_pending_state' }
    Write-Host "J4_DIAG state=$($before.State) win32=$($before.Win32) specific=$($before.Specific)"
    $reply = & $Ops.Stop
    if ($reply.Code -notin @(1052, 1061)) { throw 'unexpected_pending_control_status' }
    $after = & $Ops.Query
    if (-not $after.Exists -or $after.State -notin @(1, 2)) { throw 'unexpected_pending_state' }
    Write-Host "J4_DIAG state=$($after.State) win32=$($after.Win32) specific=$($after.Specific)"
    if ($after.State -eq 1) { $state = $after } else { $state = & $Ops.Wait 1 'exit_timeout' }
    if (-not $state.Exists -or $state.State -ne 1 -or $state.Win32 -ne 1066 -or $state.Specific -ne 13) { throw 'unexpected_exit_status' }
}

function Invoke-J4Sc {
    param([string[]] $Arguments)
    $info = [Diagnostics.ProcessStartInfo]::new((Join-Path ([Environment]::SystemDirectory) 'sc.exe'))
    $info.UseShellExecute = $false
    $info.CreateNoWindow = $true
    $info.RedirectStandardOutput = $true
    $info.RedirectStandardError = $true
    foreach ($argument in $Arguments) { $info.ArgumentList.Add($argument) }
    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $info
    $started = $false
    try {
        $started = $process.Start()
        if (-not $started) { throw 'sc_launch_failed' }
        $stdout = $process.StandardOutput.ReadToEndAsync()
        $stderr = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(8000)) { throw 'sc_deadline' }
        if (-not $stdout.Wait(1000) -or -not $stderr.Wait(1000)) { throw 'sc_output_deadline' }
        $operation = if ($Arguments[0] -cin @('create','start','stop','delete','queryex')) { $Arguments[0] } else { 'unknown' }
        if ($operation -ne 'queryex' -or $process.ExitCode -ne 0) { Write-Host "J4_DIAG operation=$operation code=$($process.ExitCode)" }
        return @{ Code = $process.ExitCode; Output = $stdout.Result }
    }
    finally {
        try {
            if ($started -and -not $process.HasExited) {
                $process.Kill()
                if (-not $process.WaitForExit(2000)) { throw 'sc_cleanup_failed' }
            }
        }
        finally { $process.Dispose() }
    }
}

function ConvertFrom-J4Status($reply) {
    if ($reply.Code -eq 1060) { return @{ Exists = $false; State = 0 } }
    if ($reply.Code -ne 0) { throw 'query_failed' }
    $state = [regex]::Match($reply.Output, '(?m)^\s*STATE\s*:\s*([1-7])\b')
    $win32 = [regex]::Match($reply.Output, '(?m)^\s*WIN32_EXIT_CODE\s*:\s*(\d+)\b')
    $specific = [regex]::Match($reply.Output, '(?m)^\s*SERVICE_EXIT_CODE\s*:\s*(\d+)\b')
    if (-not ($state.Success -and $win32.Success -and $specific.Success)) { throw 'unrecognized_scm_status' }
    $result = @{ Exists = $true; State = [int]$state.Groups[1].Value; Win32 = [uint32]$win32.Groups[1].Value; Specific = [uint32]$specific.Groups[1].Value }
    return $result
}

function Wait-J4Status {
    param([scriptblock] $Query, [int] $State, [string] $TimeoutCategory, [int] $Milliseconds = 12000)
    $timer = [Diagnostics.Stopwatch]::StartNew()
    do {
        $status = & $Query
        if ($status.State -eq $State) { Write-Host "J4_DIAG state=$($status.State) win32=$($status.Win32) specific=$($status.Specific)"; return $status }
        if (-not $status.Exists -and $State -ne 0) { throw 'service_disappeared' }
        if ($State -eq 4 -and $status.State -eq 1) { Write-Host "J4_DIAG state=$($status.State) win32=$($status.Win32) specific=$($status.Specific)"; throw 'startup_stopped' }
        Start-Sleep -Milliseconds 75
    } while ($timer.ElapsedMilliseconds -lt $Milliseconds)
    Write-Host "J4_DIAG state=$($status.State) win32=$($status.Win32) specific=$($status.Specific)"
    throw $TimeoutCategory
}

function Test-J4LocalDirectory([string] $Path) {
    if (-not [IO.Path]::IsPathFullyQualified($Path) -or $Path -notmatch '^[a-zA-Z]:[\\/]') { return $false }
    $item = Get-Item -LiteralPath $Path -ErrorAction Stop
    if (-not $item.PSIsContainer -or $null -eq $item.Parent -or $item.FullName.Contains('"')) { return $false }
    $drive = [IO.DriveInfo]::new([IO.Path]::GetPathRoot($item.FullName))
    if ($drive.DriveType -ne [IO.DriveType]::Fixed) { return $false }
    while ($null -ne $item) {
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { return $false }
        $item = $item.Parent
    }
    return $true
}

function Test-J4VmPrerequisites([string] $Root, [string] $Uuid, [string] $ComputerName) {
    $id = [Guid]::Empty
    if ([string]::IsNullOrWhiteSpace($ComputerName) -or $ComputerName -ine [Environment]::MachineName -or
        -not [Guid]::TryParse($Uuid, [ref]$id) -or $id -eq [Guid]::Empty) { return $false }
    if (-not (Test-J4LocalDirectory $Root)) { return $false }
    $marker = Get-Item -LiteralPath (Join-Path $Root '.sovereign-j4-disposable-vm') -ErrorAction Stop
    if ($marker.PSIsContainer -or ($marker.Attributes -band [IO.FileAttributes]::ReparsePoint) -or $marker.Length -gt 128) { return $false }
    if ([IO.File]::ReadAllText($marker.FullName).Trim() -ine $id.ToString()) { return $false }
    $system = Get-CimInstance Win32_ComputerSystem -OperationTimeoutSec 5 -ErrorAction Stop
    if ($system.Model -notmatch '^(Virtual Machine|VMware Virtual Platform|VirtualBox|KVM|QEMU)(\b|$)') { return $false }
    $product = Get-CimInstance Win32_ComputerSystemProduct -OperationTimeoutSec 5 -ErrorAction Stop
    if ($product.UUID -ine $id.ToString()) { return $false }
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    try {
        return ([Security.Principal.WindowsPrincipal]::new($identity)).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    }
    finally { $identity.Dispose() }
}

function Open-J4ReviewedBinary([string] $Root, [string] $Hashes, [Collections.Generic.List[IDisposable]] $Locks) {
    $expected = ConvertFrom-Json -InputObject $Hashes -AsHashtable -ErrorAction Stop
    $files = @('ServiceFoundation.exe', 'ServiceFoundation.dll', 'ServiceFoundation.runtimeconfig.json', 'ServiceFoundation.deps.json')
    if ($expected -isnot [System.Collections.IDictionary] -or $expected.Count -ne $files.Count) { throw 'invalid_hash_manifest' }
    foreach ($file in $files) {
        if ($expected[$file] -isnot [string] -or $expected[$file] -notmatch '^[0-9a-fA-F]{64}$') { throw 'invalid_hash_manifest' }
        $item = Get-Item -LiteralPath (Join-Path $Root $file) -ErrorAction SilentlyContinue
        if ($null -eq $item -or $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) { throw 'invalid_executable' }
        $stream = [IO.File]::Open($item.FullName, 'Open', 'Read', 'Read')
        $Locks.Add($stream)
        $hash = [Convert]::ToHexString([Security.Cryptography.SHA256]::HashData($stream))
        if ($hash -ine $expected[$file]) { throw 'hash_mismatch' }
    }
    $path = Join-Path ([IO.Path]::GetFullPath($Root)) 'ServiceFoundation.exe'
    return @{ Image = ('"' + $path + '" service'); Hash = $expected['ServiceFoundation.exe'].ToUpperInvariant() }
}

if ($MyInvocation.InvocationName -eq '.') { return }
$ErrorActionPreference = 'Stop'
$service = 'SovereignDevBrokerFoundation'
$locks = [Collections.Generic.List[IDisposable]]::new()
$query = { ConvertFrom-J4Status (Invoke-J4Sc @('queryex', $service)) }
$ops = @{
    Prerequisites = { try { Test-J4VmPrerequisites $Workspace $ExpectedVmUuid $ExpectedComputerName } catch { $false } }
    ReviewBinary = { Open-J4ReviewedBinary $Workspace $ExpectedHashesJson $locks }
    Query = $query
    Create = { param($binary) Invoke-J4Sc @('create', $service, 'binPath=', $binary.Image, 'type=', 'own', 'start=', 'demand', 'obj=', 'NT AUTHORITY\LocalService') }
    Owned = {
        param($binary)
        $config = Get-ItemProperty -LiteralPath "HKLM:\SYSTEM\CurrentControlSet\Services\$service" -ErrorAction SilentlyContinue
        return $null -ne $config -and $config.ImagePath -ceq $binary.Image -and $config.Type -eq 16 -and
            $config.Start -eq 3 -and $config.ObjectName -ieq 'NT AUTHORITY\LocalService'
    }
    Start = { param($argument) if ($argument) { Invoke-J4Sc @('start', $service, $argument) } else { Invoke-J4Sc @('start', $service) } }
    Stop = { Invoke-J4Sc @('stop', $service) }
    Delete = { Invoke-J4Sc @('delete', $service) }
    Wait = { param($state, $category) Wait-J4Status $query $state $category }
}
try {
    Invoke-J4Plan $ApproveDisposableVm.IsPresent ($IsWindows -eq $true) $PSVersionTable.PSVersion.Major $ops
    Write-Output 'PASS native SCM lifecycle and cleanup'
    exit 0
}
catch {
    $category = $_.Exception.Message
    if ($category -cnotmatch '^(approval_required|unsupported_platform|invalid_vm_prerequisites|invalid_hash_manifest|invalid_executable|hash_mismatch|service_name_conflict|create_failed|configuration_mismatch|start_failed|stop_failed|startup_stopped|startup_timeout|stop_timeout|exit_timeout|unexpected_exit_status|unexpected_repeated_stop_status|unexpected_start_status|unexpected_pending_state|unexpected_pending_control_status|unexpected_process_exit_status|query_failed|unrecognized_scm_status|service_disappeared|sc_deadline|sc_launch_failed|sc_output_deadline|sc_cleanup_failed|cleanup_failed_snapshot_reversion_required|creation_outcome_unknown_snapshot_reversion_required)$') { $category = 'unexpected_failure' }
    Write-Output "FAIL category=$category"
    exit 1
}
finally { foreach ($handle in $locks) { $handle.Dispose() } }
