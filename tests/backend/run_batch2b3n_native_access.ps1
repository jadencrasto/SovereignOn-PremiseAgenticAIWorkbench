[CmdletBinding()]
param(
    [switch] $ApproveDisposableVm,
    [string] $Workspace,
    [string] $VmUuid,
    [string] $ComputerName,
    [string] $ProbeHashesJson,
    [ValidateSet('application','broker','independent')] [string] $Role,
    [string] $ExpectedCallerSid,
    [switch] $ProtectedFixture,
    [string] $ExpectedManifestSha256,
    [switch] $ExerciseSyntheticWrites
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'batch2b3lmn_vm_probe.ps1')
try {
    if (-not $ApproveDisposableVm.IsPresent) { throw 'approval_required' }
    if (-not (Test-LmnLocalDirectory $Workspace)) { throw 'invalid_vm_prerequisites' }
    if ($ExerciseSyntheticWrites.IsPresent -and (-not $ProtectedFixture.IsPresent -or $Role -ne 'broker')) { throw 'invalid_vm_prerequisites' }
    if ($ProtectedFixture.IsPresent) {
        if ($ExpectedManifestSha256 -notmatch '^[a-f0-9A-F]{64}$') { throw 'invalid_expected_digest' }
        $manifest = Get-Item -LiteralPath (Join-Path $Workspace 'expected-protected-service.json') -ErrorAction Stop
        if ($manifest.PSIsContainer -or $manifest.Length -gt 65536 -or ($manifest.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
            throw 'invalid_manifest_path'
        }
        $mode = if ($ExerciseSyntheticWrites.IsPresent) { 'exercise-protected-storage' } else { 'probe-protected-storage' }
        $approval = if ($ExerciseSyntheticWrites.IsPresent) { '--approved-vm-synthetic-write' } else { '--approved-vm-observation' }
        $arguments = @($mode, $approval,
            (Join-Path $Workspace 'synthetic-storage-k'), $Role, $ExpectedCallerSid, $manifest.FullName, $ExpectedManifestSha256)
    } else {
        if ($Role -eq 'independent' -or $ExpectedManifestSha256) { throw 'invalid_vm_prerequisites' }
        $arguments = @('probe-storage', '--approved-vm-observation', (Join-Path $Workspace 'synthetic-storage-j4'), $Role, $ExpectedCallerSid)
    }
    $result = Invoke-LmnProbe $true $Workspace $VmUuid $ComputerName $ProbeHashesJson $arguments
    Write-Output $result.Line
    if ($result.Code -ne 0 -or $result.Line -cne 'STORAGE observation=ExpectedAccess authority=false') { throw 'unexpected_observation' }
    exit 0
}
catch { Write-LmnFailure $_.Exception.Message; exit 1 }
