"""Offline-only 5157 interpretation. Not an OS network boundary."""
import ipaddress
import hashlib
import json
import re
import xml.etree.ElementTree as ET
from datetime import datetime

NS = {'e': 'http://schemas.microsoft.com/win/2004/08/events/event'}


def parse_5157(xml: str) -> dict:
    if not isinstance(xml, str) or len(xml) > 65536:
        raise ValueError('malformed_event')
    try:
        root = ET.fromstring(xml)
        system = root.find('e:System', NS)
        if system is None or system.findtext('e:EventID', namespaces=NS) != '5157':
            raise ValueError('wrong_event')
        timestamp = system.find('e:TimeCreated', NS).attrib['SystemTime']
        datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        data = {}
        for element in root.findall('e:EventData/e:Data', NS):
            name = element.attrib.get('Name')
            if not name or name in data:
                raise ValueError('malformed_event')
            data[name] = element.text or ''
        pid = int(data['ProcessID'], 0)
        if pid <= 0 or not data['Application'] or len(data['Application']) > 1024:
            raise ValueError('attribution_missing')
        source = str(ipaddress.ip_address(data['SourceAddress']))
        destination = str(ipaddress.ip_address(data['DestAddress']))
        src_port, dst_port, protocol = (int(data[k]) for k in ('SourcePort', 'DestPort', 'Protocol'))
        if not (0 <= src_port <= 65535 and 0 <= dst_port <= 65535 and 0 <= protocol <= 255):
            raise ValueError('malformed_event')
        direction = data['Direction']
        if direction not in ('%%14592', '%%14593'):
            raise ValueError('malformed_event')
        # Path and PID are intentionally not exported. Addresses remain operational
        # evidence and must be reviewed/redacted before sharing.
        executable = re.split(r'[\\/]', data['Application'])[-1]
        if not re.fullmatch(r'[A-Za-z0-9_.-]{1,80}', executable):
            raise ValueError('attribution_missing')
        return {'timestamp_utc': timestamp, 'process': 'REDACTED', 'image': 'REDACTED',
                'process_attributed': True, 'source': 'REDACTED', 'source_port': src_port,
                'destination': 'REDACTED', 'destination_port': dst_port, 'protocol': protocol,
                'direction': 'outbound' if direction == '%%14593' else 'inbound', 'event': 5157,
                'result': 'WFP_BLOCKED'}
    except (ET.ParseError, KeyError, TypeError, AttributeError, ValueError) as exc:
        raise ValueError('invalid_or_unattributed_event') from exc


def execution_gate(*, vm_verified=False, isolation_verified=False, approval=False,
                   snapshot_verified=False, audit_enabled=False, calibration=False,
                   rollback_ready=False, monitor_healthy=False) -> str:
    # Deliberately no production or application-test execution capability.
    evidence = (vm_verified, isolation_verified, approval, snapshot_verified,
                audit_enabled, calibration, rollback_ready, monitor_healthy)
    return 'BLOCKED' if not all(x is True for x in evidence) else 'READY_FOR_REVIEW_ONLY'


def calibration_decision(event, expected_pid, expected_destination, expected_port):
    """Caller must supply independently acquired raw event fields, never self-attest."""
    if not isinstance(event, dict):
        return 'UNKNOWN'
    return 'OBSERVED_BLOCK_ONLY' if (event.get('event') == 5157 and
        type(expected_pid) is int and expected_pid > 0 and event.get('pid') == expected_pid and
        event.get('destination') == expected_destination and event.get('port') == expected_port and
        event.get('direction') == 'outbound') else 'UNKNOWN'


def collect_synthetic(records, *, collector_ok=True):
    """Exercise the same parser with bounded, explicitly synthetic inputs."""
    if collector_ok is not True or not isinstance(records, list) or len(records) > 500:
        raise ValueError('collection_failed')
    parsed = [parse_5157(record) for record in records]
    report = {'schema': 1, 'mode': 'SIMULATED', 'coverage': 'UNKNOWN',
              'rollback_required': True, 'events': parsed}
    encoded = json.dumps(report, sort_keys=True, separators=(',', ':')).encode('utf-8')
    if len(encoded) > 262144:
        raise ValueError('evidence_too_large')
    return encoded, hashlib.sha256(encoded).hexdigest()


def write_durable_evidence(*_args, **_kwargs):
    """Disabled until handle-based secure output creation and ACLs are verified."""
    raise OSError('durable_export_unsupported')
