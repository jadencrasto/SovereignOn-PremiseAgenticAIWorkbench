# Pure parser: no native calls, networking or filesystem access.
function ConvertFrom-SovereignWfpEvent([string] $EventXml) {
    if ([string]::IsNullOrEmpty($EventXml) -or $EventXml.Length -gt 65536 -or $EventXml -match '<!DOCTYPE|<!ENTITY') { throw 'invalid_event' }
    try { [xml]$document = $EventXml } catch { throw 'invalid_event' }
    $manager = [Xml.XmlNamespaceManager]::new($document.NameTable)
    $manager.AddNamespace('e', 'http://schemas.microsoft.com/win/2004/08/events/event')
    $eventId = $document.SelectSingleNode('/e:Event/e:System/e:EventID', $manager)
    $time = $document.SelectSingleNode('/e:Event/e:System/e:TimeCreated', $manager)
    if (-not $eventId -or $eventId.InnerText -cne '5157' -or -not $time) { throw 'invalid_event' }
    $stamp = [datetimeoffset]::MinValue
    if (-not [datetimeoffset]::TryParse($time.GetAttribute('SystemTime'), [ref]$stamp)) { throw 'invalid_event' }
    $data = @{}
    foreach ($entry in $document.SelectNodes('/e:Event/e:EventData/e:Data', $manager)) {
        $name = $entry.GetAttribute('Name')
        if (-not $name -or $data.ContainsKey($name)) { throw 'invalid_event' }
        $data[$name] = $entry.InnerText
    }
    [uint32]$pidValue = 0; [int]$protocol = 0; [int]$sourcePort = 0; [int]$destPort = 0
    if (-not [uint32]::TryParse($data.ProcessID, [ref]$pidValue) -or $pidValue -eq 0 -or
        [string]::IsNullOrWhiteSpace($data.Application) -or $data.Application.Length -gt 1024 -or
        -not [int]::TryParse($data.Protocol, [ref]$protocol) -or $protocol -lt 0 -or $protocol -gt 255 -or
        -not [int]::TryParse($data.SourcePort, [ref]$sourcePort) -or $sourcePort -lt 0 -or $sourcePort -gt 65535 -or
        -not [int]::TryParse($data.DestPort, [ref]$destPort) -or $destPort -lt 0 -or $destPort -gt 65535 -or
        $data.Direction -cnotin @('%%14592','%%14593')) { throw 'invalid_event' }
    $sourceAddress = $null; $destAddress = $null
    if (-not [Net.IPAddress]::TryParse($data.SourceAddress, [ref]$sourceAddress) -or
        -not [Net.IPAddress]::TryParse($data.DestAddress, [ref]$destAddress)) { throw 'invalid_event' }
    return [ordered]@{ event=5157; result='WFP_BLOCKED'; timestamp_utc=$stamp.UtcDateTime.ToString('o');
        direction=$(if ($data.Direction -ceq '%%14593') { 'outbound' } else { 'inbound' });
        process='REDACTED'; image='REDACTED'; source='REDACTED'; destination='REDACTED';
        protocol=$protocol; source_port=$sourcePort; destination_port=$destPort; attribution_present=$true }
}

function Restore-SovereignAuditState([string] $Initial, [bool] $Changed, [scriptblock] $Read, [scriptblock] $DisableFailure) {
    if ($Initial -cnotin @('No Auditing','Success','Failure','Success and Failure')) { throw 'audit_restore_failed' }
    if ($Changed) {
        $expected = if ($Initial -ceq 'Success') { 'Success and Failure' } elseif ($Initial -ceq 'No Auditing') { 'Failure' } else { throw 'audit_restore_failed' }
        if ((& $Read) -cne $expected) { throw 'audit_restore_conflict' }
        if (-not (& $DisableFailure)) { throw 'audit_restore_failed' }
    }
    if ((& $Read) -cne $Initial) { throw 'audit_restore_failed' }
}
