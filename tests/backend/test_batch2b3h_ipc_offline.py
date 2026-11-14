"""Protocol checks only; no IPC, process launch, native API or network."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from prototype.windows_broker_ipc.client import request
from prototype.windows_broker_ipc.protocol import Command, ErrorCode, MAX_REQUEST_BYTES, ProtocolError, parse_request, parse_response, unavailable_response
from prototype.windows_broker_ipc.transport import UnavailableTransport


class IpcFoundationTests(unittest.TestCase):
    def test_only_three_well_formed_commands_parse(self):
        for command in Command:
            wire = ('{"version":1,"command":"' + command.value + '"}').encode()
            self.assertEqual(parse_request(wire), command)

    def test_unknown_and_dangerous_commands_are_denied(self):
        for name in ('begin', 'finish', 'interrupt', 'ingest', 'retrieve', 'write', 'authorize', 'execute_code', 'task_resume', 'artifact_read'):
            with self.subTest(name=name), self.assertRaises(ProtocolError) as error:
                parse_request(('{"version":1,"command":"' + name + '"}').encode())
            self.assertEqual(error.exception.code, ErrorCode.UNSUPPORTED_COMMAND)

    def test_malformed_duplicate_extra_and_type_rejected(self):
        for wire in (b'', b'{}', b'[]', b'{"version":true,"command":"ping"}',
                     b'{"version":1,"command":"ping","command":"status"}',
                     b'{"version":1,"command":"ping","role":"admin"}',
                     b'{"version":NaN,"command":"ping"}', b'\xff'):
            with self.subTest(wire=wire), self.assertRaises(ProtocolError) as error:
                parse_request(wire)
            self.assertEqual(error.exception.code, ErrorCode.INVALID_REQUEST)

    def test_size_limit(self):
        with self.assertRaises(ProtocolError) as error:
            parse_request(b'x' * (MAX_REQUEST_BYTES + 1))
        self.assertEqual(error.exception.code, ErrorCode.REQUEST_TOO_LARGE)

    def test_unavailable_client_and_server(self):
        for cmd in Command:
            self.assertEqual(request(cmd), ErrorCode.TRANSPORT_UNAVAILABLE)
        with self.assertRaises(RuntimeError):
            UnavailableTransport().serve()
        with self.assertRaises(ProtocolError):
            request('ping')

    def test_no_forged_success_response(self):
        self.assertEqual(parse_response(unavailable_response()), ErrorCode.TRANSPORT_UNAVAILABLE)
        with self.assertRaises(ProtocolError):
            parse_response(b'{"version":1,"ok":true}')


if __name__ == '__main__':
    unittest.main()
