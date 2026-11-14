"""Execution remains disabled until an independently verified isolation design exists."""

EXECUTION_DISABLED_MESSAGE = 'Code execution is unavailable until isolation is independently verified.'

# Names remain hard-blocked at the registry until an execution-owned staging
# and verification boundary exists. This is not a configurable capability.
ARTIFACT_WRITE_TOOLS = frozenset(('docx_create', 'xlsx_report', 'file_write'))


def require_protected_storage_writer() -> None:
    """No production broker currently supplies verified identity or writer ownership.

    This unconditional denial accepts no path, configuration flag, caller
    assertion or synthetic evidence. It is not an implementation of a broker.
    """
    raise PermissionError('Protected storage writer unavailable')


def code_execution_allowed() -> bool:
    """No configuration or caller input can enable this policy."""
    return False


def require_code_execution() -> None:
    if not code_execution_allowed():
        raise PermissionError(EXECUTION_DISABLED_MESSAGE)
