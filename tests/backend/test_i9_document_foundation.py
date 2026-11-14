"""Non-authorizing synthetic evidence checks; no production index is opened."""
import copy
import hashlib
import json
import pytest
from backend.auth.request_context import effective_owner, effective_clearance
from backend.security.provenance_foundation import QuarantinedSources, document_content_allowed
from backend.security.index_reconciliation import compare_snapshot
from backend.rag.grounding import select_evidence, build_context, validate_answer


def test_quarantined_identity_ownership_reconciliation_and_revocation(tmp_path):
    registry = QuarantinedSources(tmp_path / 'sources.db')
    owner = effective_owner.set('synthetic-a'); clearance = effective_clearance.set('operator')
    try:
        source = b'Synthetic source'; sid = registry.reserve_document(source)
        record = registry.read(sid)
        assert record['owner_id'] == 'synthetic-a' and record['source_revision'] == 1
        assert record['classification'] == 'operator' and record['state'] == 'pending'
        other = effective_owner.set('synthetic-b')
        try:
            assert registry.read(sid) is None and not registry.revoke(sid)
        finally:
            effective_owner.reset(other)
        chunk_id = sid + '_chunk_0'; text = 'Synthetic passage'
        manifest = [{'chunk_id': chunk_id, 'chunk_index': 0, 'sha256': hashlib.sha256(text.encode()).hexdigest()}]
        backend = {'backend':'chromadb', 'directory':'synthetic', 'collection':'workbench_documents'}
        config = {'backend_identity':backend, 'embedding_model':'fake', 'dimensions':2,
                  'source_revision':1, 'model_digest':None}
        registry.record_manifest(sid, manifest_json=json.dumps(manifest), index_model='fake', index_configuration_json=json.dumps(config))
        registry.mark_restart_unverified(); record = registry.read(sid)
        identity = {'backend_identity':backend, 'backend_status':'verified_persistent',
                    'embedding_model':'fake', 'dimensions':2, 'model_digest':None}
        chunks = [{'id':chunk_id, 'text':text, 'embedding':[0.5, 0.6],
                   'metadata':{'document_id':sid, 'source_revision':1, 'chunk_index':0, 'index_model':'fake', 'clearance':'operator'}}]
        assert compare_snapshot(record, source, chunks, identity) == 'match_unverified'
        assert compare_snapshot(record, b'replaced', chunks, identity) == 'mismatch'
        assert compare_snapshot(record, source, [], identity) == 'mismatch'
        for value in (float('nan'), float('inf'), True, '1'):
            bad = copy.deepcopy(chunks); bad[0]['embedding'][0] = value
            assert compare_snapshot(record, source, bad, identity) == 'mismatch'
        bad = copy.deepcopy(chunks); bad[0]['metadata']['source_revision'] = True
        assert compare_snapshot(record, source, bad, identity) == 'mismatch'
        bad_record = copy.deepcopy(record); bad_manifest = copy.deepcopy(manifest)
        bad_manifest[0]['chunk_id'] = 'other_chunk_0'
        bad_record['manifest_json'] = json.dumps(bad_manifest)
        assert compare_snapshot(bad_record, source, chunks, identity) == 'invalid'
        assert registry.revoke(sid)
        assert compare_snapshot(registry.read(sid), source, chunks, identity) == 'ineligible'
        assert registry.retrieval_eligible(sid) is False and document_content_allowed(sid, 1) is False
    finally:
        effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_citation_scope_requires_an_explicit_boolean_decision():
    row = {'document_id':'doc_a', 'chunk_id':'doc_a_chunk_0', 'filename':'a.txt',
           'text':'Synthetic passage', 'chunk_index':0, 'source_revision':1,
           'distance':0.1, 'index_version':'synthetic', 'embedding_model':'fake'}
    opts = dict(index_version='synthetic', embedding_model='fake', top_k=1)
    for unknown in (None, 'allowed', 1, {'allowed': True}):
        with pytest.raises(PermissionError):
            select_evidence([row], allowed=lambda *_: unknown, **opts)
    assert select_evidence([row], allowed=lambda *_: False, **opts) == ()
    evidence = select_evidence([row], allowed=lambda *_: True, **opts)
    _, refs = build_context(evidence, allowed=lambda *_: True)
    label = next(iter(refs))
    answer = validate_answer('Synthetic [' + label + ']', refs, allowed=lambda *_: True)
    assert answer.citations[0][1].chunk_id == 'doc_a_chunk_0'
    assert answer.dependencies == (('doc_a', 1),)
    with pytest.raises(PermissionError):
        validate_answer('Synthetic [' + label + ']', refs, allowed=lambda *_: {'allowed': True})


def test_blocked_document_listing_reports_unavailable_without_reading_store():
    from unittest.mock import MagicMock
    from fastapi import HTTPException
    from backend.api.documents import list_documents
    from test_a21_conversation_offline import immediate
    service = MagicMock()
    with pytest.raises(HTTPException) as error:
        immediate(list_documents(doc_service=service, current_user=MagicMock()))
    assert error.value.status_code == 503
    assert error.value.headers['X-Error-Code'] == 'DOCUMENT_UNAVAILABLE'
    service.list_documents.assert_not_called()
