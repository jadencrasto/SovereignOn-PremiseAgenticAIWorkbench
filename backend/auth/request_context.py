"""Server-derived clearance carried through asynchronous request execution."""
from contextvars import ContextVar

effective_clearance = ContextVar('effective_clearance', default=None)
effective_owner = ContextVar('effective_owner', default=None)
local_legacy_access = ContextVar('local_legacy_access', default=False)
request_tools_enabled = ContextVar('request_tools_enabled', default=None)
# Set by the owned local chat task around one permitted tool dispatch.
local_chat_tool_turn = ContextVar('local_chat_tool_turn', default=None)
# Bound only after server authentication; never populated from tool arguments.
dispatch_identity_resolver = ContextVar('dispatch_identity_resolver', default=None)
http_request_active = ContextVar('http_request_active', default=False)


def authenticated_dispatch_request():
    """Get the original authenticated Request for the current dispatch resolver."""
    resolver = dispatch_identity_resolver.get()
    reference = getattr(resolver, 'authenticated_request', None)
    request = reference() if callable(reference) else None
    binding = getattr(request.state, 'authenticated_session', None) if request is not None else None
    if isinstance(binding, tuple) and len(binding) == 2 and binding[0] is reference and binding[1] is resolver:
        return request
    return None


class ClearanceContextMiddleware:
    """Isolate caller context across HTTP requests and streaming responses."""
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        token = effective_clearance.set(None)
        owner_token = effective_owner.set(None)
        legacy_token = local_legacy_access.set(False)
        tools_token = request_tools_enabled.set(None)
        local_tool_token = local_chat_tool_turn.set(None)
        identity_token = dispatch_identity_resolver.set(None)
        http_token = http_request_active.set(scope['type'] == 'http')
        try:
            await self.app(scope, receive, send)
        finally:
            http_request_active.reset(http_token)
            dispatch_identity_resolver.reset(identity_token)
            local_chat_tool_turn.reset(local_tool_token)
            effective_clearance.reset(token)
            effective_owner.reset(owner_token)
            local_legacy_access.reset(legacy_token)
            request_tools_enabled.reset(tools_token)


def cap_clearance(requested: str) -> str:
    """Never raise a component's requested clearance above the caller's ceiling."""
    from backend.auth.models import resolve_user_clearance
    levels = {'viewer': 1, 'operator': 2, 'admin': 3}
    ceiling = effective_clearance.get()
    desired = resolve_user_clearance(requested)
    if ceiling is None:
        return desired
    return min((desired, resolve_user_clearance(ceiling)), key=lambda value: levels[value])
