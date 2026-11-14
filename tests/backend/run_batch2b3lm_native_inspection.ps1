[CmdletBinding()]
param(
    [switch] $ApproveDisposableVm,
    [string] $Workspace,
    [string] $VmUuid,
    [string] $ComputerName,
    [string] $ProbeHashesJson,
    [ValidateSet('observe-identity','observe-endpoint')] [string] $Probe = 'observe-identity',
    [string] $ExpectedManifestSha256,
    [switch] $ProtectedFixture,
    [ValidateSet('VerifiedEvidence','MissingEvidence','MismatchedIdentity','InvalidConfiguration','UntrustedInstallation','UnexpectedProcess','InspectionFailure','NativeUnavailable','EndpointUnavailable','UnexpectedEndpoint','UnsafeEndpoint','ChangedBinding','Cancelled')]
    [string] $ExpectedFinding = 'VerifiedEvidence'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'batch2b3lmn_vm_probe.ps1')
try {
    if (-not $ApproveDisposableVm.IsPresent) { throw 'approval_required' }
    if ($ExpectedManifestSha256 -notmatch '^[a-f0-9A-F]{64}$') { throw 'invalid_expected_digest' }
    if (-not (Test-LmnLocalDirectory $Workspace)) { throw 'invalid_vm_prerequisites' }
    $manifestName = if ($ProtectedFixture.IsPresent) { 'expected-protected-service.json' } else { 'expected-service.json' }
    $manifest = Get-Item -LiteralPath (Join-Path $Workspace $manifestName) -ErrorAction Stop
    if ($manifest.PSIsContainer -or $manifest.Length -gt 65536 -or ($manifest.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'invalid_manifest_path'
    }
    $result = Invoke-LmnProbe $true $Workspace $VmUuid $ComputerName $ProbeHashesJson @(
        $Probe, '--approved-vm-observation', $manifest.FullName, $ExpectedManifestSha256)
    Write-Output $result.Line
    $expectedExit = if ($ExpectedFinding -ceq 'VerifiedEvidence') { 0 } else { 1 }
    if ($result.Line -cne "OBSERVATION finding=$ExpectedFinding authority=false" -or $result.Code -ne $expectedExit) { throw 'unexpected_observation' }
    exit 0
}
catch { Write-LmnFailure $_.Exception.Message; exit 1 }
