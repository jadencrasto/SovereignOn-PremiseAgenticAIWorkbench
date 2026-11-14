#requires -Version 7.0
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'
if (-not $IsWindows) { throw 'The recovery fixtures require Windows.' }
$binary = Join-Path $PSScriptRoot '../../prototype/windows_security_foundation/bin/Debug/net10.0/SecurityFoundation.exe'
if (-not (Test-Path -LiteralPath $binary -PathType Leaf)) { throw 'Build SecurityFoundation in Debug first.' }
& $binary recovery-selftest
exit $LASTEXITCODE
