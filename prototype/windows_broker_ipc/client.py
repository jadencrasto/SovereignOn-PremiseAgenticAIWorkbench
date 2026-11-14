"""Fail-closed client facade; no transport negotiation or fallback."""
from .protocol import Command, ErrorCode, ProtocolError, parse_response
from .transport import UnavailableTransport


def request(command: Command) -> ErrorCode:
    if type(command) is not Command:
        raise ProtocolError(ErrorCode.INVALID_REQUEST)
    # The unavailable transport never contacts a process or accepts a peer.
    return parse_response(UnavailableTransport().request(b''))
