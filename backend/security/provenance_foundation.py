"""Non-authorizing provenance primitives for future, reviewed integration.

No source registered here is trusted for retrieval or disclosure. Existing
document, conversation, task, and artifact policies are not changed by this
module. The caller must not use these rows as proof of content ownership.
"""
from datetime import datetime, timezone
import hashlib
from pathlib import Path
import sqlite3
import uuid
import json
import re
from contextlib import closing

from backend.agent.sqlite_transaction import immediate_transaction
from backend.auth.request_context import effective_clearance, effective_owner


def _validate_manifest(source_id, manifest_json, index_model, configuration_json):
    """Validate diagnostic metadata; successful validation never grants access."""
    if (not isinstance(source_id, str) or not re.fullmatch(r'doc_v1_[0-9a-f]{32}', source_id)
            or not isinstance(index_model, str) or not index_model.strip() or len(index_model) > 256
            or not isinstance(manifest_json, str) or len(manifest_json) > 4 * 1024 * 1024
            or not isinstance(configuration_json, str) or len(configuration_json) > 65536):
        raise ValueError('Invalid source manifest')
    try:
        def unique_object(pairs):
            result = {}
            for key, value in pairs:
                if key in result:
                    raise ValueError('Duplicate field')
                result[key] = value
            return result
        def invalid_constant(_value):
            raise ValueError('Invalid numeric constant')
        manifest = json.loads(manifest_json, object_pairs_hook=unique_object, parse_constant=invalid_constant)
        config = json.loads(configuration_json, object_pairs_hook=unique_object, parse_constant=invalid_constant)
        if (not isinstance(manifest, list) or not 1 <= len(manifest) <= 100000
                or not isinstance(config, dict) or config.get('embedding_model') != index_model
                or type(config.get('source_revision')) is not int or config['source_revision'] != 1
                or type(config.get('dimensions')) is not int or not 1 <= config['dimensions'] <= 65536):
            raise ValueError('Invalid source manifest')
        for idx, entry in enumerate(manifest):
            if (not isinstance(entry, dict) or type(entry.get('chunk_index')) is not int
                    or entry['chunk_index'] != idx or entry.get('chunk_id') != f'{source_id}_chunk_{idx}'
                    or not isinstance(entry.get('sha256'), str)
                    or not re.fullmatch(r'[0-9a-f]{64}', entry['sha256'])):
                raise ValueError('Invalid source manifest')
    except (TypeError, ValueError, RecursionError) as exc:
        raise ValueError('Invalid source manifest') from exc


def _manifest_binding(source_id, owner, clearance, revision, content_digest, storage_ref,
                      manifest_json, index_model, configuration_json):
    """Detect accidental metadata changes, not malicious rewriting or rollback."""
    fields = [source_id, owner, clearance, revision, content_digest, storage_ref,
              manifest_json, index_model, configuration_json]
    return hashlib.sha256(json.dumps(fields, ensure_ascii=True, separators=(',', ':')).encode('utf-8')).hexdigest()


class SecurityPolicy:
    """Deny-by-default placeholder until freshness and provenance are verified."""

    def may_disclose(self, operation, resource):
        # Dictionaries, IDs, legacy generation fields, and assertions by a
        # caller are not evidence of current authorization.
        return False


def document_content_allowed(source_id=None, source_revision=None) -> bool:
    """Fail closed until authoritative source and current-context checks exist.

    Neither caller-supplied identifiers nor vector metadata grant access. There
    is currently no ACTIVE state or safe positive decision. Do not replace this
    with a metadata-only check.
    """
    return False


class QuarantinedSources:
    """Record pending references without promoting or authorizing content.

    Startup initializes this registry; the ingestion path remains gated.
    It stores no file content or credentials. `source_id` is server generated;
    `document_reference` and stored metadata are not authorization evidence.
    """

    def __init__(self, path: Path):
        self.path = Path(path)
        with immediate_transaction(self.path) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS quarantined_source_registry (
                source_id TEXT PRIMARY KEY,
                document_reference TEXT NOT NULL,
                owner_id TEXT NOT NULL,
                state TEXT NOT NULL CHECK (state IN ('pending','revoked')),
                schema_version INTEGER NOT NULL CHECK (schema_version = 1),
                created_at TEXT NOT NULL,
                revoked_at TEXT
            )''')
            # Additive migration: old records retain NULL and remain untrusted.
            columns = {row[1] for row in conn.execute('PRAGMA table_info(quarantined_source_registry)')}
            for name, sql_type in [('content_sha256', 'TEXT'), ('source_revision', 'INTEGER'),
                                   ('classification', 'TEXT'), ('storage_ref', 'TEXT'),
                                   ('index_model', 'TEXT'), ('manifest_json', 'TEXT'),
                                   ('verification_state', 'TEXT'), ('index_configuration_json', 'TEXT'),
                                   ('reconciliation_status', 'TEXT'), ('verification_timestamp', 'TEXT'),
                                   ('manifest_binding_sha256', 'TEXT'), ('replacement_operation_id', 'TEXT'),
                                   ('replacement_expected_revision', 'INTEGER'), ('replacement_proposed_revision', 'INTEGER'),
                                   ('replacement_expected_generation', 'TEXT'), ('replacement_proposed_generation', 'TEXT'),
                                   ('replacement_content_sha256', 'TEXT'), ('replacement_stage', 'TEXT'),
                                   ('replacement_decision', 'TEXT')]:
                if name not in columns:
                    conn.execute(f'ALTER TABLE quarantined_source_registry ADD COLUMN {name} {sql_type}')
            conn.execute('''CREATE UNIQUE INDEX IF NOT EXISTS quarantined_replacement_operation_unique
                ON quarantined_source_registry(replacement_operation_id) WHERE replacement_operation_id IS NOT NULL''')

    def reserve(self, document_reference: str) -> str:
        owner = effective_owner.get()
        if not isinstance(owner, str) or not owner:
            raise PermissionError('Identity unavailable')
        if not isinstance(document_reference, str) or not 0 < len(document_reference) <= 128:
            raise ValueError('Invalid document reference')
        source_id = uuid.uuid4().hex
        with immediate_transaction(self.path, require_existing=True) as conn:
            conn.execute('INSERT INTO quarantined_source_registry (source_id, document_reference, owner_id, state, schema_version, created_at, revoked_at) VALUES (?,?,?,?,?,?,NULL)',
                         (source_id, document_reference, owner, 'pending', 1,
                          datetime.now(timezone.utc).isoformat()))
        return source_id

    def reserve_document(self, content: bytes) -> str:
        """Reserve a non-authorizing record for supplied bytes, never activate it.

        The digest is only an assertion about the supplied bytes. It is not
        evidence that a file or index exists or belongs to this owner.
        """
        owner, clearance = effective_owner.get(), effective_clearance.get()
        if not isinstance(owner, str) or not owner or clearance not in ('viewer', 'operator', 'admin'):
            raise PermissionError('Identity or clearance unavailable')
        if not isinstance(content, bytes) or not content or len(content) > 50 * 1024 * 1024:
            raise ValueError('Invalid document content')
        source_id = 'doc_v1_' + uuid.uuid4().hex
        with immediate_transaction(self.path, require_existing=True) as conn:
            conn.execute('''INSERT INTO quarantined_source_registry
                (source_id, document_reference, owner_id, state, schema_version, created_at,
                 content_sha256, source_revision, classification, storage_ref)
                VALUES (?,?,?,'pending',1,?,?,1,?,?)''',
                (source_id, source_id, owner, datetime.now(timezone.utc).isoformat(),
                 hashlib.sha256(content).hexdigest(), clearance, source_id + '.bin'))
        return source_id

    def record_readback(self, source_id: str, *, manifest_json: str, index_model: str, nondurable: bool) -> None:
        """Record observed readback only while the same pending owner remains bound."""
        owner, clearance = effective_owner.get(), effective_clearance.get()
        if not isinstance(owner, str) or not owner or clearance not in ('viewer', 'operator', 'admin'):
            raise PermissionError('Identity or clearance unavailable')
        if type(nondurable) is not bool:
            raise ValueError('Invalid readback durability')
        state = 'readback_verified_nondurable' if nondurable else 'readback_verified_durability_unproven'
        with immediate_transaction(self.path, require_existing=True) as conn:
            row = conn.execute('''SELECT index_configuration_json, content_sha256, manifest_binding_sha256 FROM quarantined_source_registry
                WHERE source_id=? AND owner_id=? AND classification=? AND state='pending'
                  AND source_revision=1 AND manifest_json=? AND index_model=? AND verification_state='manifest_pending'
                  AND reconciliation_status IS NULL AND storage_ref=? AND document_reference=source_id
                  AND schema_version=1 AND revoked_at IS NULL AND verification_timestamp IS NULL
                  AND replacement_operation_id IS NULL''',
                (source_id, owner, clearance, manifest_json, index_model, source_id + '.bin')).fetchone()
            if row is None:
                raise PermissionError('Pending source unavailable')
            if not isinstance(row[1], str) or not re.fullmatch(r'[0-9a-f]{64}', row[1]):
                raise ValueError('Invalid source metadata')
            _validate_manifest(source_id, manifest_json, index_model, row[0])
            expected = _manifest_binding(source_id, owner, clearance, 1, row[1], source_id + '.bin',
                                         manifest_json, index_model, row[0])
            if not isinstance(row[2], str) or row[2] != expected:
                raise ValueError('Source metadata changed')
            changed = conn.execute('''UPDATE quarantined_source_registry
                SET manifest_json=?, index_model=?, storage_ref=?, verification_state=?
                WHERE source_id=? AND owner_id=? AND classification=? AND source_revision=1 AND state='pending'
                  AND manifest_json=? AND index_model=? AND verification_state='manifest_pending'
                  AND reconciliation_status IS NULL AND storage_ref=?
                  AND length(content_sha256)=64 AND index_configuration_json IS NOT NULL
                  AND manifest_binding_sha256=? AND document_reference=source_id
                  AND schema_version=1 AND revoked_at IS NULL AND verification_timestamp IS NULL
                  AND replacement_operation_id IS NULL ''',
                (manifest_json, index_model, source_id + '.bin', state, source_id, owner, clearance,
                 manifest_json, index_model, source_id + '.bin', expected)).rowcount
            if changed != 1:
                raise PermissionError('Pending source unavailable')

    def record_manifest(self, source_id: str, *, manifest_json: str, index_model: str, index_configuration_json: str = None) -> None:
        owner, clearance = effective_owner.get(), effective_clearance.get()
        if not isinstance(owner, str) or not owner or clearance not in ('viewer', 'operator', 'admin'):
            raise PermissionError('Identity or clearance unavailable')
        _validate_manifest(source_id, manifest_json, index_model, index_configuration_json)
        with immediate_transaction(self.path, require_existing=True) as conn:
            row = conn.execute('''SELECT content_sha256 FROM quarantined_source_registry
                WHERE source_id=? AND owner_id=? AND classification=? AND source_revision=1 AND state='pending'
                  AND manifest_json IS NULL AND reconciliation_status IS NULL AND storage_ref=?
                  AND manifest_binding_sha256 IS NULL AND verification_state IS NULL AND index_model IS NULL
                  AND index_configuration_json IS NULL AND verification_timestamp IS NULL AND revoked_at IS NULL
                  AND document_reference=source_id AND schema_version=1 AND replacement_operation_id IS NULL''', (source_id, owner, clearance, source_id + '.bin')).fetchone()
            if row is None or not isinstance(row[0], str) or not re.fullmatch(r'[0-9a-f]{64}', row[0]):
                raise PermissionError('Pending source unavailable')
            binding = _manifest_binding(source_id, owner, clearance, 1, row[0], source_id + '.bin',
                                        manifest_json, index_model, index_configuration_json)
            changed = conn.execute('''UPDATE quarantined_source_registry
                SET manifest_json=?, index_model=?, storage_ref=?, index_configuration_json=?, verification_state='manifest_pending', manifest_binding_sha256=?
                WHERE source_id=? AND owner_id=? AND classification=? AND source_revision=1 AND state='pending'
                  AND manifest_json IS NULL AND reconciliation_status IS NULL AND storage_ref=?
                  AND content_sha256=? AND manifest_binding_sha256 IS NULL AND verification_state IS NULL
                  AND index_model IS NULL AND index_configuration_json IS NULL AND verification_timestamp IS NULL
                  AND revoked_at IS NULL AND document_reference=source_id AND schema_version=1
                  AND replacement_operation_id IS NULL ''',
                (manifest_json, index_model, source_id + '.bin', index_configuration_json, binding,
                 source_id, owner, clearance, source_id + '.bin', row[0])).rowcount
            if changed != 1:
                raise PermissionError('Pending source unavailable')

    def begin_replacement(self, source_id: str, *, expected_revision: int, content: bytes) -> str:
        """Persist a non-authorizing replacement intent; never stage or publish content.

        Existing readback is an observation, not a committed source. The intent
        cannot advance until a protected storage boundary is implemented.
        """
        owner, clearance = effective_owner.get(), effective_clearance.get()
        if not isinstance(owner, str) or not owner or clearance not in ('viewer', 'operator', 'admin'):
            raise PermissionError('Identity or clearance unavailable')
        if (not isinstance(source_id, str) or not re.fullmatch(r'doc_v1_[0-9a-f]{32}', source_id)
                or type(expected_revision) is not int or expected_revision != 1
                or not isinstance(content, bytes) or not 0 < len(content) <= 50 * 1024 * 1024):
            raise ValueError('Invalid replacement request')
        operation_id, generation = uuid.uuid4().hex, uuid.uuid4().hex
        digest = hashlib.sha256(content).hexdigest()
        with immediate_transaction(self.path, require_existing=True) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('''SELECT * FROM quarantined_source_registry
                WHERE source_id=? AND owner_id=? AND classification=? AND state='pending'
                  AND source_revision=? AND schema_version=1 AND document_reference=source_id
                  AND revoked_at IS NULL AND reconciliation_status IS NULL AND verification_timestamp IS NULL
                  AND verification_state IN ('readback_verified_nondurable','readback_verified_durability_unproven')
                  AND storage_ref=? AND replacement_operation_id IS NULL''',
                (source_id, owner, clearance, expected_revision, source_id + '.bin')).fetchone()
            if row is None:
                raise PermissionError('Source replacement unavailable')
            for key in ('replacement_expected_revision', 'replacement_proposed_revision',
                        'replacement_expected_generation', 'replacement_proposed_generation',
                        'replacement_content_sha256', 'replacement_stage', 'replacement_decision'):
                if row[key] is not None:
                    raise PermissionError('Source replacement unavailable')
            _validate_manifest(source_id, row['manifest_json'], row['index_model'], row['index_configuration_json'])
            if not isinstance(row['content_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['content_sha256']):
                raise ValueError('Invalid source metadata')
            binding = _manifest_binding(source_id, owner, clearance, expected_revision, row['content_sha256'],
                row['storage_ref'], row['manifest_json'], row['index_model'], row['index_configuration_json'])
            if row['manifest_binding_sha256'] != binding:
                raise ValueError('Source metadata changed')
            config = json.loads(row['index_configuration_json'])
            old_generation = config.get('index_generation')
            if not isinstance(old_generation, str) or not old_generation.strip() or len(old_generation) > 256:
                raise PermissionError('Source generation unavailable')
            if generation == old_generation:
                raise RuntimeError('Unable to allocate a distinct generation')
            changed = conn.execute('''UPDATE quarantined_source_registry SET replacement_operation_id=?,
                replacement_expected_revision=?, replacement_proposed_revision=?, replacement_expected_generation=?,
                replacement_proposed_generation=?, replacement_content_sha256=?, replacement_stage='storage_boundary_required',
                replacement_decision='undecided' WHERE source_id=? AND owner_id=? AND classification=?
                AND source_revision=? AND state='pending' AND revoked_at IS NULL AND reconciliation_status IS NULL
                AND manifest_binding_sha256=? AND replacement_operation_id IS NULL''',
                (operation_id, expected_revision, expected_revision + 1, old_generation, generation, digest,
                 source_id, owner, clearance, expected_revision, binding)).rowcount
            if changed != 1:
                raise PermissionError('Source replacement unavailable')
        return operation_id

    def mark_restart_unverified(self) -> int:
        """Record conservative restart status; do not read data or grant access."""
        with immediate_transaction(self.path, require_existing=True) as conn:
            changed = conn.execute('''UPDATE quarantined_source_registry
                SET reconciliation_status=CASE state WHEN 'pending' THEN 'restart_unverified'
                    ELSE 'revoked_cleanup_review' END
                WHERE (state='pending' AND (reconciliation_status IS NULL OR reconciliation_status='restart_unverified'))
                   OR (state='revoked' AND (reconciliation_status IS NULL OR reconciliation_status='revoked_cleanup_review'))''').rowcount
            conn.execute('''UPDATE quarantined_source_registry SET replacement_stage=CASE state
                WHEN 'pending' THEN 'recovery_required' ELSE 'revoked_recovery_required' END
                WHERE state IN ('pending','revoked') AND replacement_operation_id IS NOT NULL
                  AND replacement_stage='storage_boundary_required' AND replacement_decision='undecided' ''')
            return changed

    def replacement_status(self, source_id: str, operation_id: str) -> str:
        """Classify an intent for its current owner; never interpret it as a grant."""
        owner, clearance = effective_owner.get(), effective_clearance.get()
        if (not isinstance(owner, str) or not owner or clearance not in ('viewer', 'operator', 'admin')
                or not isinstance(source_id, str) or not re.fullmatch(r'doc_v1_[0-9a-f]{32}', source_id)
                or not isinstance(operation_id, str) or not re.fullmatch(r'[0-9a-f]{32}', operation_id)):
            raise PermissionError('Replacement unavailable')
        with closing(sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('''SELECT * FROM quarantined_source_registry
                WHERE source_id=? AND owner_id=? AND classification=? AND replacement_operation_id=?''',
                (source_id, owner, clearance, operation_id)).fetchone()
        if row is None:
            raise PermissionError('Replacement unavailable')
        if (type(row['replacement_expected_revision']) is not int or row['replacement_expected_revision'] != 1
                or type(row['replacement_proposed_revision']) is not int or row['replacement_proposed_revision'] != 2
                or not isinstance(row['replacement_expected_generation'], str)
                or not row['replacement_expected_generation'].strip() or len(row['replacement_expected_generation']) > 256
                or not isinstance(row['replacement_proposed_generation'], str)
                or not re.fullmatch(r'[0-9a-f]{32}', row['replacement_proposed_generation'])
                or row['replacement_proposed_generation'] == row['replacement_expected_generation']
                or not isinstance(row['replacement_content_sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', row['replacement_content_sha256'])
                or row['replacement_decision'] != 'undecided'
                or row['replacement_stage'] not in ('storage_boundary_required', 'recovery_required', 'revoked_recovery_required')):
            return 'corrupt_operation'
        if row['state'] == 'revoked':
            return 'revoked_recovery_required'
        if row['state'] != 'pending':
            return 'corrupt_operation'
        if row['replacement_stage'] == 'revoked_recovery_required':
            return 'corrupt_operation'
        if row['source_revision'] != row['replacement_expected_revision']:
            return 'revision_conflict'
        try:
            if (row['schema_version'] != 1 or row['document_reference'] != source_id or row['revoked_at'] is not None
                    or row['verification_timestamp'] is not None
                    or row['verification_state'] not in ('readback_verified_nondurable','readback_verified_durability_unproven')
                    or not isinstance(row['content_sha256'], str) or not re.fullmatch(r'[0-9a-f]{64}', row['content_sha256'])
                    or row['reconciliation_status'] not in (None, 'restart_unverified')):
                return 'corrupt_operation'
            _validate_manifest(source_id, row['manifest_json'], row['index_model'], row['index_configuration_json'])
            binding = _manifest_binding(source_id, owner, clearance, row['source_revision'], row['content_sha256'],
                row['storage_ref'], row['manifest_json'], row['index_model'], row['index_configuration_json'])
            if (row['manifest_binding_sha256'] != binding or row['storage_ref'] != source_id + '.bin'
                    or json.loads(row['index_configuration_json']).get('index_generation') != row['replacement_expected_generation']):
                return 'corrupt_operation'
        except (ValueError, TypeError, KeyError, RecursionError):
            return 'corrupt_operation'
        if row['reconciliation_status'] is not None:
            return 'recovery_required'
        return row['replacement_stage']

    def retrieval_eligible(self, source_id: str) -> bool:
        """No registry record establishes retrieval authorization yet."""
        return False

    def read(self, source_id: str):
        owner = effective_owner.get()
        if not isinstance(owner, str) or not owner:
            return None
        with closing(sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro', uri=True)) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute('SELECT * FROM quarantined_source_registry WHERE source_id=? AND owner_id=?',
                               (source_id, owner)).fetchone()
        return dict(row) if row else None

    def revoke(self, source_id: str) -> bool:
        owner = effective_owner.get()
        if not isinstance(owner, str) or not owner:
            return False
        with immediate_transaction(self.path, require_existing=True) as conn:
            changed = conn.execute("UPDATE quarantined_source_registry SET state='revoked', revoked_at=?, reconciliation_status='revoked_cleanup_review', verification_timestamp=NULL, "
                                   "replacement_stage=CASE WHEN replacement_operation_id IS NOT NULL AND replacement_decision='undecided' AND replacement_stage IN ('storage_boundary_required','recovery_required') THEN 'revoked_recovery_required' ELSE replacement_stage END "
                                   "WHERE source_id=? AND owner_id=? AND state='pending' AND revoked_at IS NULL AND schema_version=1",
                                   (datetime.now(timezone.utc).isoformat(), source_id, owner)).rowcount
        return changed == 1
