"""Strict, dependency-free protocol parsing; no IPC or client authentication."""
from enum import Enum
import json

MAX_REQUEST_BYTES = 1024
MAX_RESPONSE_BYTES = 2048


class Command(str, Enum):
    STATUS = 'status'
    CAPABILITIES = 'capabilities'
    PING = 'ping'


class ErrorCode(str, Enum):
    INVALID_REQUEST = 'INVALID_REQUEST'
    REQUEST_TOO_LARGE = 'REQUEST_TOO_LARGE'
    UNSUPPORTED_COMMAND = 'UNSUPPORTED_COMMAND'
    TRANSPORT_UNAVAILABLE = 'TRANSPORT_UNAVAILABLE'


class ProtocolError(ValueError):
    def __init__(self, code: ErrorCode):
        self.code = code
        super().__init__(code.value)


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate key')
        result[key] = value
    return result


def parse_request(raw: bytes) -> Command:
    if type(raw) is not bytes:
        raise ProtocolError(ErrorCode.INVALID_REQUEST)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ProtocolError(ErrorCode.REQUEST_TOO_LARGE)
    try:
        obj = json.loads(raw.decode('utf-8'), object_pairs_hook=_unique_object,
                         parse_constant=lambda _: (_ for _ in ()).throw(ValueError('invalid number')))
    except (UnicodeError, ValueError, RecursionError):
        raise ProtocolError(ErrorCode.INVALID_REQUEST) from None
    if type(obj) is not dict or set(obj) != {'version', 'command'}:
        raise ProtocolError(ErrorCode.INVALID_REQUEST)
    if type(obj['version']) is not int or obj['version'] != 1 or type(obj['command']) is not str:
        raise ProtocolError(ErrorCode.INVALID_REQUEST)
    try:
        return Command(obj['command'])
    except ValueError:
        raise ProtocolError(ErrorCode.UNSUPPORTED_COMMAND) from None


def unavailable_response() -> bytes:
    """Only response until authenticated transport and exclusive ownership exist."""
    return b'{"version":1,"ok":false,"error":"TRANSPORT_UNAVAILABLE"}'


def parse_response(raw: bytes) -> ErrorCode:
    if type(raw) is not bytes or len(raw) > MAX_RESPONSE_BYTES:
        raise ProtocolError(ErrorCode.INVALID_REQUEST)
    if raw != unavailable_response():
        # No success responses accepted until an authenticated protocol exists.
        raise ProtocolError(ErrorCode.INVALID_REQUEST)
    return ErrorCode.TRANSPORT_UNAVAILABLE
