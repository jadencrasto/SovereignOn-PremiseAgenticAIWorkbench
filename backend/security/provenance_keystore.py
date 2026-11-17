"""Fail-closed key-ring validation, independent of Windows IO and crypto."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from dataclasses import asdict, dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import PureWindowsPath
from types import MappingProxyType
from typing import Mapping, Protocol

from backend.security.provenance_dpapi import KEY_BYTES, MAX_SEALED_BLOB_BYTES

SERVICE_IDENTITY = "SovereignAISvc"
MAX_RECORD_BYTES = 24 * 1024
MAX_ANCHOR_BYTES = 1024
MAX_RETAINED_KEYS = 8
_MAX_EPOCH = 2**63 - 1
_KEY_ID = re.compile(r"[0-9a-f]{32}").fullmatch
_DIGEST = re.compile(r"[0-9a-f]{64}").fullmatch
_SERVICE_SID = re.compile(r"S-1-5-(?:21(?:-[0-9]{1,10}){4}|80(?:-[0-9]{1,10}){5})").fullmatch


class KeyStoreCode(StrEnum):
    BLOCKED = "provenance_durable_blocked"
    IDENTITY = "provenance_identity_unverified"
    PROFILE = "provenance_profile_unverified"
    STORAGE = "provenance_storage_unverified"
    ANCHOR_MISSING = "provenance_anchor_missing"
    ANCHOR_UNAVAILABLE = "provenance_anchor_unavailable"
    ANCHOR_INVALID = "provenance_anchor_invalid"
    ANCHOR_EPOCH = "provenance_anchor_epoch_mismatch"
    RECORD_UNAVAILABLE = "provenance_record_unavailable"
    RECORD_INVALID = "provenance_record_invalid"
    DIGEST_MISMATCH = "provenance_digest_mismatch"
    UNSEAL_FAILED = "provenance_unseal_failed"
    KEY_INVALID = "provenance_key_invalid"
    ROTATION_INVALID = "provenance_rotation_invalid"
    SEAL_FAILED = "provenance_seal_failed"
    STAGE_FAILED = "provenance_stage_failed"
    PUBLICATION_UNCERTAIN = "provenance_publication_uncertain"
    SIGNER_UNCONFIRMED = "provenance_signer_unconfirmed"


class KeyStoreError(RuntimeError):
    def __init__(self, code: KeyStoreCode):
        self.code = code
        super().__init__(code.value)


def _matches(value, pattern) -> bool:
    return type(value) is str and pattern(value) is not None


def _epoch(value) -> bool:
    return type(value) is int and 0 < value <= _MAX_EPOCH


def _utc(value) -> bool:
    if type(value) is not str or not 20 <= len(value) <= 27 or not value.endswith("Z"):
        return False
    try:
        return datetime.fromisoformat(value).isoformat().replace("+00:00", "Z") == value
    except ValueError:
        return False


def _canonical(record) -> bytes:
    return json.dumps(asdict(record), sort_keys=True, separators=(",", ":")).encode("ascii")


def _parse(raw, cls, limit, code):
    if type(raw) is not bytes or not 0 < len(raw) <= limit:
        raise KeyStoreError(code)
    try:
        record = cls(**json.loads(raw.decode("ascii")))
        # Equality also rejects duplicate keys and alternative field encodings.
        if raw != _canonical(record):
            raise ValueError
        return record
    except (TypeError, ValueError, RecursionError):
        raise KeyStoreError(code) from None


@dataclass(frozen=True)
class KeyReference:
    key_id: str
    epoch: int
    sealed_blob_sha256: str

    def valid(self) -> bool:
        return _matches(self.key_id, _KEY_ID) and _epoch(self.epoch) and _matches(self.sealed_blob_sha256, _DIGEST)


@dataclass(frozen=True, repr=False)
class SealedKey:
    version: int
    key_id: str
    epoch: int
    created_at: str
    algorithm: str
    ciphertext_b64: str

    @classmethod
    def parse(cls, raw: bytes) -> tuple[SealedKey, bytes]:
        code = KeyStoreCode.RECORD_INVALID
        record = _parse(raw, cls, MAX_RECORD_BYTES, code)
        if (type(record.version) is not int or record.version != 1
                or not _matches(record.key_id, _KEY_ID) or not _epoch(record.epoch)
                or not _utc(record.created_at) or record.algorithm != "HMAC-SHA256"
                or type(record.ciphertext_b64) is not str):
            raise KeyStoreError(code)
        try:
            ciphertext = base64.b64decode(record.ciphertext_b64, validate=True)
        except ValueError:
            raise KeyStoreError(code) from None
        if (not 0 < len(ciphertext) <= MAX_SEALED_BLOB_BYTES
                or base64.b64encode(ciphertext).decode("ascii") != record.ciphertext_b64):
            raise KeyStoreError(code)
        return record, ciphertext

    def serialize(self) -> bytes:
        raw = _canonical(self)
        self.parse(raw)
        return raw


@dataclass(frozen=True)
class KeyAnchor:
    anchor_version: int
    service_identity: str
    active_key_id: str
    active_epoch: int
    sealed_blob_sha256: str
    updated_at: str

    def reference(self) -> KeyReference:
        return KeyReference(self.active_key_id, self.active_epoch, self.sealed_blob_sha256)

    @classmethod
    def parse(cls, raw: bytes) -> KeyAnchor:
        code = KeyStoreCode.ANCHOR_INVALID
        anchor = _parse(raw, cls, MAX_ANCHOR_BYTES, code)
        if (type(anchor.anchor_version) is not int or anchor.anchor_version != 1
                or anchor.service_identity != SERVICE_IDENTITY or not anchor.reference().valid()
                or not _utc(anchor.updated_at)):
            raise KeyStoreError(code)
        return anchor

    def serialize(self) -> bytes:
        raw = _canonical(self)
        self.parse(raw)
        return raw


@dataclass(frozen=True)
class TrustedAnchorSnapshot:
    # Produced by a protected provider. The epoch and retained references are
    # independently protected provider metadata, not fields in anchor.json.
    raw_anchor: bytes = field(repr=False)
    authoritative_epoch: int
    retained: tuple[KeyReference, ...] = ()

    def validate(self) -> KeyAnchor:
        anchor = KeyAnchor.parse(self.raw_anchor)
        if not _epoch(self.authoritative_epoch) or anchor.active_epoch != self.authoritative_epoch:
            raise KeyStoreError(KeyStoreCode.ANCHOR_EPOCH)
        if type(self.retained) is not tuple or len(self.retained) > MAX_RETAINED_KEYS:
            raise KeyStoreError(KeyStoreCode.ANCHOR_INVALID)
        ids = {anchor.active_key_id}
        epochs = {anchor.active_epoch}
        for item in self.retained:
            if (type(item) is not KeyReference or not item.valid() or item.epoch >= anchor.active_epoch
                    or item.key_id in ids or item.epoch in epochs):
                raise KeyStoreError(KeyStoreCode.ANCHOR_INVALID)
            ids.add(item.key_id)
            epochs.add(item.epoch)
        return anchor


@dataclass(frozen=True)
class EffectiveKeyStoreEvidence:
    """Attested by the platform boundary. Configuration cannot supply these fields."""
    service_identity: str
    token_sid: str
    profile_loaded_non_roaming: bool
    root: PureWindowsPath
    # Effective access limits key custody to SYSTEM and the dedicated service principal.
    effective_ntfs_acl_verified: bool
    no_broad_inherited_write: bool
    no_unsafe_aliases_or_reparse_points: bool


def _local_path(path: PureWindowsPath) -> bool:
    return (isinstance(path, PureWindowsPath) and path.is_absolute()
            and re.fullmatch(r"[A-Za-z]:", path.drive) is not None
            and all(part not in (".", "..") and not part.endswith((".", " "))
                    and re.search(r'[<>:"/\\|?*\x00-\x1f]', part) is None
                    for part in path.parts[1:]))


@dataclass(frozen=True)
class KeyStorePolicy:
    expected_sid: str
    root: PureWindowsPath
    repository: PureWindowsPath
    conversation_storage: PureWindowsPath
    sandbox_storage: PureWindowsPath
    cloud_sync_roots: tuple[PureWindowsPath, ...] = ()

    def require(self, evidence: EffectiveKeyStoreEvidence) -> None:
        if (type(evidence) is not EffectiveKeyStoreEvidence or evidence.service_identity != SERVICE_IDENTITY
                or not _matches(self.expected_sid, _SERVICE_SID)
                or any(str(int(part)) != part or int(part) > 0xFFFFFFFF
                       for part in self.expected_sid.split("-")[4:])
                or evidence.token_sid != self.expected_sid):
            raise KeyStoreError(KeyStoreCode.IDENTITY)
        if evidence.profile_loaded_non_roaming is not True:
            raise KeyStoreError(KeyStoreCode.PROFILE)
        disallowed = (self.repository, self.conversation_storage, self.sandbox_storage, *self.cloud_sync_roots)
        if (not _local_path(self.root) or len(self.root.parts) == 1
                or not _local_path(evidence.root) or evidence.root != self.root
                or evidence.effective_ntfs_acl_verified is not True
                or evidence.no_broad_inherited_write is not True
                or evidence.no_unsafe_aliases_or_reparse_points is not True
                or any(not _local_path(path) or self.root.is_relative_to(path)
                       or path.is_relative_to(self.root) for path in disallowed)
                or any(part.casefold().startswith("onedrive") for part in self.root.parts[1:])):
            raise KeyStoreError(KeyStoreCode.STORAGE)


class ProtectedKeyFiles(Protocol):
    def attest(self) -> EffectiveKeyStoreEvidence:
        """Observe the real token, profile, resolved root and effective filesystem access."""
        ...

    def read(self, path: PureWindowsPath, limit: int) -> bytes:
        """Read at most limit bytes through a handle under the attested root and deny overflow."""
        ...


class TrustedAnchorProvider(Protocol):
    """Protect integrity and rollback state outside the sealed-file writer's boundary."""

    def read(self) -> TrustedAnchorSnapshot | None:
        """Read one authenticated snapshot of anchor.json, its authoritative epoch and retained references."""
        ...


class KeyProtector(Protocol):
    def seal(self, key: bytes) -> bytes: ...
    def unseal(self, ciphertext: bytes) -> bytes: ...


class ProtectedRotationPublisher(Protocol):
    """Used only within an externally enforced exclusive window with signing stopped."""

    def stage(self, path: PureWindowsPath, raw: bytes) -> None:
        """Publish the durable sealed candidate with create-new semantics and verified ACLs."""
        ...

    def publish(self, previous: TrustedAnchorSnapshot, candidate: TrustedAnchorSnapshot) -> None:
        """Commit by durable, monotonic CAS during an exclusive offline rotation window."""
        ...


@dataclass(frozen=True, repr=False)
class ProvenanceKeyRing:
    active_id: str
    _keys: Mapping[str, bytes]

    def __post_init__(self):
        keys = dict(self._keys)
        if (not _matches(self.active_id, _KEY_ID) or self.active_id not in keys
                or not 1 <= len(keys) <= MAX_RETAINED_KEYS + 1
                or any(not _matches(key_id, _KEY_ID) or type(key) is not bytes or len(key) != KEY_BYTES
                       for key_id, key in keys.items())):
            raise KeyStoreError(KeyStoreCode.KEY_INVALID)
        object.__setattr__(self, "_keys", MappingProxyType(keys))

    def signing_key(self) -> tuple[str, bytes]:
        return self.active_id, self._keys[self.active_id]

    def verification_key(self, key_id: str) -> bytes | None:
        return self._keys.get(key_id) if _matches(key_id, _KEY_ID) else None


class ProtectedProvenanceKeyStore:
    def __init__(self, policy: KeyStorePolicy, files: ProtectedKeyFiles, protector: KeyProtector,
                 anchor: TrustedAnchorProvider | None, publisher: ProtectedRotationPublisher | None = None):
        self._policy = policy
        self._files = files
        self._protector = protector
        self._anchor = anchor
        self._publisher = publisher

    def _snapshot(self) -> tuple[TrustedAnchorSnapshot, KeyAnchor]:
        if self._anchor is None:
            raise KeyStoreError(KeyStoreCode.BLOCKED)
        try:
            evidence = self._files.attest()
        except Exception:
            raise KeyStoreError(KeyStoreCode.STORAGE) from None
        self._policy.require(evidence)
        try:
            snapshot = self._anchor.read()
        except Exception:
            raise KeyStoreError(KeyStoreCode.ANCHOR_UNAVAILABLE) from None
        if snapshot is None:
            raise KeyStoreError(KeyStoreCode.ANCHOR_MISSING)
        if type(snapshot) is not TrustedAnchorSnapshot:
            raise KeyStoreError(KeyStoreCode.ANCHOR_INVALID)
        return snapshot, snapshot.validate()

    def _path(self, key_id: str) -> PureWindowsPath:
        if not _matches(key_id, _KEY_ID):
            raise KeyStoreError(KeyStoreCode.RECORD_INVALID)
        return self._policy.root / "keys" / f"{key_id}.sealed"

    def _load_key(self, reference: KeyReference) -> bytes:
        try:
            raw = self._files.read(self._path(reference.key_id), MAX_RECORD_BYTES)
        except Exception:
            raise KeyStoreError(KeyStoreCode.RECORD_UNAVAILABLE) from None
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_RECORD_BYTES:
            raise KeyStoreError(KeyStoreCode.RECORD_INVALID)
        if not hmac.compare_digest(hashlib.sha256(raw).hexdigest(), reference.sealed_blob_sha256):
            raise KeyStoreError(KeyStoreCode.DIGEST_MISMATCH)
        record, ciphertext = SealedKey.parse(raw)
        if (record.key_id, record.epoch) != (reference.key_id, reference.epoch):
            raise KeyStoreError(KeyStoreCode.RECORD_INVALID)
        try:
            key = self._protector.unseal(ciphertext)
        except Exception:
            raise KeyStoreError(KeyStoreCode.UNSEAL_FAILED) from None
        if type(key) is not bytes or len(key) != KEY_BYTES:
            raise KeyStoreError(KeyStoreCode.KEY_INVALID)
        return key

    def load(self) -> ProvenanceKeyRing:
        snapshot, anchor = self._snapshot()
        return self._load_ring(snapshot, anchor)

    def _load_ring(self, snapshot: TrustedAnchorSnapshot, anchor: KeyAnchor) -> ProvenanceKeyRing:
        references = (anchor.reference(), *snapshot.retained)
        return ProvenanceKeyRing(anchor.active_key_id, {ref.key_id: self._load_key(ref) for ref in references})

    def rotate(self, key_id: str, created_at: str, *, retain: tuple[str, ...] = ()) -> ProvenanceKeyRing:
        """Offline only. A protected publisher must enforce the exclusive rotation window."""
        if self._publisher is None:
            raise KeyStoreError(KeyStoreCode.BLOCKED)
        previous, anchor = self._snapshot()
        references = {r.key_id: r for r in (anchor.reference(), *previous.retained)}
        if (not _matches(key_id, _KEY_ID) or key_id in references or not _utc(created_at)
                or anchor.active_epoch == _MAX_EPOCH or type(retain) is not tuple
                or len(retain) > MAX_RETAINED_KEYS
                or not all(_matches(ref, _KEY_ID) and ref in references for ref in retain)
                or len(set(retain)) != len(retain)):
            raise KeyStoreError(KeyStoreCode.ROTATION_INVALID)
        retained = tuple(references[ref] for ref in retain)
        for reference in (anchor.reference(), *retained):
            self._load_key(reference)
        try:
            key = secrets.token_bytes(KEY_BYTES)
            if type(key) is not bytes or len(key) != KEY_BYTES:
                raise ValueError
            ciphertext = self._protector.seal(key)
            if type(ciphertext) is not bytes or not 0 < len(ciphertext) <= MAX_SEALED_BLOB_BYTES:
                raise ValueError
            unsealed = self._protector.unseal(ciphertext)
            if type(unsealed) is not bytes or not hmac.compare_digest(unsealed, key):
                raise ValueError
        except Exception:
            raise KeyStoreError(KeyStoreCode.SEAL_FAILED) from None
        epoch = anchor.active_epoch + 1
        raw = SealedKey(1, key_id, epoch, created_at, "HMAC-SHA256",
                        base64.b64encode(ciphertext).decode("ascii")).serialize()
        path = self._path(key_id)
        try:
            self._publisher.stage(path, raw)
            staged = self._files.read(path, MAX_RECORD_BYTES)
            if type(staged) is not bytes or staged != raw:
                raise ValueError
        except Exception:
            raise KeyStoreError(KeyStoreCode.STAGE_FAILED) from None
        digest = hashlib.sha256(staged).hexdigest()
        candidate = TrustedAnchorSnapshot(
            KeyAnchor(1, SERVICE_IDENTITY, key_id, epoch, digest, created_at).serialize(), epoch, retained
        )
        try:
            self._publisher.publish(previous, candidate)
        except Exception:
            raise KeyStoreError(KeyStoreCode.PUBLICATION_UNCERTAIN) from None
        try:
            current, current_anchor = self._snapshot()
            if current != candidate:
                raise KeyStoreError(KeyStoreCode.ANCHOR_EPOCH)
            ring = self._load_ring(current, current_anchor)
            if not hmac.compare_digest(ring.signing_key()[1], key):
                raise KeyStoreError(KeyStoreCode.KEY_INVALID)
            return ring
        except KeyStoreError:
            raise KeyStoreError(KeyStoreCode.SIGNER_UNCONFIRMED) from None


def startup_provenance_keys(mode: str, store: ProtectedProvenanceKeyStore | None = None) -> ProvenanceKeyRing | None:
    if mode == "ephemeral":
        return None
    if mode != "durable" or store is None:
        raise KeyStoreError(KeyStoreCode.BLOCKED)
    ring = store.load()
    if type(ring) is not ProvenanceKeyRing:
        raise KeyStoreError(KeyStoreCode.SIGNER_UNCONFIRMED)
    return ring
