"""Fixed public messages for errors originating in internal services."""

PUBLIC_ERRORS = {
    'DOCUMENT_INVALID': 'Document could not be processed. Check its type and content.',
    'DOCUMENT_UNAVAILABLE': 'Document processing is temporarily unavailable.',
    'DOCUMENT_NOT_FOUND': 'Document not found.',
    'GRAPH_UNAVAILABLE': 'Knowledge graph service is temporarily unavailable.',
    'MODEL_PRELOAD_FAILED': 'Model preload is temporarily unavailable.',
    'VISION_UNAVAILABLE': 'Vision model error: image analysis is temporarily unavailable.',
    'INTERNAL_SERVER_ERROR': 'An internal server error occurred.',
    'OPERATION_UNAVAILABLE': 'Operation unavailable',
    'TASK_CANCEL_FAILED': 'Task cannot be cancelled in its current state.',
    'MODEL_SCAN_FAILED': 'Local model scan is temporarily unavailable.',
    'IMAGE_INVALID': 'Image validation failed. Check the image format and size.',
}


def public_error(code: str) -> str:
    """Return only server-authored strings, never exception-derived text."""
    return PUBLIC_ERRORS[code]
