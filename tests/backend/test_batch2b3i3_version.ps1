$ErrorActionPreference = 'Stop'
$tokens=$null; $errors=$null
$path = Join-Path $PSScriptRoot 'run_batch2b3h8_dotnet_ipc.ps1'
$ast=[System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors)
if ($errors.Count) { throw 'runner_parse_failed' }
foreach ($name in @('Test-ResponseEnvelope','Format-ResponseDiagnostics')) {
    $fn=$ast.Find({ param($node) $node -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $node.Name -eq $name },$true)
    if ($null -eq $fn) { throw 'missing_function' }
    . ([scriptblock]::Create($fn.Extent.Text))
}
$valid='{"version":1,"ok":true,"development_only":true,"trusted_broker":false,"result":"pong","error":null}'
$cases=@(
    @{ json=$valid; expected=$true },
    @{ json=$valid.Replace('"version":1,',''); expected=$false },
    @{ json=$valid.Replace('"version":1','"version":"1"'); expected=$false },
    @{ json=$valid.Replace('"version":1','"version":2'); expected=$false },
    @{ json=$valid.Replace('"version":1','"version":null'); expected=$false },
    @{ json=$valid.Replace('"version":1','"version":1.0'); expected=$false },
    @{ json=$valid.Replace('"version":1','"version":true'); expected=$false },
    @{ json=$valid.Replace('"version":1','"version":1,"extra":0'); expected=$false }
)
foreach ($case in $cases) {
    $value=$case.json | ConvertFrom-Json -ErrorAction Stop
    if ((Test-ResponseEnvelope $value) -ne $case.expected) { throw 'envelope_case_failed' }
}
$validResponse=$valid | ConvertFrom-Json
if ((Format-ResponseDiagnostics $validResponse) -notmatch 'version=1 ') { throw 'version_display_failed' }
try { $null = '{' | ConvertFrom-Json -ErrorAction Stop; throw 'malformed_was_accepted' } catch { if ($_.Exception.Message -eq 'malformed_was_accepted') { throw } }
Write-Output 'PASS: 9 synthetic version/schema cases and version display.'
