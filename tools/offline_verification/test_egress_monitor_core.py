import unittest
from pathlib import Path
import importlib.util

spec = importlib.util.spec_from_file_location('egress_monitor_core', Path(__file__).with_name('egress_monitor_core.py'))
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

def fixture():
    fields = {'ProcessID':'123', 'Application':r'C:\Users\private\python.exe', 'SourceAddress':'127.0.0.1',
              'SourcePort':'1000', 'DestAddress':'127.0.0.1', 'DestPort':'1001', 'Protocol':'6', 'Direction':'%%14593'}
    data = ''.join(f'<Data Name="{k}">{v}</Data>' for k,v in fields.items())
    return '<Event xmlns="http://schemas.microsoft.com/win/2004/08/events/event"><System><EventID>5157</EventID><TimeCreated SystemTime="2026-11-13T00:00:00Z"/></System><EventData>' + data + '</EventData></Event>'

class MonitorTests(unittest.TestCase):
    def test_valid_event_and_path_pid_redacted(self):
        result = core.parse_5157(fixture())
        self.assertEqual(result['result'], 'WFP_BLOCKED')
        self.assertEqual(result['direction'], 'outbound')
        self.assertEqual(result['destination_port'], 1001)
        self.assertNotIn('private', str(result))
        self.assertNotIn('123', str(result))

    def test_missing_and_malformed_events(self):
        for xml in ('', '<Event>', fixture().replace('5157', '5156'), fixture().replace('127.0.0.1', 'not-ip')):
            with self.subTest(xml=xml), self.assertRaises(ValueError): core.parse_5157(xml)

    def test_missing_attribution_and_duplicates(self):
        for xml in (fixture().replace('123', '0'), fixture().replace('Name="Application"', 'Name="Other"'),
                    fixture().replace('</EventData>', '<Data Name="ProcessID">5</Data></EventData>')):
            with self.subTest(xml=xml), self.assertRaises(ValueError): core.parse_5157(xml)

    def test_unauthorized_and_monitor_failure_block(self):
        keys = dict(vm_verified=True, isolation_verified=True, approval=True, snapshot_verified=True,
                    audit_enabled=True, calibration=True, rollback_ready=True, monitor_healthy=True)
        self.assertEqual(core.execution_gate(**keys), 'READY_FOR_REVIEW_ONLY')
        for key in keys:
            values = dict(keys); values[key] = False
            self.assertEqual(core.execution_gate(**values), 'BLOCKED')
        self.assertEqual(core.execution_gate(), 'BLOCKED')

    def test_calibration_failure_unknown(self):
        event = {'event':5157,'pid':9,'destination':'127.0.0.1','port':10,'direction':'outbound'}
        self.assertEqual(core.calibration_decision(event,9,'127.0.0.1',10), 'OBSERVED_BLOCK_ONLY')
        self.assertEqual(core.calibration_decision(event,8,'127.0.0.1',10), 'UNKNOWN')
        self.assertEqual(core.calibration_decision(None,9,'127.0.0.1',10), 'UNKNOWN')

    def test_collector_parser_and_synthetic_evidence(self):
        payload, digest = core.collect_synthetic([fixture()])
        self.assertIn(b'"coverage":"UNKNOWN"', payload)
        self.assertIn(b'"WFP_BLOCKED"', payload)
        self.assertEqual(len(digest), 64)
        self.assertNotIn(b'private', payload)
        with self.assertRaises(ValueError): core.collect_synthetic([fixture()], collector_ok=False)
        with self.assertRaises(ValueError): core.collect_synthetic([fixture()] * 501)
        with self.assertRaises(ValueError): core.collect_synthetic(['bad'])

    def test_durable_export_refuses_arbitrary_paths(self):
        for path in ('../escape', r'C:\Windows\file', 'synthetic'):
            with self.assertRaisesRegex(OSError, 'durable_export_unsupported'): core.write_durable_evidence(path, b'x')

if __name__ == '__main__': unittest.main(verbosity=2)
