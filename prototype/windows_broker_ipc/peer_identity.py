"""Unwired named-pipe peer token inspection for a future Windows broker.

This helper does not authenticate the broker to its client, create a pipe, or
authorize any command. It is not a production boundary and is never called by
the disabled transport. The allowed SID must come from trusted configuration.
"""
from dataclasses import dataclass
import importlib


@dataclass(frozen=True)
class PeerCheck:
    accepted: bool
    code: str


def _windows_api():
    return (importlib.import_module('win32security'), importlib.import_module('win32api'),
            importlib.import_module('win32con'))


def inspect_pipe_client(pipe_handle, trusted_allowed_sid: str) -> PeerCheck:
    """Compare the pipe client's Windows token SID after successful impersonation.

    Called only after a bounded message has been read from a connected server
    pipe handle. A failure in API access, token level or cleanup is a denial.
    A same-user process is indistinguishable by SID; no broader identity or
    client authenticity is established. On failed revert the calling thread is
    unsafe to reuse and its hosting broker must stop; no listener uses this yet.
    """
    if not isinstance(trusted_allowed_sid, str) or not trusted_allowed_sid:
        return PeerCheck(False, 'INVALID_POLICY')
    token = None
    impersonated = False
    result = PeerCheck(False, 'IDENTITY_UNAVAILABLE')
    cleanup_failed = False
    try:
        security, api, con = _windows_api()
        allowed = security.ConvertStringSidToSid(trusted_allowed_sid)
        security.ImpersonateNamedPipeClient(pipe_handle)
        impersonated = True
        token = security.OpenThreadToken(api.GetCurrentThread(), con.TOKEN_QUERY, True)
        level = security.GetTokenInformation(token, security.TokenImpersonationLevel)
        if level < security.SecurityIdentification:
            result = PeerCheck(False, 'INSUFFICIENT_TOKEN_LEVEL')
        else:
            sid, _attributes = security.GetTokenInformation(token, security.TokenUser)
            matches = security.ConvertSidToStringSid(sid) == security.ConvertSidToStringSid(allowed)
            result = PeerCheck(matches, 'SID_MATCH' if matches else 'SID_DENIED')
    except Exception:
        result = PeerCheck(False, 'IDENTITY_UNAVAILABLE')
    finally:
        if token is not None:
            try:
                token.Close()
            except Exception:
                cleanup_failed = True
        if impersonated:
            try:
                security.RevertToSelf()
            except Exception:
                cleanup_failed = True
    return PeerCheck(False, 'CLEANUP_FAILED') if cleanup_failed else result
