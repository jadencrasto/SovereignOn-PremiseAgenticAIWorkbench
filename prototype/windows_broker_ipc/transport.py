"""Explicitly unsupported transport: never opens pipes, sockets, or files."""
from .protocol import unavailable_response


class UnavailableTransport:
    def request(self, request: bytes) -> bytes:
        # Do not inspect or transmit data to an unauthenticated peer.
        return unavailable_response()

    def serve(self) -> None:
        raise RuntimeError('Authenticated Windows IPC is unavailable')
