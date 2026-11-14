"""Offline-only lifecycle and grounding checks; none grants production access."""
import copy
import hashlib
import json
import sqlite3
from unittest.mock import AsyncMock

import pytest
from backend.auth.request_context import effective_owner, effective_clearance
from backend.rag.ingest import DocumentParser, TextChunker
from backend.rag.grounding import answer_with_evidence
from backend.security.index_reconciliation import compare_snapshot
from backend.security.provenance_foundation import QuarantinedSources, document_content_allowed
from test_a21_conversation_offline import immediate


def setup_source(tmp_path):
    registry = QuarantinedSources(tmp_path/'source.db')
    content = b'The synthetic pump moves clean water.'
    sid = registry.reserve_document(content)
    document = DocumentParser.parse('synthetic.txt', content)
    document.document_id = sid
    chunks = TextChunker(chunk_size=100, chunk_overlap=0).chunk(document)
    manifest = [{'chunk_id':c.chunk_id, 'chunk_index':c.chunk_index,
                 'sha256':hashlib.sha256(c.text.encode()).hexdigest()} for c in chunks]
    backend = {'backend':'chromadb', 'directory':'synthetic', 'collection':'workbench_documents'}
    cfg = {'embedding_model':'fake', 'dimensions':2, 'source_revision':1,
           'model_digest':None, 'backend_identity':backend, 'index_generation':'g1'}
    encoded = json.dumps(manifest)
    registry.record_manifest(sid, manifest_json=encoded, index_model='fake', index_configuration_json=json.dumps(cfg))
    registry.record_readback(sid, manifest_json=encoded, index_model='fake', nondurable=True)
    registry.mark_restart_unverified()
    identity = {'backend_identity':backend, 'backend_status':'verified_persistent', 'embedding_model':'fake',
                'dimensions':2, 'model_digest':None, 'index_generation':'g1'}
    index = [{'id':c.chunk_id, 'text':c.text, 'embedding':[0.1,0.2], 'metadata':{
        'document_id':sid, 'source_revision':1, 'chunk_index':c.chunk_index, 'index_model':'fake',
        'clearance':'operator', 'index_generation':'g1', 'filename':'synthetic.txt'}} for c in chunks]
    return registry, content, sid, chunks, cfg, identity, index, encoded


def test_internal_pipeline_uses_pending_registry_and_synthetic_scope(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        registry, content, sid, chunks, cfg, identity, index, encoded = setup_source(tmp_path)
        rows = [dict(document_id=sid, filename='synthetic.txt', chunk_id=c.chunk_id, chunk_index=c.chunk_index,
            text=c.text, source_revision=1, distance=.1, index_version='g1', embedding_model='fake') for c in chunks]
        # Synthetic test authority: a diagnostic match is deliberately insufficient in production.
        grant = {sid}
        def allowed(source, revision):
            record = registry.read(source)
            return bool(source in grant and record and revision == 1 and
                        compare_snapshot(record, content, index, identity) == 'match_unverified')
        def verify_evidence(item):
            matches = [chunk for chunk in index if chunk['id'] == item.chunk_id]
            return (len(matches) == 1 and matches[0]['text'] == item.text
                    and matches[0]['metadata']['filename'] == item.filename
                    and allowed(item.document_id, item.source_revision))
        async def generate(question, context): return 'Water moves ' + context.split(']')[0] + ']'
        args = dict(allowed=allowed, index_version='g1', embedding_model='fake', top_k=1, generate=generate,
                    verify_evidence=verify_evidence)
        answer = immediate(answer_with_evidence('What moves?', rows, **args))
        assert answer.citations[0][1].chunk_id == chunks[0].chunk_id
        assert answer.dependencies == ((sid,1),) and not document_content_allowed(sid, 1)
        forged = copy.deepcopy(rows); forged[0]['text'] = 'Forged source content'
        with pytest.raises(PermissionError): immediate(answer_with_evidence('What?', forged, **args))
        forged = copy.deepcopy(rows); forged[0]['filename'] = 'wrong.txt'
        with pytest.raises(PermissionError): immediate(answer_with_evidence('What?', forged, **args))
        other = effective_owner.set('other')
        try:
            with pytest.raises(PermissionError): immediate(answer_with_evidence('What?', rows, **args))
        finally: effective_owner.reset(other)
        async def revoke_during_generation(question, context):
            assert registry.revoke(sid)
            return 'Answer ' + context.split(']')[0] + ']'
        args['generate'] = revoke_during_generation
        with pytest.raises(PermissionError): immediate(answer_with_evidence('What?', rows, **args))
        assert registry.read(sid)['reconciliation_status'] == 'revoked_cleanup_review'
        assert not registry.retrieval_eligible(sid)
    finally:
        effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_manifest_validation_and_corrupt_readback_fail_closed(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'source.db'); sid = registry.reserve_document(b'hello')
        assert registry.reserve_document(b'hello') != sid
        cfg = {'embedding_model':'fake','dimensions':2,'source_revision':1}
        manifest = [{'chunk_id':sid+'_chunk_0','chunk_index':0,'sha256':hashlib.sha256(b'x').hexdigest()}]
        good = json.dumps(manifest)
        for broken in ['[]', '[{}]', good.replace(sid, 'doc_v1_'+'a'*32), good.replace('"chunk_index": 0', '"chunk_index": true')]:
            with pytest.raises(ValueError): registry.record_manifest(sid, manifest_json=broken, index_model='fake', index_configuration_json=json.dumps(cfg))
        for bad_config in ({**cfg,'source_revision':True}, {**cfg,'dimensions':True}, {**cfg,'embedding_model':'other'}):
            with pytest.raises(ValueError): registry.record_manifest(sid, manifest_json=good, index_model='fake', index_configuration_json=json.dumps(bad_config))
        assert registry.read(sid)['manifest_json'] is None
        registry.record_manifest(sid, manifest_json=good, index_model='fake', index_configuration_json=json.dumps(cfg))
        with pytest.raises(PermissionError):
            registry.record_manifest(sid, manifest_json=good, index_model='fake', index_configuration_json=json.dumps(cfg))
        with pytest.raises(ValueError): registry.record_readback(sid, manifest_json=good, index_model='fake', nondurable='false')
        with sqlite3.connect(str(registry.path)) as conn:
            conn.execute("UPDATE quarantined_source_registry SET index_configuration_json='{}' WHERE source_id=?", (sid,))
        with pytest.raises(ValueError): registry.record_readback(sid, manifest_json=good, index_model='fake', nondurable=False)
        assert registry.read(sid)['verification_state'] == 'manifest_pending'
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_snapshot_rejects_corrupt_generation_scope_and_vectors(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        registry, content, sid, chunks, cfg, identity, index, encoded = setup_source(tmp_path)
        record = registry.read(sid)
        assert compare_snapshot(record, content, index, identity) == 'match_unverified'
        assert compare_snapshot(record, b'replaced', index, identity) == 'mismatch'
        assert compare_snapshot(record, content, [], identity) == 'mismatch'
        for field, value in [('index_generation','g2'), ('clearance','admin'), ('document_id','other')]:
            bad = copy.deepcopy(index); bad[0]['metadata'][field] = value
            assert compare_snapshot(record, content, bad, identity) == 'mismatch'
        for value in (True, float('nan'), float('inf'), '0'):
            bad = copy.deepcopy(index); bad[0]['embedding'][0] = value
            assert compare_snapshot(record, content, bad, identity) == 'mismatch'
        bad = copy.deepcopy(identity); bad['index_generation'] = 'g2'
        assert compare_snapshot(record, content, index, bad) == 'mismatch'
        for field, value in [('owner_id', ''), ('classification', None), ('storage_ref', 'other.bin'), ('source_revision', 2)]:
            bad = copy.deepcopy(record); bad[field] = value
            assert compare_snapshot(bad, content, index, identity) == 'invalid'
        bad = copy.deepcopy(record); corrupt = dict(cfg, source_revision=True); bad['index_configuration_json'] = json.dumps(corrupt)
        assert compare_snapshot(bad, content, index, identity) == 'mismatch'
        bad['index_configuration_json'] = '{"source_revision":1,"source_revision":2}'
        assert compare_snapshot(bad, content, index, identity) == 'invalid'
        assert registry.revoke(sid)
        assert compare_snapshot(registry.read(sid), content, index, identity) == 'ineligible'
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_integrity_and_authorization_are_rechecked_after_generation():
    row = dict(document_id='doc_a', filename='a.txt', chunk_id='doc_a_chunk_0', chunk_index=0,
               text='Actual text', source_revision=1, distance=.1, index_version='g1', embedding_model='fake')
    current = {'text':'Actual text'}
    def verify(item): return current['text'] == item.text
    async def mutate(question, context):
        current['text'] = 'Replaced text'
        return 'Answer ' + context.split(']')[0] + ']'
    with pytest.raises(PermissionError):
        immediate(answer_with_evidence('What?', [row], allowed=lambda *_: True, index_version='g1',
            embedding_model='fake', top_k=1, generate=mutate, verify_evidence=verify))
    generator = AsyncMock()
    with pytest.raises(PermissionError):
        immediate(answer_with_evidence('What?', [row], allowed=lambda *_: False, index_version='g1',
            embedding_model='fake', top_k=1, generate=generator, verify_evidence=lambda _: True))
    generator.assert_not_awaited()
    with pytest.raises(ValueError, match='verifier'):
        immediate(answer_with_evidence('What?', [row], allowed=lambda *_: True, index_version='g1',
            embedding_model='fake', top_k=1, generate=generator))


def test_registry_storage_failure_cannot_grant_scope(tmp_path):
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        registry = QuarantinedSources(tmp_path/'source.db'); sid = registry.reserve_document(b'hello')
        registry.path = tmp_path/'missing-directory'/'source.db'
        with pytest.raises(sqlite3.OperationalError): registry.read(sid)
        assert registry.retrieval_eligible(sid) is False
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)


def test_actual_ingestion_readback_validation_rejects_corrupt_metadata(tmp_path):
    from backend.rag.service import _validate_index_readback
    owner = effective_owner.set('owner'); clearance = effective_clearance.set('operator')
    try:
        registry, content, sid, chunks, cfg, identity, index, encoded = setup_source(tmp_path)
        manifest = json.loads(encoded)
        readback = {'ids':[item['id'] for item in index], 'documents':[item['text'] for item in index],
                    'metadatas':[item['metadata'] for item in index]}
        _validate_index_readback(readback, manifest, sid, 'fake', 'operator')
        for field, value in [('source_revision',True), ('chunk_index',False), ('clearance','viewer'), ('document_id','other')]:
            bad = copy.deepcopy(readback); bad['metadatas'][0][field] = value
            with pytest.raises(RuntimeError): _validate_index_readback(bad, manifest, sid, 'fake', 'operator')
        bad = copy.deepcopy(readback); bad['documents'][0] = 'forged'
        with pytest.raises(RuntimeError): _validate_index_readback(bad, manifest, sid, 'fake', 'operator')
        with pytest.raises(RuntimeError): _validate_index_readback({'ids':[], 'documents':[], 'metadatas':[]}, [], sid, 'fake', 'operator')
    finally: effective_clearance.reset(clearance); effective_owner.reset(owner)
