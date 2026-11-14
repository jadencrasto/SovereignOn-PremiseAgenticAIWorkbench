$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'run_batch2b3j4_native_scm.ps1')

$script:passed = 0
$script:failed = 0
function Test-Case([string] $Name, [scriptblock] $Test) {
    try { & $Test; $script:passed++; Write-Host "PASS $Name" }
    catch { $script:failed++; Write-Host "FAIL $Name ($($_.Exception.Message))" }
}
function Assert-J4([bool] $Condition, [string] $Message) {
    if (-not $Condition) { throw $Message }
}
function Expect-J4Error([scriptblock] $Action, [string] $Category) {
    try { & $Action }
    catch { Assert-J4 ($_.Exception.Message -ceq $Category) ('expected ' + $Category + ', got ' + $_.Exception.Message); return }
    throw ('missing ' + $Category)
}

function New-J4Mock([string] $Fault) {
    $m = @{ Fault=$Fault; Present=$false; State=1; Mode=''; Win32=0; Specific=0; Calls=[Collections.Generic.List[string]]::new() }
    $m.Present = $Fault -eq 'conflict'
    $ops = @{
        Prerequisites = { $m.Calls.Add('prerequisites'); return $m.Fault -ne 'prerequisites' }.GetNewClosure()
        ReviewBinary = {
            $m.Calls.Add('review')
            if ($m.Fault -eq 'binary') { throw 'invalid_executable' }
            if ($m.Fault -eq 'hash') { throw 'hash_mismatch' }
            return @{ Hash = 'A' * 64; Image = 'synthetic' }
        }.GetNewClosure()
        Query = { $m.Calls.Add('query'); return @{ Exists=$m.Present; State=$m.State; Win32=$m.Win32; Specific=$m.Specific } }.GetNewClosure()
        Create = {
            param($binary)
            $m.Calls.Add('create')
            if ($m.Fault -eq 'create') { return @{ Code=5 } }
            if ($m.Fault -eq 'uncertain_create') { $m.Present=$true; throw 'sc_deadline' }
            $m.Present=$true
            return @{ Code=0 }
        }.GetNewClosure()
        Owned = { param($binary) $m.Calls.Add('owned'); return $m.Fault -ne 'replaced' }.GetNewClosure()
        Start = {
            param($mode)
            $m.Calls.Add('start:' + $mode)
            $m.Mode=$mode
            $m.Win32=0
            $m.Specific=0
            switch ($mode) {
                '--j4-hold-initialization' { $m.State=2 }
                '--j4-fail-initialization' { $m.State=1; $m.Win32=1066; $m.Specific=7 }
                '--j4-invalid-configuration' { $m.State=1; $m.Win32=1066; $m.Specific=1 }
                default { $m.State=4 }
            }
            return @{ Code=0 }
        }.GetNewClosure()
        Stop = {
            $m.Calls.Add('stop')
            if ($m.State -eq 1) { return @{ Code=1062 } }
            if ($m.State -eq 2) { return @{ Code=1061 } }
            if ($m.Fault -eq 'stop_timeout' -and $m.Calls.FindAll([Predicate[string]]{param($s) $s -eq 'stop'}).Count -eq 1) { return @{ Code=0 } }
            $m.State=1
            return @{ Code=0 }
        }.GetNewClosure()
        Wait = {
            param($state, $category)
            $m.Calls.Add('wait:' + $state)
            if ($m.Fault -eq 'startup_timeout' -and $state -eq 4) { throw $category }
            if ($state -eq 1 -and $m.Mode -eq '--j4-exit-process') { $m.State=1; $m.Win32=1067 }
            if ($state -eq 1 -and $m.Mode -eq '--j4-hold-initialization') { $m.State=1; $m.Win32=1066; $m.Specific=13 }
            if ($m.State -ne $state) { throw $category }
            return @{ Exists=$m.Present; State=$m.State; Win32=$m.Win32; Specific=$m.Specific }
        }.GetNewClosure()
        Delete = {
            $m.Calls.Add('delete')
            if ($m.Fault -eq 'delete') { return @{ Code=5 } }
            $m.Present=$false; $m.State=0
            return @{ Code=0 }
        }.GetNewClosure()
    }
    return @{ State=$m; Ops=$ops }
}

Test-Case 'approval required before any operation' {
    $mock = New-J4Mock ''
    Expect-J4Error { Invoke-J4Plan $false $true 7 $mock.Ops } 'approval_required'
    Assert-J4 ($mock.State.Calls.Count -eq 0) 'preflight made a call'
}
Test-Case 'unsupported platform and PowerShell' {
    $mock = New-J4Mock ''
    Expect-J4Error { Invoke-J4Plan $true $false 7 $mock.Ops } 'unsupported_platform'
    Expect-J4Error { Invoke-J4Plan $true $true 5 $mock.Ops } 'unsupported_platform'
    Assert-J4 ($mock.State.Calls.Count -eq 0) 'preflight made a call'
}
foreach ($case in @(
    @('prerequisites','invalid_vm_prerequisites'), @('binary','invalid_executable'), @('hash','hash_mismatch'),
    @('conflict','service_name_conflict'), @('create','create_failed'),
    @('uncertain_create','creation_outcome_unknown_snapshot_reversion_required'),
    @('startup_timeout','startup_timeout'), @('stop_timeout','stop_timeout'),
    @('delete','cleanup_failed_snapshot_reversion_required'), @('replaced','cleanup_failed_snapshot_reversion_required')
)) {
    Test-Case $case[0] {
        $mock = New-J4Mock $case[0]
        Expect-J4Error { Invoke-J4Plan $true $true 7 $mock.Ops } $case[1]
        $calls = $mock.State.Calls
        if ($case[0] -in @('prerequisites','binary','hash','conflict')) { Assert-J4 ('create' -notin $calls) 'unsafe create attempted' }
        if ($case[0] -in @('create','uncertain_create','replaced')) { Assert-J4 ('delete' -notin $calls -and 'stop' -notin $calls) 'touched unowned service' }
        if ($case[0] -in @('startup_timeout','stop_timeout')) { Assert-J4 ('delete' -in $calls -and -not $mock.State.Present) 'owned cleanup missing' }
        if ($case[0] -eq 'delete') { Assert-J4 ($mock.State.Present) 'failed deletion claimed success' }
    }
}
Test-Case 'successful orchestration exercises each native scenario' {
    $mock = New-J4Mock ''
    Invoke-J4Plan $true $true 7 $mock.Ops
    $calls = $mock.State.Calls
    foreach ($mode in @('--j4-exit-process','--j4-fail-initialization','--j4-invalid-configuration','--j4-hold-initialization')) {
        Assert-J4 (('start:' + $mode) -in $calls) 'missing scenario'
    }
    Assert-J4 (@($calls | Where-Object { $_ -ceq 'start:' }).Count -eq 2) 'restart not exercised'
    Assert-J4 (-not $mock.State.Present -and $calls[-1] -eq 'wait:0') 'deletion not confirmed'
}
Test-Case 'status parser fails closed' {
    $status = ConvertFrom-J4Status @{Code=0;Output="STATE : 1  STOPPED`nWIN32_EXIT_CODE : 1066`nSERVICE_EXIT_CODE : 7"}
    Assert-J4 ($status.Exists -and $status.State -eq 1 -and $status.Win32 -eq 1066 -and $status.Specific -eq 7) 'incorrect status mapping'
    Assert-J4 (-not (ConvertFrom-J4Status @{Code=1060}).Exists) 'missing service misclassified'
    Expect-J4Error { ConvertFrom-J4Status @{Code=5} } 'query_failed'
    Expect-J4Error { ConvertFrom-J4Status @{Code=0;Output='STATE : 4'} } 'unrecognized_scm_status'
}
foreach ($code in @(1052, 1061)) {
    Test-Case "pending STOP rejects control code $code and confirms stopped" {
        $script:pendingCode = $code
        $state = @{ Queries=0; Waits=0 }
        $ops = @{
            Query = { $state.Queries++; return @{Exists=$true;State=2} }.GetNewClosure()
            Stop = { return @{Code=$script:pendingCode} }
            Wait = { param($target,$category) $state.Waits++; return @{Exists=$true;State=1;Win32=1066;Specific=13} }.GetNewClosure()
        }
        Invoke-J4PendingStop $ops
        Assert-J4 ($state.Queries -eq 2 -and $state.Waits -eq 1) 'missing state checks'
    }
}
Test-Case 'pending STOP rejects success, other errors, wrong states and bad final status' {
    $state = @{ Before=2; After=2; Code=1052; Final=13; Queries=0 }
    $ops = @{
        Query = { $state.Queries++; $value = if ($state.Queries -eq 1) { $state.Before } else { $state.After }; return @{Exists=$true;State=$value;Win32=1066;Specific=$state.Final} }.GetNewClosure()
        Stop = { return @{Code=$state.Code} }.GetNewClosure()
        Wait = { param($target,$category) return @{Exists=$true;State=1;Win32=1066;Specific=$state.Final} }.GetNewClosure()
    }
    foreach ($code in @(0, 5, 1062)) { $state.Code=$code; $state.Queries=0; Expect-J4Error { Invoke-J4PendingStop $ops } 'unexpected_pending_control_status' }
    $state.Code=1052; $state.Before=4; $state.Queries=0; Expect-J4Error { Invoke-J4PendingStop $ops } 'unexpected_pending_state'
    $state.Before=2; $state.After=4; $state.Queries=0; Expect-J4Error { Invoke-J4PendingStop $ops } 'unexpected_pending_state'
    $state.After=2; $state.Final=7; $state.Queries=0; Expect-J4Error { Invoke-J4PendingStop $ops } 'unexpected_exit_status'
    $state.After=1; $state.Final=13; $state.Queries=0; Invoke-J4PendingStop $ops
}
Test-Case 'pending STOP rejects missing service and bounded wait failure' {
    $ops = @{ Query={ @{Exists=$false;State=0} }; Stop={ @{Code=1052} }; Wait={ throw 'exit_timeout' } }
    Expect-J4Error { Invoke-J4PendingStop $ops } 'unexpected_pending_state'
    $ops.Query={ @{Exists=$true;State=2} }
    Expect-J4Error { Invoke-J4PendingStop $ops } 'exit_timeout'
}
Test-Case 'bounded polling rejects stalled startup and stop' {
    Expect-J4Error { Wait-J4Status { @{ Exists=$true; State=2 } } 4 'startup_timeout' 0 } 'startup_timeout'
    Expect-J4Error { Wait-J4Status { @{ Exists=$true; State=3 } } 1 'stop_timeout' 0 } 'stop_timeout'
}
Test-Case 'actual workspace checks reject absent prerequisites' {
    Assert-J4 (-not (Test-J4VmPrerequisites '' '' '')) 'missing input accepted'
    Assert-J4 (-not (Test-J4LocalDirectory 'relative-path')) 'relative workspace accepted'
}
Test-Case 'actual binary validation rejects missing file and hash mismatch' {
    $dir = [IO.Directory]::CreateTempSubdirectory('sovereign-j4-test-')
    $locks = [Collections.Generic.List[IDisposable]]::new()
    $binary = Join-Path $dir.FullName 'ServiceFoundation.exe'
    try {
        $hashes = @{}
        foreach ($file in @('ServiceFoundation.exe','ServiceFoundation.dll','ServiceFoundation.runtimeconfig.json','ServiceFoundation.deps.json')) { $hashes[$file]='0' * 64 }
        $json = $hashes | ConvertTo-Json -Compress
        Expect-J4Error { Open-J4ReviewedBinary $dir.FullName $json $locks } 'invalid_executable'
        [IO.File]::WriteAllText($binary, 'synthetic')
        Expect-J4Error { Open-J4ReviewedBinary $dir.FullName $json $locks } 'hash_mismatch'
    }
    finally {
        foreach ($handle in $locks) { $handle.Dispose() }
        [IO.File]::Delete($binary)
        $dir.Delete()
    }
}
Write-Output "J4 offline tests: $script:passed passed, $script:failed failed"
if ($script:failed -gt 0) { exit 1 }
