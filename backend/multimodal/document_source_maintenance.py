"""Local operator backup and read-only restore preflight for source authority."""

from __future__ import annotations

import argparse
from contextlib import ExitStack, closing, contextmanager
from dataclasses import asdict, dataclass
import hmac
import json
import os
from pathlib import Path
import re
import sqlite3
import stat
import sys
from time import monotonic

from backend.multimodal import document_source_recovery as recovery
from backend.multimodal.document_reasoning import ReasoningError
from backend.multimodal.document_source_approvals import _CREATE
from backend.multimodal.document_source_provider import _CREATE_CATALOG
from backend.security.provenance_keystore import KeyStoreError, valid_service_sid
from backend.security.provenance_windows import _owned, _require_private_acl
from backend.security.provenance_windows_native import UNSAFE_ATTRIBUTES, TokenFacts, WindowsKeyStoreNative


_BACKUP = 'authorization.sqlite3'
_CHECKPOINT = 'checkpoint.sha256'
_MANIFEST = '''CREATE TABLE source_backup_manifest (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    version INTEGER NOT NULL,
    generation TEXT NOT NULL,
    checkpoint TEXT NOT NULL
)'''
_MAX_BYTES = 64 * 1024 * 1024
_MAINTENANCE_SECONDS = 15
_DDL = (_CREATE, _CREATE_CATALOG)
_SHA256 = re.compile('[0-9a-f]{64}')
_GENERATION = re.compile('[0-9a-f]{32}')


class MaintenanceError(Exception):
    """A diagnostic code safe to print without paths or database contents."""


@dataclass(frozen=True)
class Preflight:
    status: str
    checks: tuple[str, ...]
    blockers: tuple[str, ...]


@dataclass(frozen=True)
class _OutputSecurity:
    api: WindowsKeyStoreNative
    parent: int
    owner: str


def _safe_path(path: Path) -> None:
    if not path.is_absolute() or '..' in path.parts:
        raise MaintenanceError('UNSAFE_PATH')
    if os.name == 'nt' and (re.fullmatch('[A-Za-z]:', path.drive) is None
                            or any(os.path.isreserved(part) for part in path.parts[1:])):
        raise MaintenanceError('UNSAFE_PATH')
    for item in (*reversed(path.parents), path):
        try:
            info = item.lstat()
        except FileNotFoundError:
            if item == path:
                break
            raise MaintenanceError('UNSAFE_PATH') from None
        if (stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & UNSAFE_ATTRIBUTES
                or (item != path and not stat.S_ISDIR(info.st_mode))
                or (stat.S_ISREG(info.st_mode) and info.st_nlink != 1)):
            raise MaintenanceError('UNSAFE_PATH')


def _sidecars(path: Path) -> tuple[Path, ...]:
    return tuple(path.with_name(path.name + suffix) for suffix in ('-wal', '-shm', '-journal'))


def _standalone_database(path: Path, *, maximum: int = _MAX_BYTES) -> None:
    _safe_path(path)
    if not path.is_file() or not 100 <= path.stat().st_size <= maximum:
        raise MaintenanceError('DATABASE_UNREADABLE')
    for sidecar in _sidecars(path):
        _safe_path(sidecar)
        if sidecar.exists():
            raise MaintenanceError('SQLITE_SIDECAR_PRESENT')
    with path.open('rb') as stream:
        header = stream.read(20)
    if not header.startswith(b'SQLite format 3\x00') or header[18:20] != b'\x01\x01':
        raise MaintenanceError('INVALID_STANDALONE_DATABASE')


def _open_read(path: Path, deadline: float, *, standalone=False) -> sqlite3.Connection:
    suffix = '&immutable=1' if standalone else ''
    connection = sqlite3.connect(f'{path.as_uri()}?mode=ro{suffix}', timeout=1, uri=True)
    try:
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 128 * 1024)
        connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
        connection.execute('PRAGMA trusted_schema=OFF')
        connection.execute('BEGIN')
        return connection
    except BaseException:
        connection.close()
        raise


def _check_integrity(
    connection: sqlite3.Connection, deadline: float, *, failure_code: str = 'DATABASE_INTEGRITY_FAILED',
) -> None:
    if connection.execute('PRAGMA integrity_check(1)').fetchall() != [('ok',)]:
        raise MaintenanceError(failure_code)
    if monotonic() >= deadline:
        raise MaintenanceError('MAINTENANCE_TIMEOUT')


def _catalog_schema(connection: sqlite3.Connection) -> list[tuple]:
    return connection.execute(
        '''SELECT type, name, tbl_name, sql FROM sqlite_schema
           WHERE tbl_name IN (?, ?) ORDER BY type, name LIMIT 129''', recovery._TABLES,
    ).fetchall()


def _create_catalogs(connection: sqlite3.Connection) -> None:
    for ddl in _DDL:
        connection.execute(ddl)


@contextmanager
def _new_bundle(directory: Path, filename: str, *, security: _OutputSecurity | None = None):
    _safe_path(directory)
    if directory.name.lower().endswith(('-wal', '-shm', '-journal', '.source-restore.sqlite3')):
        raise MaintenanceError('RESERVED_OUTPUT_NAME')
    temporary = directory / '.incomplete'
    created = False
    published = False
    try:
        with ExitStack() as stack:
            if directory.exists():
                raise MaintenanceError('OUTPUT_EXISTS')
            if security is None:
                # Internal disposable fixtures. The Windows CLI always supplies a native guard.
                directory.mkdir(mode=0o700)
                created = True
                fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                os.close(fd)
            else:
                api, parent, owner = security.api, security.parent, security.owner
                bundle = stack.enter_context(api.create_maintenance_item(parent, directory.name, owner, directory=True))
                created = True
                _check_file(api, bundle, directory, api.file_facts(parent).volume, directory=True)
                _require_private_acl(api.security(bundle), owner, directory=True)
                with api.create_maintenance_item(bundle, temporary.name, owner, directory=False) as staging:
                    _check_file(api, staging, temporary, api.file_facts(bundle).volume)
                    _require_private_acl(api.security(staging), owner, directory=False)
            yield temporary
            temporary.rename(directory / filename)
            published = True
    except FileExistsError:
        raise MaintenanceError('OUTPUT_EXISTS') from None
    except KeyStoreError:
        raise MaintenanceError('STORAGE_ACCESS_UNVERIFIED') from None
    finally:
        if created and not published:
            for owned in (temporary, *_sidecars(temporary)):
                owned.unlink(missing_ok=True)
            directory.rmdir()


def _coordinator(database: Path) -> recovery._RecoveryState:
    path = recovery._coordination_path(database)
    _standalone_database(path, maximum=1024 * 1024)
    return recovery._read_shared_state(path)


def _backup(database: Path, output: Path, *, output_security: _OutputSecurity | None = None) -> None:
    _safe_path(database)
    _safe_path(output)
    if output.is_relative_to(database.parent):
        raise MaintenanceError('OUTPUT_IN_DEPLOYMENT_DIRECTORY')
    for sidecar in _sidecars(database):
        _safe_path(sidecar)
    if not database.is_file():
        raise MaintenanceError('DATABASE_UNREADABLE')
    state = _coordinator(database)
    if state.pending:
        raise MaintenanceError('RESTORE_QUARANTINED')
    deadline = monotonic() + _MAINTENANCE_SECONDS
    with closing(sqlite3.connect(':memory:')) as snapshot:
        page_size, = snapshot.execute('PRAGMA page_size').fetchone()
        snapshot.execute(f'PRAGMA max_page_count = {_MAX_BYTES // page_size}')
        _create_catalogs(snapshot)
        with closing(_open_read(database, deadline)) as source:
            _check_integrity(source, deadline)
            if _catalog_schema(source) != _catalog_schema(snapshot):
                raise MaintenanceError('UNSUPPORTED_SCHEMA')
            checkpoint = recovery._checkpoint_connection(source, deadline)
            for table in recovery._TABLES:
                rows = source.execute(f'SELECT * FROM {table}')
                placeholders = ', '.join('?' for _ in rows.description)

                def checked_rows():
                    for row in rows:
                        if monotonic() >= deadline:
                            raise MaintenanceError('MAINTENANCE_TIMEOUT')
                        yield row

                snapshot.executemany(f'INSERT INTO {table} VALUES ({placeholders})', checked_rows())
        if not hmac.compare_digest(recovery._checkpoint_connection(snapshot, deadline), checkpoint):
            raise MaintenanceError('SNAPSHOT_MISMATCH')
        snapshot.execute(_MANIFEST)
        snapshot.execute('INSERT INTO source_backup_manifest VALUES (1, 1, ?, ?)', (state.generation, checkpoint))
        snapshot.commit()

        def check_progress(status, remaining, total):
            if monotonic() >= deadline:
                raise MaintenanceError('MAINTENANCE_TIMEOUT')

        with _new_bundle(output, _BACKUP, security=output_security) as temporary:
            with closing(sqlite3.connect(temporary, timeout=1)) as target:
                snapshot.backup(target, pages=128, progress=check_progress)
            if _inspect_backup(temporary, deadline) != checkpoint:
                raise MaintenanceError('SNAPSHOT_MISMATCH')
            if _coordinator(database) != state:
                raise MaintenanceError('RECOVERY_GENERATION_CHANGED')


def _inspect_backup(path: Path, deadline: float) -> str:
    _standalone_database(path)
    with closing(sqlite3.connect(':memory:')) as expected, closing(_open_read(path, deadline, standalone=True)) as copy:
        _create_catalogs(expected)
        expected.execute(_MANIFEST)
        query = 'SELECT type, name, tbl_name, sql FROM sqlite_schema ORDER BY type, name LIMIT 129'
        if copy.execute(query).fetchall() != expected.execute(query).fetchall():
            raise MaintenanceError('UNSUPPORTED_SCHEMA')
        _check_integrity(copy, deadline)
        rows = copy.execute('SELECT * FROM source_backup_manifest LIMIT 2').fetchall()
        if len(rows) != 1 or rows[0][:2] != (1, 1):
            raise MaintenanceError('INVALID_BACKUP_METADATA')
        _, _, generation, claimed = rows[0]
        if (not isinstance(generation, str) or _GENERATION.fullmatch(generation) is None
                or not isinstance(claimed, str) or _SHA256.fullmatch(claimed) is None):
            raise MaintenanceError('INVALID_BACKUP_METADATA')
        actual = recovery._checkpoint_connection(copy, deadline)
        if not hmac.compare_digest(actual, claimed):
            raise MaintenanceError('BACKUP_CHECKPOINT_MISMATCH')
        return actual


def _export_checkpoint(backup: Path, output: Path, *, output_security: _OutputSecurity | None = None) -> None:
    _safe_path(output)
    if output.is_relative_to(backup.parent):
        raise MaintenanceError('OUTPUT_IN_BACKUP_BUNDLE')
    checkpoint = _completed_backup(backup, monotonic() + _MAINTENANCE_SECONDS)
    with _new_bundle(output, _CHECKPOINT, security=output_security) as temporary:
        temporary.write_bytes(checkpoint.encode('ascii') + b'\n')


def _completed_backup(path: Path, deadline: float) -> str:
    if path.name != _BACKUP:
        raise MaintenanceError('COMPLETED_BACKUP_REQUIRED')
    return _inspect_backup(path, deadline)


def _read_checkpoint(path: Path) -> str:
    _safe_path(path)
    if not path.is_file():
        raise MaintenanceError('CHECKPOINT_UNREADABLE')
    with path.open('rb') as stream:
        raw = stream.read(67)
    if re.fullmatch(b'[0-9a-f]{64}(?:\r?\n)?', raw) is None:
        raise MaintenanceError('INVALID_CHECKPOINT')
    return raw[:64].decode('ascii')


def _inspect_destination(database: Path, deadline: float) -> None:
    # Keep preflight observational, including for a WAL-mode database whose
    # last connection already checkpointed and removed its sidecars.
    for sidecar in _sidecars(database):
        _safe_path(sidecar)
        if sidecar.exists():
            raise MaintenanceError('DESTINATION_SQLITE_ACTIVITY_POSSIBLE')
    try:
        with closing(_open_read(database, deadline, standalone=True)) as destination:
            _check_integrity(destination, deadline, failure_code='DESTINATION_INTEGRITY_UNVERIFIED')
            catalogs = destination.execute('''SELECT name FROM sqlite_schema
                WHERE type = 'table' AND name IN (?, ?)''', recovery._TABLES).fetchall()
            if {name for name, in catalogs} != set(recovery._TABLES):
                raise MaintenanceError('DESTINATION_CATALOGS_UNVERIFIED')
    except (OSError, sqlite3.Error):
        code = 'MAINTENANCE_TIMEOUT' if monotonic() >= deadline else 'DESTINATION_INTEGRITY_UNVERIFIED'
        raise MaintenanceError(code) from None
    for sidecar in _sidecars(database):
        _safe_path(sidecar)
        if sidecar.exists():
            raise MaintenanceError('DESTINATION_SQLITE_ACTIVITY_POSSIBLE')


def _preflight(database: Path, backup: Path, trusted_checkpoint: Path, expected_generation: str) -> Preflight:
    checks = []
    try:
        deadline = monotonic() + _MAINTENANCE_SECONDS
        checkpoint = _completed_backup(backup, deadline)
        checks.append('BACKUP_INTEGRITY_AND_SCHEMA_VALID')
        if not hmac.compare_digest(checkpoint, _read_checkpoint(trusted_checkpoint)):
            raise MaintenanceError('TRUSTED_CHECKPOINT_MISMATCH')
        checks.append('SUPPLIED_CHECKPOINT_MATCHES')
        _safe_path(database)
        if not database.is_file():
            raise MaintenanceError('DESTINATION_UNREADABLE')
        if backup == database or trusted_checkpoint == database:
            raise MaintenanceError('DESTINATION_PATH_COLLISION')
        state = _coordinator(database)
        if not state.pending:
            raise MaintenanceError('QUARANTINE_REQUIRED')
        if _GENERATION.fullmatch(expected_generation) is None or state.generation != expected_generation:
            raise MaintenanceError('RECOVERY_GENERATION_MISMATCH')
        checks.append('EXPECTED_QUARANTINE_GENERATION')
        _inspect_destination(database, deadline)
        checks.append('DESTINATION_INTEGRITY_VALID')
        if _coordinator(database) != state:
            raise MaintenanceError('RECOVERY_GENERATION_CHANGED')
        workers = recovery.inspect_recovery_maintenance(database, state)
    except MaintenanceError as exc:
        return Preflight('BLOCKED', tuple(checks), (str(exc),))
    except (OSError, sqlite3.Error, ValueError, ReasoningError):
        return Preflight('BLOCKED', tuple(checks), ('STORAGE_OR_COORDINATION_UNAVAILABLE',))
    blockers = ['CHECKPOINT_CUSTODY_UNVERIFIED', 'WORKER_QUIESCENCE_UNVERIFIED',
                'SUPERVISOR_RESTART_UNVERIFIED', 'SQLITE_CLIENT_INVENTORY_UNVERIFIED']
    if not workers.active:
        blockers.append('MAINTENANCE_BARRIER_REQUIRED')
    elif workers.awaiting_shutdown != 0:
        blockers.append('WORKER_SHUTDOWN_ACK_MISSING')
    elif workers.registered_workers == 0:
        checks.append('NO_REGISTERED_WORKERS_RECORDED')
    else:
        checks.append('REGISTERED_WORKERS_ACKNOWLEDGED')
    return Preflight('BLOCKED', tuple(checks), tuple(blockers))


def _local_operator(api: WindowsKeyStoreNative) -> str:
    if os.name != 'nt' or sys.version_info < (3, 13):
        raise MaintenanceError('OPERATOR_ENVIRONMENT_UNVERIFIED')
    try:
        thread = api.open_thread_token()
        if thread is not None:
            with _owned(api, thread):
                raise MaintenanceError('LOCAL_OPERATOR_REQUIRED')
        with _owned(api, api.open_process_token()) as token:
            facts = api.token_facts(token)
            if (type(facts) is not TokenFacts or facts.primary is not True or facts.logon_type not in (2, 11)
                    or not valid_service_sid(facts.sid) or not facts.sid.startswith('S-1-5-21-')
                    or facts.sid != facts.logon_sid
                    or api.elevated_administrator(token) is not True):
                raise MaintenanceError('LOCAL_OPERATOR_REQUIRED')
            return facts.sid
    except (AttributeError, OSError, TypeError, ValueError):
        raise MaintenanceError('LOCAL_OPERATOR_REQUIRED') from None


def _check_file(api, handle, path, volume, *, directory=False) -> None:
    facts = api.file_facts(handle)
    if (facts.path != path or facts.volume != volume or facts.attributes & UNSAFE_ATTRIBUTES
            or bool(facts.attributes & stat.FILE_ATTRIBUTE_DIRECTORY) != directory
            or (not directory and facts.links != 1)):
        raise MaintenanceError('UNSAFE_PATH')


def _guard_location(stack: ExitStack, api: WindowsKeyStoreNative, path: Path, owner: str, *, purpose: str) -> int:
    directory = path.parent
    if (not path.is_absolute() or re.fullmatch('[A-Za-z]:', path.drive) is None
            or directory == Path(path.anchor)
            or any(part in ('..', '.') or os.path.isreserved(part) for part in path.parts[1:])):
        raise MaintenanceError('UNSAFE_PATH')
    try:
        handle = stack.enter_context(_owned(api, api.open_drive(Path(path.anchor))))
        api.volume_root(handle)  # Require a fixed local NTFS volume with a real root.
        root = api.file_facts(handle)
        current = Path(path.anchor)
        _check_file(api, handle, current, root.volume, directory=True)
        for part in directory.parts[1:]:
            current /= part
            handle = stack.enter_context(_owned(api, api.open_child(
                handle, part, True, inspect_acl=current == directory,
            )))
            _check_file(api, handle, current, root.volume, directory=True)
        if not api.outside_cloud_root(handle):
            raise MaintenanceError('LOCAL_STORAGE_UNVERIFIED')
        _require_private_acl(api.security(handle), owner, directory=True)
        _safe_path(path)
        if purpose != 'output':
            paths = [path]
            if purpose in ('database', 'destination'):
                paths.append(recovery._coordination_path(path))
                for sidecar in _sidecars(path):
                    _safe_path(sidecar)
                    if sidecar.exists():
                        paths.append(sidecar)
            for source in paths:
                sharing = {'share_write': True} if purpose == 'control' else {}
                target = stack.enter_context(_owned(api, api.open_child(
                    handle, source.name, False, inspect_acl=True, **sharing,
                )))
                _check_file(api, target, source, root.volume)
                acl = api.security(target)
                if purpose == 'destination' and source == path and acl.owner != owner:
                    raise MaintenanceError('DESTINATION_OWNERSHIP_UNVERIFIED')
                _require_private_acl(acl, owner, directory=False, allow_inherited=True)
        return handle
    except (OSError, ValueError, KeyStoreError):
        raise MaintenanceError('STORAGE_ACCESS_UNVERIFIED') from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    backup = commands.add_parser('backup')
    backup.add_argument('--database', required=True, type=Path)
    backup.add_argument('--output-directory', required=True, type=Path)
    export = commands.add_parser('export-checkpoint')
    export.add_argument('--backup', required=True, type=Path)
    export.add_argument('--output-directory', required=True, type=Path)
    status = commands.add_parser('status')
    status.add_argument('--database', required=True, type=Path)
    for name in ('prepare-maintenance', 'cancel-maintenance'):
        transition = commands.add_parser(name)
        transition.add_argument('--database', required=True, type=Path)
        transition.add_argument('--expected-generation', required=True)
    preflight = commands.add_parser('preflight')
    preflight.add_argument('--database', required=True, type=Path)
    preflight.add_argument('--backup', required=True, type=Path)
    preflight.add_argument('--trusted-checkpoint', required=True, type=Path)
    preflight.add_argument('--expected-generation', required=True)
    args = parser.parse_args(argv)

    try:
        api = WindowsKeyStoreNative()
        owner = _local_operator(api)
        with ExitStack() as stack:
            if args.command == 'backup':
                _guard_location(stack, api, args.database, owner, purpose='database')
                parent = _guard_location(stack, api, args.output_directory, owner, purpose='output')
                _backup(args.database, args.output_directory, output_security=_OutputSecurity(api, parent, owner))
                result = {'status': 'BACKUP_CREATED', 'file': _BACKUP}
            elif args.command == 'export-checkpoint':
                _guard_location(stack, api, args.backup, owner, purpose='input')
                parent = _guard_location(stack, api, args.output_directory, owner, purpose='output')
                _export_checkpoint(args.backup, args.output_directory, output_security=_OutputSecurity(api, parent, owner))
                result = {'status': 'CHECKPOINT_EXPORTED_UNTRUSTED', 'file': _CHECKPOINT}
            elif args.command in ('status', 'prepare-maintenance', 'cancel-maintenance'):
                coordinator = recovery._coordination_path(args.database)
                purpose = 'input' if args.command == 'status' else 'control'
                _guard_location(stack, api, coordinator, owner, purpose=purpose)
                _safe_path(args.database)
                if not args.database.is_file():
                    raise MaintenanceError('DATABASE_UNREADABLE')
                if args.command == 'status':
                    state = _coordinator(args.database)
                    workers = recovery.inspect_recovery_maintenance(args.database, state)
                    result = {'status': 'COORDINATION_READ', 'pending': state.pending, 'generation': state.generation,
                              'maintenance': asdict(workers), 'supervisor_verified': False}
                else:
                    _standalone_database(coordinator, maximum=1024 * 1024)
                    active = args.command == 'prepare-maintenance'
                    recovery.set_recovery_maintenance(args.database, args.expected_generation, active=active)
                    result = {'status': 'MAINTENANCE_PREPARED' if active else 'MAINTENANCE_CANCELLED',
                              'restore_authorized': False}
            else:
                _guard_location(stack, api, args.database, owner, purpose='destination')
                _guard_location(stack, api, args.backup, owner, purpose='input')
                _guard_location(stack, api, args.trusted_checkpoint, owner, purpose='input')
                result = asdict(_preflight(args.database, args.backup, args.trusted_checkpoint, args.expected_generation))
    except MaintenanceError as exc:
        result = {'status': 'BLOCKED', 'checks': [], 'blockers': [str(exc)]}
    except (OSError, sqlite3.Error, ValueError, ReasoningError):
        result = {'status': 'BLOCKED', 'checks': [], 'blockers': ['STORAGE_OR_COORDINATION_UNAVAILABLE']}
    except KeyboardInterrupt:
        result = {'status': 'BLOCKED', 'checks': [], 'blockers': ['OPERATION_INTERRUPTED']}
    print(json.dumps(result, separators=(',', ':')))
    return 2 if result['status'] == 'BLOCKED' else 0


if __name__ == '__main__':
    raise SystemExit(main())
