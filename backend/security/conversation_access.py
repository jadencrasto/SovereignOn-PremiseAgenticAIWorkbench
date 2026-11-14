"""Conservative server-side access checks for stored assistant responses."""


def source_provenance(sources):
    """Only used by plain chat, where prior assistant messages are excluded."""
    # An empty retrieval result also represents skipped, failed or timed-out
    # retrieval; it is not evidence of source independence.
    if not isinstance(sources, list) or not sources or len(sources) > 64:
        return None
    refs = []
    for source in sources:
        document_id = getattr(source, 'document_id', None)
        chunk_id = getattr(source, 'chunk_id', None)
        if not all(isinstance(v, str) and 0 < len(v) <= 256 for v in (document_id, chunk_id)):
            return None
        refs.append({'document_id': document_id, 'chunk_id': chunk_id})
    return {'version': 1, 'kind': 'plain_chat', 'sources': refs}


def may_read_assistant(message, doc_service, clearance):
    provenance = message.extra_fields.get('access_provenance_v1')
    if not isinstance(provenance, dict) or provenance.get('version') != 1:
        return False
    if provenance.get('kind') not in ('plain_chat', 'deterministic_calculation'):
        return False
    refs = provenance.get('sources')
    if not isinstance(refs, list) or len(refs) > 64:
        return False
    if provenance['kind'] == 'plain_chat' and not refs:
        return False
    if provenance['kind'] == 'deterministic_calculation' and refs:
        return False
    for ref in refs:
        from backend.security.provenance_foundation import document_content_allowed
        if not document_content_allowed():
            return False
        if not isinstance(ref, dict):
            return False
        document_id, chunk_id = ref.get('document_id'), ref.get('chunk_id')
        if not all(isinstance(v, str) and 0 < len(v) <= 256 for v in (document_id, chunk_id)) or doc_service is None:
            return False
        try:
            details = doc_service.get_document_details(document_id, user_clearance=clearance)
        except Exception:
            return False
        if not isinstance(details, dict) or details.get('document_id') != document_id:
            return False
        if not any(chunk.get('chunk_id') == chunk_id for chunk in details.get('chunks', []) if isinstance(chunk, dict)):
            return False
    return True
