#requires -Version 7.0
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'wfp_event_parser.ps1')
$passed = 0; $failed = 0
function Test-Case($name, $action) { try { & $action; $script:passed++; Write-Host "PASS $name" } catch { $script:failed++; Write-Host "FAIL $name" } }
function Denied($action) { $denied = $false; try { & $action | Out-Null } catch { $denied = $true }; if (-not $denied) { throw 'expected_denial' } }
$xml = @'
<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><EventID>5157</EventID><TimeCreated SystemTime="2026-11-13T00:00:00Z"/></System><EventData><Data Name="ProcessID">123</Data><Data Name="Application">C:\Users\private\python.exe</Data><Data Name="SourceAddress">127.0.0.1</Data><Data Name="SourcePort">1000</Data><Data Name="DestAddress">127.0.0.1</Data><Data Name="DestPort">1001</Data><Data Name="Protocol">6</Data><Data Name="Direction">%%14593</Data></EventData></Event>
'@
Test-Case 'integrated parser sanitizes 5157' { $v=ConvertFrom-SovereignWfpEvent $xml; if ($v.result -cne 'WFP_BLOCKED' -or $v.destination -cne 'REDACTED' -or $v.direction -cne 'outbound') { throw 'invalid' } }
Test-Case 'reject malformed event and missing attribution' { Denied { ConvertFrom-SovereignWfpEvent '<broken>' }; Denied { ConvertFrom-SovereignWfpEvent ($xml.Replace('123','0')) }; Denied { ConvertFrom-SovereignWfpEvent ($xml.Replace('5157','5156')) } }
Test-Case 'restore original audit configuration' { $s=@{value='Failure'; calls=0}; Restore-SovereignAuditState 'No Auditing' $true { $s.value } { $s.calls++; $s.value='No Auditing'; $true }; if ($s.calls -ne 1) { throw 'missing_restore' } }
Test-Case 'restore conflict and failure are denied' { Denied { Restore-SovereignAuditState 'No Auditing' $true { 'Success' } { $true } }; Denied { Restore-SovereignAuditState 'No Auditing' $true { 'Failure' } { $false } }; Denied { Restore-SovereignAuditState 'Failure' $false { 'Success' } { $true } } }
Write-Host "WFP parser/restoration: $passed passed, $failed failed"
if ($failed) { exit 1 }
