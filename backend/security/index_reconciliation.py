"""Non-authorizing comparison of synthetic or independently acquired snapshots.

This module never opens a backend or source and never writes verification state.
An observation is not a stable snapshot in the presence of independent writers.
"""
import hashlib
import json
import math
import re


BACKEND_STATES = frozenset({'verified_persistent', 'persistent_durability_unverified',
                            'in_memory_fallback', 'initialization_failure'})


def _strict_json(value):
    def unique(pairs):
        result = {}
        for key, item in pairs:
            if key in result:
                raise ValueError('Duplicate field')
            result[key] = item
        return result
    def invalid_constant(_value):
        raise ValueError('Invalid numeric constant')
    return json.loads(value, object_pairs_hook=unique, parse_constant=invalid_constant)


def compare_snapshot(record, source_bytes, chunks, identity):
    """Return a diagnostic, never authorization or a durable verification claim.

    The caller must supply one complete, stable collection snapshot; the current
    application has no supported way to guarantee this for independent writers.
    A match therefore remains unverified and must never be persisted as verified.
    """
    try:
        if record['state'] != 'pending' or record['reconciliation_status'] != 'restart_unverified':
            return 'ineligible'
        source_id = record['source_id']
        if not re.fullmatch(r'doc_v1_[0-9a-f]{32}', source_id):
            return 'invalid'
        revision = record['source_revision']
        owner = record.get('owner_id')
        if (type(revision) is not int or revision != 1 or record['storage_ref'] != source_id + '.bin'
                or not isinstance(owner, str) or not owner.strip() or len(owner) > 256):
            return 'invalid'
        if not isinstance(source_bytes, bytes) or hashlib.sha256(source_bytes).hexdigest() != record['content_sha256']:
            return 'mismatch'
        config = _strict_json(record['index_configuration_json'])
        manifest = _strict_json(record['manifest_json'])
        if (not isinstance(config, dict) or not isinstance(manifest, list) or not manifest
                or not isinstance(identity, dict) or not isinstance(chunks, list)):
            return 'invalid'
        backend = config['backend_identity']
        if (not isinstance(backend, dict) or backend != identity.get('backend_identity')
                or backend.get('backend') != 'chromadb'
                or not isinstance(backend.get('directory'), str) or not backend['directory']
                or not isinstance(backend.get('collection'), str) or not backend['collection']
                or identity.get('backend_status') != 'verified_persistent'):
            return 'mismatch'
        model, dimension = config['embedding_model'], config['dimensions']
        if (not isinstance(model, str) or not model or record['index_model'] != model
                or type(dimension) is not int or dimension <= 0
                or type(config.get('source_revision')) is not int or config['source_revision'] != revision
                or identity.get('embedding_model') != model or type(identity.get('dimensions')) is not int
                or identity.get('dimensions') != dimension):
            return 'mismatch'
        generation = config.get('index_generation')
        if generation is not None and (not isinstance(generation, str) or not generation or len(generation) > 256):
            return 'invalid'
        if identity.get('index_generation') != generation:
            return 'mismatch'
        classification = record.get('classification')
        if classification not in ('viewer', 'operator', 'admin'):
            return 'invalid'
        # Names are mutable; neither side can assert a digest without an
        # independently validated source, which this comparison does not have.
        if config.get('model_digest') is not None or identity.get('model_digest') is not None:
            return 'invalid'
        expected = {}
        indices = set()
        for item in manifest:
            cid, idx, digest = item['chunk_id'], item['chunk_index'], item['sha256']
            if (not isinstance(cid, str) or cid != f'{source_id}_chunk_{idx}' or cid in expected or type(idx) is not int
                    or idx < 0 or idx in indices or not isinstance(digest, str)
                    or not re.fullmatch(r'[0-9a-f]{64}', digest)):
                return 'invalid'
            expected[cid] = (idx, digest)
            indices.add(idx)
        if indices != set(range(len(expected))):
            return 'invalid'
        seen = set()
        for chunk in chunks:
            cid, text, meta, embedding = chunk['id'], chunk['text'], chunk['metadata'], chunk['embedding']
            if (cid not in expected or cid in seen or not isinstance(text, str)
                    or not isinstance(meta, dict) or not isinstance(embedding, (list, tuple))
                    or len(embedding) != dimension
                    or any(type(value) not in (int, float) or not math.isfinite(value) for value in embedding)):
                return 'mismatch'
            idx, digest = expected[cid]
            if (meta.get('document_id') != source_id or type(meta.get('source_revision')) is not int
                    or meta.get('source_revision') != revision or type(meta.get('chunk_index')) is not int
                    or meta.get('chunk_index') != idx or meta.get('index_model') != model
                    or meta.get('index_generation') != generation
                    or meta.get('clearance') != classification
                    or hashlib.sha256(text.encode('utf-8')).hexdigest() != digest):
                return 'mismatch'
            seen.add(cid)
        return 'match_unverified' if seen == set(expected) else 'mismatch'
    except (KeyError, TypeError, ValueError, UnicodeError, OverflowError, RecursionError):
        return 'invalid'
