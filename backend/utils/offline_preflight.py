"""Read-only local prerequisites; this is not a startup or security attestation."""
from importlib import metadata
from pathlib import Path
import re
from urllib.parse import urlsplit


_REQUIRED = ('fastapi', 'pydantic-settings', 'httpx', 'python-multipart')
_MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$')


def inspect_prerequisites(cfg, *, version_lookup=metadata.version):
    """Inspect supplied settings only; do not create files, connect, or launch processes."""
    checks = []
    def add(name, state, detail):
        checks.append({'name': name, 'state': state, 'detail': detail})

    environment = str(getattr(cfg, 'app_env', '') or '').strip().lower()
    host = str(getattr(cfg, 'backend_host', '') or '').strip().lower()
    auth = getattr(cfg, 'auth_enabled', None)
    override = getattr(cfg, 'auth_dev_override', None)
    if not environment or not host or not isinstance(auth, bool) or not isinstance(override, bool):
        add('identity_configuration', 'fail', 'Required identity or network configuration is missing.')
    elif (environment in ('production', 'prod') and (not auth or override)) or (host not in ('localhost', '127.0.0.1', '::1') and (not auth or override)):
        add('identity_configuration', 'fail', 'Authentication settings are incompatible with the configured environment or binding.')
    else:
        add('identity_configuration', 'pass', 'Configuration is consistent; authorization and isolation are not verified.')
    port = getattr(cfg, 'backend_port', None)
    add('backend_port', 'pass' if isinstance(port, int) and not isinstance(port, bool) and 1 <= port <= 65535 else 'fail', 'Port range checked; listener ownership was not checked.')
    try:
        url = str(getattr(cfg, 'ollama_base_url', '') or '')
        parsed = urlsplit(url)
        valid = (parsed.scheme == 'http' and parsed.hostname in ('127.0.0.1', '::1', 'localhost')
                 and parsed.username is None and parsed.password is None and parsed.port != 0
                 and parsed.path in ('', '/') and not parsed.query and not parsed.fragment
                 and '\\' not in url and not any(ord(c) <= 32 or ord(c) == 127 for c in url))
    except ValueError:
        valid = False
    add('ollama_endpoint', 'pass' if valid else 'fail', 'Only a well-formed plain HTTP loopback endpoint is eligible for this offline preflight; provider availability is unknown.')
    model = getattr(cfg, 'ollama_default_model', None)
    add('model_configuration', 'pass' if isinstance(model, str) and _MODEL.fullmatch(model) else 'fail', 'Model identifier checked; installation and inference are unknown.')
    for name in _REQUIRED:
        try:
            version_lookup(name)
        except metadata.PackageNotFoundError:
            add('dependency:' + name, 'fail', 'Required distribution metadata was not found.')
        except Exception:
            add('dependency:' + name, 'unknown', 'Distribution metadata could not be inspected.')
        else:
            add('dependency:' + name, 'pass', 'Distribution metadata is present; import and integrity are unverified.')
    for name in ('upload_dir', 'log_dir', 'sandbox_dir', 'chroma_persist_dir', 'tasks_dir'):
        value = getattr(cfg, name, None)
        try:
            exists = bool(value) and Path(value).is_dir()
        except (OSError, ValueError, TypeError):
            exists = False
        add('storage:' + name, 'unknown' if exists else 'fail', 'Directory exists; writability, identity and protection are unverified.' if exists else 'Directory is missing or inaccessible; no directory was created.')
    add('security_prerequisites', 'unknown', 'Protected storage, provenance, egress isolation and process ownership are not attested by this check.')
    return {'ready': False, 'checks': checks}
