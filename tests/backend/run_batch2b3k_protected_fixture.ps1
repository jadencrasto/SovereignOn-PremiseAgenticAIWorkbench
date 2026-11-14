[CmdletBinding()]
param(
    [switch] $ApproveDisposableVm,
    [string] $Workspace,
    [string] $VmUuid,
    [string] $ComputerName,
    [string] $ProbeHashesJson,
    [string] $ExpectedManifestSha256,
    [string] $ExpectedDeniedCallerSid,
    [ValidateSet('VerifiedEvidence','MissingEvidence','MismatchedIdentity','InvalidConfiguration','UntrustedInstallation','UnexpectedProcess','InspectionFailure','NativeUnavailable','EndpointUnavailable','UnexpectedEndpoint','UnsafeEndpoint','ChangedBinding','Cancelled')]
    [string] $ExpectedFinding = 'VerifiedEvidence'
)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'batch2b3lmn_vm_probe.ps1')
try {
    if (-not $ApproveDisposableVm.IsPresent) { throw 'approval_required' }
    if (-not $IsWindows -or $PSVersionTable.PSVersion.Major -lt 7) { throw 'unsupported_platform' }
    if ($ExpectedManifestSha256 -notmatch '^[a-f0-9A-F]{64}$') { throw 'invalid_expected_digest' }
    if (-not (Test-LmnLocalDirectory $Workspace)) { throw 'invalid_vm_prerequisites' }
    $manifest = Get-Item -LiteralPath (Join-Path $Workspace 'expected-protected-service.json') -ErrorAction Stop
    if ($manifest.PSIsContainer -or $manifest.Length -gt 65536 -or ($manifest.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
        throw 'invalid_manifest_path'
    }
    # Separate from J.4. The observer verifies an independently provisioned, already
    # running fixture and performs no SCM lifecycle or machine configuration changes.
    if ($ExpectedDeniedCallerSid) {
        if ($ExpectedDeniedCallerSid -cnotmatch '^S-1-5-21-(\d+-){3}\d+$' -or $ExpectedFinding -cne 'VerifiedEvidence') {
            throw 'invalid_vm_prerequisites'
        }
        $result = Invoke-LmnProbe $true $Workspace $VmUuid $ComputerName $ProbeHashesJson @(
            'probe-protected-pipe-denial', '--approved-vm-observation', $manifest.FullName, $ExpectedManifestSha256, $ExpectedDeniedCallerSid)
        Write-Output $result.Line
        if ($result.Line -cne 'PIPE_ACCESS observation=Denied authority=false' -or $result.Code -ne 0) { throw 'unexpected_observation' }
        exit 0
    }
    $result = Invoke-LmnProbe $true $Workspace $VmUuid $ComputerName $ProbeHashesJson @(
        'observe-protected-endpoint', '--approved-vm-observation', $manifest.FullName, $ExpectedManifestSha256)
    Write-Output $result.Line
    $expectedExit = if ($ExpectedFinding -ceq 'VerifiedEvidence') { 0 } else { 1 }
    if ($result.Line -cne "OBSERVATION finding=$ExpectedFinding authority=false" -or $result.Code -ne $expectedExit) { throw 'unexpected_observation' }
    exit 0
}
catch { Write-LmnFailure $_.Exception.Message; exit 1 }
