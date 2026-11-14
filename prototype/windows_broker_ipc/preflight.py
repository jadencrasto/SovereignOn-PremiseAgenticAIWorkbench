"""Read-only dependency diagnostics; never enables or initializes IPC."""
from dataclasses import dataclass
import importlib.metadata
import importlib.util
import sys

EXPECTED_VERSION = '312'
REQUIRED_MODULES = ('win32pipe', 'win32security', 'win32file', 'pywintypes')


@dataclass(frozen=True)
class PreflightResult:
    windows: bool
    binding_version: str | None
    missing_modules: tuple[str, ...]
    binding_matches_pin: bool
    transport_available: bool = False
    peer_authentication: str = 'UNSUPPORTED'
    server_authentication: str = 'UNSUPPORTED'
    exclusive_ownership: str = 'UNSUPPORTED'


def inspect_environment() -> PreflightResult:
    """Inspect metadata/module locations only, without importing native modules."""
    missing = []
    for name in REQUIRED_MODULES:
        try:
            if importlib.util.find_spec(name) is None:
                missing.append(name)
        except (ImportError, ValueError, ModuleNotFoundError):
            missing.append(name)
    try:
        version = importlib.metadata.version('pywin32')
    except importlib.metadata.PackageNotFoundError:
        version = None
    windows = sys.platform == 'win32'
    return PreflightResult(windows, version, tuple(missing),
                           windows and version == EXPECTED_VERSION and not missing)
