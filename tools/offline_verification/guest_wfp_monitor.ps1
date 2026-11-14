#requires -Version 7.0
[CmdletBinding()]
param(
    [ValidateSet('Inspect','Capture')] [string] $Mode = 'Inspect',
    [string] $ExpectedVmUuid,
    [string] $ExpectedComputerName,
    [string] $SnapshotReference,
    [switch] $ApproveGuestAuditChange,
    [ValidateRange(1,60)] [int] $CaptureSeconds = 15
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'wfp_event_parser.ps1')
if (-not $IsWindows -or $PSVersionTable.PSVersion.Major -lt 7) { throw 'unsupported_platform' }
if (-not $ExpectedVmUuid -or -not $ExpectedComputerName -or -not $SnapshotReference -or
    $SnapshotReference -notmatch '^[A-Za-z0-9][A-Za-z0-9_.-]{0,63}$') { throw 'missing_reviewed_vm_or_snapshot' }
$expected = [guid]::Empty
if (-not [guid]::TryParse($ExpectedVmUuid, [ref]$expected) -or $expected -eq [guid]::Empty) { throw 'invalid_vm_uuid' }
$computer = Get-CimInstance Win32_ComputerSystem -OperationTimeoutSec 5
$product = Get-CimInstance Win32_ComputerSystemProduct -OperationTimeoutSec 5
if ($computer.Model -cne 'VirtualBox' -or $product.UUID -ine $expected.ToString() -or
    [Environment]::MachineName -ine $ExpectedComputerName) { throw 'vm_identity_mismatch' }
# A guest cannot prove hypervisor isolation or snapshot recoverability. Require
# independent, per-run human confirmation in addition to guest observations.
$active = @(Get-NetAdapter -ErrorAction Stop | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -notmatch 'Loopback' })
if ($active.Count -ne 0) { throw 'guest_adapter_active' }
if ((Read-Host 'Type ISOLATED after independently checking hypervisor isolation and snapshot') -cne 'ISOLATED') { throw 'isolation_not_confirmed' }
$subcategory = 'Filtering Platform Connection'
function Read-AuditState {
    $output = & "$env:SystemRoot\System32\auditpol.exe" /get "/subcategory:$subcategory" 2>&1
    if ($LASTEXITCODE -ne 0) { throw 'audit_query_failed' }
    $matches = @($output | Where-Object { $_ -match '^\s*Filtering Platform Connection\s+(No Auditing|Success|Failure|Success and Failure)\s*$' })
    if ($matches.Count -ne 1) { throw 'audit_state_unrecognized' }
    $null = $matches[0] -match '^\s*Filtering Platform Connection\s+(No Auditing|Success|Failure|Success and Failure)\s*$'
    return $Matches[1]
}
$initial = Read-AuditState
$profiles = @(Get-NetFirewallProfile -ErrorAction Stop | Select-Object Name, Enabled, DefaultOutboundAction)
Write-Host ('AUDIT original=' + $initial)
foreach ($profile in $profiles) { Write-Host ('FIREWALL profile=' + $profile.Name + ' enabled=' + $profile.Enabled + ' outbound=' + $profile.DefaultOutboundAction) }
Write-Host 'COVERAGE=UNKNOWN hypervisor isolation and firewall effective policy require independent verification.'
if ($Mode -eq 'Inspect') { exit 0 }
if (-not $ApproveGuestAuditChange) { throw 'approval_required' }
if ((Read-Host 'Type ENABLE GUEST AUDIT to change connection failure auditing for this run') -cne 'ENABLE GUEST AUDIT') { throw 'approval_refused' }
$changed = $false
try {
    if ($initial -notin @('Failure','Success and Failure')) {
        $changed = $true # Set before native call: a failed call can have an ambiguous outcome.
        & "$env:SystemRoot\System32\auditpol.exe" /set "/subcategory:$subcategory" /failure:enable | Out-Null
        if ($LASTEXITCODE -ne 0) { throw 'audit_enable_failed' }
    }
    if ((Read-AuditState) -notin @('Failure','Success and Failure')) { throw 'audit_enable_unconfirmed' }
    $start = [datetime]::UtcNow
    Write-Host 'MONITOR active; no stimulus or application tests will be launched. Coverage remains UNKNOWN.'
    Start-Sleep -Seconds $CaptureSeconds
    try { $events = @(Get-WinEvent -FilterHashtable @{LogName='Security'; Id=5157; StartTime=$start} -MaxEvents 500 -ErrorAction Stop) }
    catch [System.Exception] {
        if ($_.FullyQualifiedErrorId -like 'NoMatchingEventsFound*') { $events = @() } else { throw 'event_collection_failed' }
    }
    $invalid = 0
    foreach ($event in $events) {
        try {
            $record = ConvertFrom-SovereignWfpEvent ($event.ToXml())
            Write-Host ('EVENT ' + (ConvertTo-Json -InputObject $record -Compress))
        } catch { $invalid++; Write-Host 'EVENT invalid_or_unattributed' }
    }
    if ($invalid -gt 0) { throw 'event_validation_failed' }
    if ($events.Count -ge 500) { Write-Host 'MONITOR event_limit_reached coverage=UNKNOWN' }
    Write-Host ('MONITOR events=' + $events.Count + ' coverage=UNKNOWN')
} finally {
    try {
        Restore-SovereignAuditState $initial $changed { Read-AuditState } {
            & "$env:SystemRoot\System32\auditpol.exe" /set "/subcategory:$subcategory" /failure:disable | Out-Null
            return $LASTEXITCODE -eq 0
        }
    } catch { Write-Host 'RESTORATION UNCONFIRMED; restore reviewed VM snapshot'; throw 'audit_restore_failed' }
    Write-Host 'RESTORATION confirmed for selected audit subcategory; snapshot remains authoritative.'
}
