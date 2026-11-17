"""Dedicated Windows service identity and handle-relative sealed-file access."""
from __future__ import annotations

import hashlib
import ntpath
import struct
from contextlib import AbstractContextManager, ExitStack, contextmanager
from pathlib import PureWindowsPath
from typing import Protocol

from backend.security.provenance_keystore import (
    MAX_RECORD_BYTES, SERVICE_IDENTITY, EffectiveKeyStoreEvidence, KeyProtector, KeyReference,
    KeyStoreCode, KeyStoreError, KeyStorePolicy, ProtectedProvenanceKeyStore, ProvenanceKeyRing,
    SealedKey, TrustedAnchorProvider, TrustedAnchorSnapshot, valid_service_sid,
)
from backend.security.provenance_windows_native import (
    DIRECTORY, FILE_ALL_ACCESS, SYSTEM_SID, UNSAFE_ATTRIBUTES, FileFacts,
    FileSecurity, TokenFacts, WindowsKeyStoreNative, binary_sid,
)


class WindowsIdentityApi(Protocol):
    def open_thread_token(self) -> int | None: ...
    def open_process_token(self) -> int: ...
    def close(self, handle: int) -> None: ...
    def token_facts(self, handle: int) -> TokenFacts: ...
    def service_sid(self, virtual: bool) -> str: ...
    def profile_flags(self) -> int: ...


class WindowsKeyFilesApi(WindowsIdentityApi, Protocol):
    def open_drive(self, path: PureWindowsPath) -> int: ...
    def volume_root(self, handle: int) -> str: ...
    def open_child(self, parent: int, name: str, directory: bool, *, inspect_acl: bool) -> int: ...
    def file_facts(self, handle: int) -> FileFacts: ...
    def outside_cloud_root(self, handle: int) -> bool: ...
    def security(self, handle: int) -> FileSecurity: ...
    def read(self, handle: int, count: int) -> bytes: ...
    def create_record(self, parent: int, name: str, service_sid: str) -> AbstractContextManager[int]: ...
    def write(self, handle: int, raw: bytes) -> None: ...
    def flush(self, handle: int) -> None: ...


def _require(condition, code: KeyStoreCode) -> None:
    if not condition:
        raise KeyStoreError(code)


@contextmanager
def _owned(api: WindowsIdentityApi, handle: int):
    if type(handle) is not int or handle <= 0:
        raise ValueError
    try:
        yield handle
    finally:
        api.close(handle)


class WindowsServiceIdentity:
    def __init__(self, expected_sid: str, api: WindowsIdentityApi):
        self._sid = expected_sid
        self._api = api

    def require(self) -> str:
        _require(valid_service_sid(self._sid), KeyStoreCode.IDENTITY)
        try:
            thread = self._api.open_thread_token()
            if thread is not None:
                with _owned(self._api, thread):
                    raise KeyStoreError(KeyStoreCode.IDENTITY)
            with _owned(self._api, self._api.open_process_token()) as token:
                facts = self._api.token_facts(token)
                _require(type(facts) is TokenFacts and facts.primary is True and facts.sid == self._sid
                         and facts.logon_sid == facts.sid and type(facts.logon_type) is int
                         and facts.logon_type == 5, KeyStoreCode.IDENTITY)  # Service logon.
                resolved = self._api.service_sid(self._sid.startswith("S-1-5-80-"))
                _require(type(resolved) is str and resolved == facts.sid, KeyStoreCode.IDENTITY)
            try:
                flags = self._api.profile_flags()
            except Exception:
                raise KeyStoreError(KeyStoreCode.PROFILE) from None
            # GetProfileType must positively report an ordinary loaded local profile.
            _require(type(flags) is int and flags == 0, KeyStoreCode.PROFILE)
            return facts.sid
        except KeyStoreError:
            raise
        except Exception:
            raise KeyStoreError(KeyStoreCode.IDENTITY) from None


def _require_private_acl(security: FileSecurity, sid: str, *, directory: bool) -> None:
    code = KeyStoreCode.STORAGE
    allowed = {SYSTEM_SID, sid}
    _require(type(security) is FileSecurity and type(security.owner) is str
             and security.owner in allowed and type(security.control) is int
             and security.control & 0x100C == 0x1004, code)  # PROTECTED | PRESENT; never DEFAULTED.
    raw = security.dacl
    _require(type(raw) is bytes and 8 <= len(raw) <= 65535, code)
    declared, count = struct.unpack_from("<HH", raw, 2)
    revision = raw[0]
    _require(revision in (2, 4) and len(raw) <= declared and count == 2, code)
    offset = 8
    for _ in range(count):
        _require(offset + 8 <= len(raw), code)
        kind, flags, size, mask = struct.unpack_from("<BBHI", raw, offset)
        end = offset + size
        _require(kind == 0 and flags in ((0, 3) if directory else (0,))
                 and size >= 20 and end <= len(raw) and mask == FILE_ALL_ACCESS, code)
        try:
            ace_sid = binary_sid(raw[offset + 8:end])
        except ValueError:
            raise KeyStoreError(code) from None
        _require(ace_sid in allowed, code)
        allowed.remove(ace_sid)
        offset = end
    _require(offset == len(raw) and not allowed, code)


class WindowsProtectedKeyFiles:
    """Re-attest on every operation. Keep directory handles open through the final I/O."""

    def __init__(self, policy: KeyStorePolicy, api: WindowsKeyFilesApi):
        self._policy = policy
        self._api = api
        self.identity = WindowsServiceIdentity(policy.expected_sid, api)

    def _facts(self, handle, path, *, directory, volume=None) -> FileFacts:
        facts = self._api.file_facts(handle)
        _require(type(facts) is FileFacts and isinstance(facts.path, PureWindowsPath) and facts.path == path
                 and type(facts.volume) is int and (volume is None or facts.volume == volume)
                 and type(facts.attributes) is int and facts.attributes >= 0
                 and facts.attributes & UNSAFE_ATTRIBUTES == 0
                 and bool(facts.attributes & DIRECTORY) is directory, KeyStoreCode.STORAGE)
        return facts

    def _directory(self, stack, target, *, private_sid=None):
        path = PureWindowsPath(target.anchor)
        handle = stack.enter_context(_owned(self._api, self._api.open_drive(path)))
        drive = self._facts(handle, path, directory=True)
        volume_root = self._api.volume_root(handle)
        _require(type(volume_root) is str and bool(volume_root), KeyStoreCode.STORAGE)
        for part in target.parts[1:]:
            isreserved = getattr(ntpath, "isreserved", None)
            reserved = isreserved(part) if isreserved is not None else PureWindowsPath(part).is_reserved()
            _require(not reserved, KeyStoreCode.STORAGE)
            path /= part
            private = private_sid is not None and path in (self._policy.root, target)
            child = self._api.open_child(handle, part, True, inspect_acl=private)
            handle = stack.enter_context(_owned(self._api, child))
            self._facts(handle, path, directory=True, volume=drive.volume)
            if private:
                self._private(handle, private_sid, directory=True)
        return handle, drive.volume, volume_root

    @contextmanager
    def _keys_directory(self):
        policy = self._policy
        policy.require_location()
        sid = self.identity.require()
        with ExitStack() as stack:
            parent, volume, volume_root = self._directory(stack, policy.root / "keys", private_sid=sid)
            root = PureWindowsPath(*policy.root.parts[1:])
            exclusions = (policy.repository, policy.conversation_storage, policy.sandbox_storage,
                          *policy.cloud_sync_roots)
            for path in exclusions:
                _, _, excluded_volume = self._directory(stack, path)
                excluded = PureWindowsPath(*path.parts[1:])
                _require(excluded_volume != volume_root or not (
                    root.is_relative_to(excluded) or excluded.is_relative_to(root)
                ), KeyStoreCode.STORAGE)
            yield parent, volume, sid

    def _private(self, handle, sid, *, directory):
        _require(self._api.outside_cloud_root(handle) is True, KeyStoreCode.STORAGE)
        _require_private_acl(self._api.security(handle), sid, directory=directory)

    def attest(self) -> EffectiveKeyStoreEvidence:
        try:
            with self._keys_directory() as (_, _, sid):
                return EffectiveKeyStoreEvidence(SERVICE_IDENTITY, sid, True, self._policy.root, True, True, True)
        except KeyStoreError:
            raise
        except Exception:
            raise KeyStoreError(KeyStoreCode.STORAGE) from None

    def _record_name(self, path) -> str:
        code = KeyStoreCode.RECORD_INVALID
        _require(isinstance(path, PureWindowsPath) and path.name.endswith(".sealed"), code)
        expected = self._policy.record_path(path.name[:-7])
        _require(path == expected, code)
        return expected.name

    def _record_facts(self, handle, name, volume, sid):
        facts = self._facts(handle, self._policy.root / "keys" / name, directory=False, volume=volume)
        _require(facts.path.name == name and type(facts.links) is int and facts.links == 1
                 and type(facts.size) is int and facts.size >= 0, KeyStoreCode.RECORD_INVALID)
        self._private(handle, sid, directory=False)
        return facts

    def read(self, path: PureWindowsPath, limit: int) -> bytes:
        name = self._record_name(path)
        code = KeyStoreCode.RECORD_INVALID
        _require(type(limit) is int and 0 < limit <= MAX_RECORD_BYTES, code)
        try:
            with self._keys_directory() as (parent, volume, sid):
                record = self._api.open_child(parent, name, False, inspect_acl=True)
                with _owned(self._api, record) as handle:
                    before = self._record_facts(handle, name, volume, sid)
                    _require(0 < before.size <= limit, code)
                    raw = self._api.read(handle, before.size)
                    _require(type(raw) is bytes and len(raw) == before.size
                             and self._record_facts(handle, name, volume, sid) == before, code)
                    return raw
        except KeyStoreError:
            raise
        except Exception:
            raise KeyStoreError(KeyStoreCode.RECORD_UNAVAILABLE) from None

    def stage_candidate(self, path: PureWindowsPath, raw: bytes) -> None:
        name = self._record_name(path)
        record, _ = SealedKey.parse(raw)
        _require(self._policy.record_path(record.key_id).name == name, KeyStoreCode.RECORD_INVALID)
        try:
            with self._keys_directory() as (parent, volume, sid):
                with self._api.create_record(parent, name, sid) as handle:
                    before = self._record_facts(handle, name, volume, sid)
                    _require(before.size == 0, KeyStoreCode.STAGE_FAILED)
                    self._api.write(handle, raw)
                    self._api.flush(handle)
                    after = self._record_facts(handle, name, volume, sid)
                    _require(after.size == len(raw), KeyStoreCode.STAGE_FAILED)
        except KeyStoreError:
            raise
        except Exception:
            raise KeyStoreError(KeyStoreCode.STAGE_FAILED) from None


class _ServiceProtector:
    def __init__(self, identity: WindowsServiceIdentity, protector: KeyProtector):
        self._identity = identity
        self._protector = protector

    def seal(self, key: bytes) -> bytes:
        self._identity.require()
        return self._protector.seal(key)

    def unseal(self, ciphertext: bytes) -> bytes:
        self._identity.require()
        return self._protector.unseal(ciphertext)


def windows_provenance_store(policy: KeyStorePolicy, *, anchor: TrustedAnchorProvider | None = None,
                             protector: KeyProtector | None = None,
                             api: WindowsKeyFilesApi | None = None) -> ProtectedProvenanceKeyStore:
    """Load-only composition for offline verification of the existing startup gate."""
    if anchor is None or protector is None or api is None or isinstance(api, WindowsKeyStoreNative):
        raise KeyStoreError(KeyStoreCode.BLOCKED)
    files = WindowsProtectedKeyFiles(policy, api)
    return ProtectedProvenanceKeyStore(policy, files, _ServiceProtector(files.identity, protector), anchor)


class TrustedRotationLease(TrustedAnchorProvider, Protocol):
    """Fenced exclusive session. Reads reflect the current authoritative snapshot."""

    def compare_and_swap(self, previous: TrustedAnchorSnapshot, candidate: TrustedAnchorSnapshot) -> bool:
        """Durably commit the exact next snapshot, or return False for definite non-publication."""
        ...


class TrustedRotationAuthority(TrustedAnchorProvider, Protocol):
    """An independent anti-rollback authority with cross-process signer/writer exclusion."""

    def rotation_window(self) -> AbstractContextManager[TrustedRotationLease | None]:
        """Quiesce signers, fence startup and writers through readback. Yield None on contention."""
        ...


class WindowsRotationPublisher:
    """Publish one flushed candidate through an already-held trusted rotation lease."""

    def __init__(self, files: WindowsProtectedKeyFiles, lease: TrustedRotationLease):
        self._files = files
        self._lease = lease
        self._staged = None

    def stage(self, path: PureWindowsPath, raw: bytes) -> None:
        _require(self._staged is None, KeyStoreCode.ROTATION_INVALID)
        record, _ = SealedKey.parse(raw)
        self._files.stage_candidate(path, raw)
        _require(self._files.read(path, MAX_RECORD_BYTES) == raw, KeyStoreCode.STAGE_FAILED)
        self._staged = KeyReference(record.key_id, record.epoch, hashlib.sha256(raw).hexdigest())

    def publish(self, previous: TrustedAnchorSnapshot, candidate: TrustedAnchorSnapshot) -> None:
        old, new = previous.validate(), candidate.validate()
        prior_references = {old.reference(), *previous.retained}
        _require(self._staged == new.reference() and new.active_epoch == old.active_epoch + 1
                 and all(new.active_key_id != ref.key_id for ref in prior_references)
                 and set(candidate.retained).issubset(prior_references), KeyStoreCode.ANCHOR_EPOCH)
        _require(self._lease.read() == previous, KeyStoreCode.ANCHOR_EPOCH)
        try:
            committed = self._lease.compare_and_swap(previous, candidate)
        except Exception:
            raise KeyStoreError(KeyStoreCode.PUBLICATION_UNCERTAIN) from None
        if committed is False:
            raise KeyStoreError(KeyStoreCode.ROTATION_CONFLICT)
        _require(committed is True, KeyStoreCode.PUBLICATION_UNCERTAIN)


def rotate_windows_provenance_keys(
    policy: KeyStorePolicy, key_id: str, created_at: str, *, retain: tuple[str, ...] = (),
    authority: TrustedRotationAuthority | None = None, protector: KeyProtector | None = None,
    api: WindowsKeyFilesApi | None = None,
) -> ProvenanceKeyRing:
    """Hold the authority's exclusive window from the first read through signer confirmation."""
    if (authority is None or not callable(getattr(authority, "rotation_window", None))
            or protector is None or api is None or isinstance(api, WindowsKeyStoreNative)):
        raise KeyStoreError(KeyStoreCode.BLOCKED)
    files = WindowsProtectedKeyFiles(policy, api)
    try:
        with authority.rotation_window() as lease:
            _require(lease is not None, KeyStoreCode.ROTATION_CONFLICT)
            _require(callable(getattr(lease, "compare_and_swap", None)), KeyStoreCode.BLOCKED)
            store = ProtectedProvenanceKeyStore(
                policy, files, _ServiceProtector(files.identity, protector),
                lease, WindowsRotationPublisher(files, lease),
            )
            return store.rotate(key_id, created_at, retain=retain)
    except KeyStoreError:
        raise
    except Exception:
        raise KeyStoreError(KeyStoreCode.PUBLICATION_UNCERTAIN) from None
