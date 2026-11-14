"""Inert until explicitly invoked in an independently isolated, approved VM.

Synthetic probe only; does not establish document authorization or model identity.
Use a fresh operator-created empty directory and separate write/verify processes.
"""
import argparse
import hashlib
import json
from pathlib import Path

DOC = 'synthetic_probe_v1'
TEXTS = ['synthetic alpha', 'synthetic beta']
IDS = [DOC + ':1:0', DOC + ':1:1']


def require_persistent(store, fallback_type):
    if store._client is None or isinstance(store._collection, fallback_type):
        raise RuntimeError('Persistent backend unavailable; fallback rejected')


def expected():
    return {key: (text, {'document_id': DOC, 'revision': 1, 'chunk_index': i,
                        'text_sha256': hashlib.sha256(text.encode()).hexdigest()})
            for i, (key, text) in enumerate(zip(IDS, TEXTS))}


def validate(result):
    ids, docs, metadata = result['ids'], result['documents'], result['metadatas']
    if len(ids) != len(set(ids)) or len(ids) != len(docs) or len(ids) != len(metadata):
        raise RuntimeError('Malformed manifest')
    actual = {key: (text, meta) for key, text, meta in zip(ids, docs, metadata)}
    if actual != expected():
        raise RuntimeError('Manifest mismatch')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['write', 'verify'])
    parser.add_argument('root', type=Path)
    args = parser.parse_args()
    root = args.root.resolve(strict=True)
    if not root.is_dir():
        raise RuntimeError('Root must be an existing isolated directory')
    marker = root / 'synthetic-probe-marker.txt'
    if args.mode == 'write':
        if any(root.iterdir()):
            raise RuntimeError('Write requires an empty directory')
        marker.write_text('synthetic-probe-v1', encoding='utf-8')
    elif not marker.is_file() or marker.read_text(encoding='utf-8') != 'synthetic-probe-v1':
        raise RuntimeError('Missing probe marker')

    # Import only after explicit invocation. Never start application lifespan or models.
    from backend.rag.store import VectorStore, _InMemoryFallbackCollection
    from backend.auth.request_context import effective_owner, effective_clearance
    from backend.security.provenance_foundation import QuarantinedSources
    store = VectorStore(root / 'chroma')
    require_persistent(store, _InMemoryFallbackCollection)
    registry = QuarantinedSources(root / 'synthetic-registry.sqlite')
    owner_token = effective_owner.set('offline-synthetic-owner')
    clearance_token = effective_clearance.set('viewer')
    try:
        if args.mode == 'write':
            pending = registry.reserve_document(b'synthetic pending')
            revoked = registry.reserve_document(b'synthetic revoked')
            if not registry.revoke(revoked):
                raise RuntimeError('Synthetic revocation failed')
            (root / 'synthetic-ids.json').write_text(json.dumps([pending, revoked]), encoding='utf-8')
        else:
            pending, revoked = json.loads((root / 'synthetic-ids.json').read_text(encoding='utf-8'))
            registry.mark_restart_unverified()
            if registry.read(pending)['state'] != 'pending' or registry.read(revoked)['state'] != 'revoked':
                raise RuntimeError('Unexpected lifecycle state')
            if registry.retrieval_eligible(pending) or registry.retrieval_eligible(revoked):
                raise RuntimeError('Unexpected retrieval eligibility')
    finally:
        effective_clearance.reset(clearance_token)
        effective_owner.reset(owner_token)
    if args.mode == 'write':
        rows = expected()
        store.add_chunks(IDS, [[1.0, 0.0], [0.0, 1.0]], TEXTS, [rows[k][1] for k in IDS])
    # Raw read is deliberately confined to this synthetic offline probe, not an API.
    result = store._collection.get(include=['documents', 'metadatas'])
    validate(result)
    print('PASS:', args.mode, 'synthetic manifest; backend', type(store._client).__name__)
    print('NOTE: close/reopen and OS isolation require independent operator evidence')


if __name__ == '__main__':
    main()
