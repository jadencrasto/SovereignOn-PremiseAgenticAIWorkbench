"""Synthetic key blobs, independent anchor state, and no native DPAPI."""

import base64
import ctypes
import hashlib
import json
from dataclasses import replace
from pathlib import PureWindowsPath

import httpx
import pytest

from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.memory import ConversationMemory
from backend.security import provenance_dpapi as dpapi
from backend.security import provenance_keystore as ks
from test_a21_conversation_offline import immediate, make_app, setup_engine
from test_a22_async_offline import run_offline
from test_r13_lifecycle_readiness import config
from test_r813_provenance_offline import owner  # pytest fixture

ACTIVE = "a" * 32
CANDIDATE = "b" * 32
TIME = "2026-11-17T00:00:00Z"
SID = "S-1-5-21-1-2-3-1001"
ROOT = PureWindowsPath(r"C:\Sovereign\keystore")
MARKER = "sensitive-test-marker"
KEY = MARKER.encode().ljust(32, b"!")
NEXT_KEY = b"n" * 32
CIPHERTEXT = b"fixture-sealed"
NEXT_CIPHERTEXT = b"fixture-next-sealed"
LOAD_DPAPI = dpapi._load_api


@pytest.fixture(autouse=True)
def forbid_native(monkeypatch):
    monkeypatch.setattr(dpapi, "_load_api", lambda: pytest.fail("Native DPAPI is forbidden"))


class FakeProtector:
    def __init__(self):
        self.plaintext = {CIPHERTEXT: KEY, NEXT_CIPHERTEXT: NEXT_KEY}
        self.unsealed = []

    def seal(self, key):
        assert key == NEXT_KEY
        return NEXT_CIPHERTEXT

    def unseal(self, ciphertext):
        self.unsealed.append(ciphertext)
        if ciphertext not in self.plaintext:
            raise RuntimeError(MARKER)
        return self.plaintext[ciphertext]


def sealed(key_id=ACTIVE, epoch=1, ciphertext=CIPHERTEXT):
    return ks.SealedKey(1, key_id, epoch, TIME, "HMAC-SHA256", base64.b64encode(ciphertext).decode("ascii"))


class FakeBoundary:
    def __init__(self):
        self.policy = ks.KeyStorePolicy(
            SID, ROOT, PureWindowsPath(r"C:\repo"), PureWindowsPath(r"C:\conversations"),
            PureWindowsPath(r"C:\sandbox"), (PureWindowsPath(r"C:\sync"),),
        )
        self.evidence = ks.EffectiveKeyStoreEvidence(ks.SERVICE_IDENTITY, SID, True, ROOT, True, True, True)
        raw = sealed().serialize()
        self.records = {ROOT / "keys" / f"{ACTIVE}.sealed": raw}
        anchor = ks.KeyAnchor(1, ks.SERVICE_IDENTITY, ACTIVE, 1, hashlib.sha256(raw).hexdigest(), TIME)
        self.trusted = ks.TrustedAnchorSnapshot(anchor.serialize(), 1)
        self.visible_anchor = self.trusted.raw_anchor
        self.stage_hook = None
        self.publish_hook = None
        self.protector = FakeProtector()

    def attest(self):
        return self.evidence

    def read(self, path, limit):
        assert path.parent == ROOT / "keys" and limit == ks.MAX_RECORD_BYTES
        return self.records[path]

    def read_anchor(self):
        if self.trusted is None:
            return None
        if self.trusted.raw_anchor != self.visible_anchor:
            raise RuntimeError(MARKER)
        return self.trusted

    def stage(self, path, raw):
        assert path not in self.records
        if self.stage_hook:
            self.stage_hook(path, raw)
        self.records[path] = raw

    def publish(self, previous, candidate):
        assert previous == self.trusted
        assert candidate.authoritative_epoch == previous.authoritative_epoch + 1
        if self.publish_hook:
            self.publish_hook(candidate)
        else:
            self.commit(candidate)

    def commit(self, candidate):
        self.trusted = candidate
        self.visible_anchor = candidate.raw_anchor

    def store(self, *, anchor=True, publisher=False):
        return ks.ProtectedProvenanceKeyStore(
            self.policy, self, self.protector, FakeAnchorProvider(self) if anchor else None,
            self if publisher else None,
        )


class FakeAnchorProvider:
    def __init__(self, boundary):
        self.boundary = boundary

    def read(self):
        return self.boundary.read_anchor()


def assert_code(exc, code):
    assert exc.value.code is code
    assert str(exc.value) == code.value
    assert MARKER not in str(exc.value)


def test_dpapi_adapter_uses_only_approved_flags_entropy_and_wipes_buffers(monkeypatch):
    outputs = []
    operations = []
    source_pointer = None

    def operation(expected_input, output):
        def call(source, description, entropy, reserved, prompt, flags, result):
            nonlocal source_pointer
            source_pointer = ctypes.cast(source, ctypes.POINTER(dpapi._DataBlob)).contents.data
            source_view = ctypes.cast(source, ctypes.POINTER(dpapi._DataBlob)).contents
            entropy_view = ctypes.cast(entropy, ctypes.POINTER(dpapi._DataBlob)).contents
            assert ctypes.string_at(source_view.data, source_view.size) == expected_input
            assert ctypes.string_at(entropy_view.data, entropy_view.size) == hashlib.sha256(
                b"sovereignai:provenance:v3:dpapi:entropy"
            ).digest()
            assert description is reserved is prompt is None
            assert flags == dpapi.CRYPTPROTECT_UI_FORBIDDEN
            buffer = (ctypes.c_ubyte * len(output)).from_buffer_copy(output)
            outputs.append(buffer)
            view = ctypes.cast(result, ctypes.POINTER(dpapi._DataBlob)).contents
            view.size, view.data = len(output), buffer
            operations.append(len(expected_input))
            return 1
        return call

    def free(pointer):
        assert ctypes.string_at(pointer, len(outputs[-1])) == bytes(len(outputs[-1]))
        assert ctypes.string_at(source_pointer, operations[-1]) == bytes(operations[-1])

    monkeypatch.setattr(dpapi, "_load_api", lambda: (
        operation(KEY, CIPHERTEXT), operation(CIPHERTEXT, KEY), free, None,
    ))
    adapter = dpapi.CurrentUserDPAPI()
    assert adapter.seal(KEY) == CIPHERTEXT
    assert adapter.unseal(CIPHERTEXT) == KEY
    assert operations == [len(KEY), len(CIPHERTEXT)]
    monkeypatch.setattr(dpapi, "_load_api", lambda: (
        None, operation(CIPHERTEXT, b"x"), free, None,
    ))
    with pytest.raises(dpapi.DPAPIError) as exc:
        adapter.unseal(CIPHERTEXT)
    assert_code(exc, dpapi.DPAPICode.OUTPUT)
    assert bytes(outputs[-1]) == b"\x00"


@pytest.mark.parametrize("flags", [0, 4, 5, True, 9])
def test_dpapi_rejects_nonapproved_flags(flags):
    with pytest.raises(dpapi.DPAPIError) as exc:
        dpapi.CurrentUserDPAPI(flags=flags)
    assert_code(exc, dpapi.DPAPICode.FLAGS)


@pytest.mark.parametrize("last_error,code", [(5, dpapi.DPAPICode.USER_CONTEXT), (13, dpapi.DPAPICode.REJECTED)])
def test_dpapi_reports_only_categorical_errors(monkeypatch, last_error, code):
    monkeypatch.setattr(dpapi, "_load_api", lambda: (None, lambda *args: 0, None, lambda: last_error))
    with pytest.raises(dpapi.DPAPIError) as exc:
        dpapi.CurrentUserDPAPI().unseal(CIPHERTEXT)
    assert_code(exc, code)


def test_dpapi_rejects_bad_input_before_platform_call():
    adapter = dpapi.CurrentUserDPAPI()
    for call, param in ((adapter.seal, b"short"), (adapter.unseal, b""),
                        (adapter.unseal, bytes(dpapi.MAX_SEALED_BLOB_BYTES + 1))):
        with pytest.raises(dpapi.DPAPIError) as exc:
            call(param)
        assert_code(exc, dpapi.DPAPICode.INPUT)


def test_dpapi_reports_unsupported_platform_without_loading_a_library(monkeypatch):
    with monkeypatch.context() as patch:
        patch.setattr(dpapi.sys, "platform", "linux")
        with pytest.raises(dpapi.DPAPIError) as exc:
            LOAD_DPAPI()
    assert_code(exc, dpapi.DPAPICode.UNSUPPORTED)


@pytest.mark.parametrize("field,value", [
    ("version", True), ("version", 2), ("key_id", "../escape"), ("epoch", 0), ("epoch", True),
    ("created_at", "2026-11-17T00:00:00+00:00"), ("algorithm", "SHA256"),
    ("ciphertext_b64", "!!!"), ("ciphertext_b64", "YR=="), ("ciphertext_b64", ""),
    ("ciphertext_b64", base64.b64encode(bytes(dpapi.MAX_SEALED_BLOB_BYTES + 1)).decode("ascii")),
])
def test_sealed_record_strict_schema(field, value):
    record = sealed()
    assert ks.SealedKey.parse(record.serialize()) == (record, CIPHERTEXT)
    with pytest.raises(ks.KeyStoreError) as exc:
        replace(record, **{field: value}).serialize()
    assert_code(exc, ks.KeyStoreCode.RECORD_INVALID)


@pytest.mark.parametrize("field,value", [
    ("anchor_version", False), ("anchor_version", 2), ("service_identity", "interactive"),
    ("active_key_id", "../escape"), ("active_epoch", -1), ("active_epoch", 2**63),
    ("sealed_blob_sha256", "F" * 64), ("updated_at", "2026-02-29T00:00:00Z"),
])
def test_anchor_strict_schema(field, value):
    anchor = ks.KeyAnchor.parse(FakeBoundary().visible_anchor)
    with pytest.raises(ks.KeyStoreError) as exc:
        replace(anchor, **{field: value}).serialize()
    assert_code(exc, ks.KeyStoreCode.ANCHOR_INVALID)


@pytest.mark.parametrize("parse,raw,limit,code", [
    (ks.KeyAnchor.parse, FakeBoundary().visible_anchor, ks.MAX_ANCHOR_BYTES, ks.KeyStoreCode.ANCHOR_INVALID),
    (ks.SealedKey.parse, sealed().serialize(), ks.MAX_RECORD_BYTES, ks.KeyStoreCode.RECORD_INVALID),
])
def test_records_reject_duplicate_unknown_noncanonical_and_oversized_bytes(parse, raw, limit, code):
    field = raw[1:].split(b",", 1)[0]
    for malformed in (b"{" + field + b"," + raw[1:], b'{"unknown":1,' + raw[1:],
                      raw + b"\n", bytearray(raw), b"x" * (limit + 1)):
        with pytest.raises(ks.KeyStoreError) as exc:
            parse(malformed)
        assert_code(exc, code)


@pytest.mark.parametrize("case,code", [
    ("provider-missing", ks.KeyStoreCode.BLOCKED),
    ("anchor-missing", ks.KeyStoreCode.ANCHOR_MISSING),
    ("anchor-substituted", ks.KeyStoreCode.ANCHOR_UNAVAILABLE),
    ("anchor-rollback", ks.KeyStoreCode.ANCHOR_EPOCH),
    ("retained-active", ks.KeyStoreCode.ANCHOR_INVALID),
    ("retained-future", ks.KeyStoreCode.ANCHOR_INVALID),
    ("record-missing", ks.KeyStoreCode.RECORD_UNAVAILABLE),
    ("record-substituted", ks.KeyStoreCode.DIGEST_MISMATCH),
    ("record-id-mismatch", ks.KeyStoreCode.RECORD_INVALID),
    ("record-epoch-mismatch", ks.KeyStoreCode.RECORD_INVALID),
])
def test_durable_gate_rejects_untrusted_anchors_and_files(case, code):
    boundary = FakeBoundary()
    anchor = ks.KeyAnchor.parse(boundary.visible_anchor)
    path = next(iter(boundary.records))
    if case == "anchor-missing":
        boundary.trusted = None
    elif case == "anchor-substituted":
        boundary.visible_anchor = replace(anchor, active_key_id=CANDIDATE).serialize()
    elif case == "anchor-rollback":
        boundary.trusted = replace(boundary.trusted, authoritative_epoch=2)
    elif case == "retained-active":
        boundary.trusted = replace(boundary.trusted, retained=(anchor.reference(),))
    elif case == "retained-future":
        ref = ks.KeyReference(CANDIDATE, 2, anchor.sealed_blob_sha256)
        boundary.trusted = replace(boundary.trusted, retained=(ref,))
    elif case == "record-missing":
        boundary.records.clear()
    elif case == "record-substituted":
        boundary.records[path] = sealed(ciphertext=NEXT_CIPHERTEXT).serialize()
    elif case in ("record-epoch-mismatch", "record-id-mismatch"):
        record = sealed(epoch=2) if case == "record-epoch-mismatch" else sealed(key_id=CANDIDATE)
        boundary.records[path] = raw = record.serialize()
        new_anchor = replace(anchor, sealed_blob_sha256=hashlib.sha256(raw).hexdigest()).serialize()
        boundary.commit(replace(boundary.trusted, raw_anchor=new_anchor))
    with pytest.raises(ks.KeyStoreError) as exc:
        ks.startup_provenance_keys("durable", boundary.store(anchor=case != "provider-missing"))
    assert_code(exc, code)
    assert boundary.protector.unsealed == []


@pytest.mark.parametrize("field,value,code", [
    ("token_sid", "S-1-5-21-1-2-3-1002", ks.KeyStoreCode.IDENTITY),
    ("service_identity", "interactive", ks.KeyStoreCode.IDENTITY),
    ("profile_loaded_non_roaming", False, ks.KeyStoreCode.PROFILE),
    ("profile_loaded_non_roaming", None, ks.KeyStoreCode.PROFILE),
    ("effective_ntfs_acl_verified", 1, ks.KeyStoreCode.STORAGE),
    ("no_broad_inherited_write", False, ks.KeyStoreCode.STORAGE),
    ("no_unsafe_aliases_or_reparse_points", False, ks.KeyStoreCode.STORAGE),
    ("root", PureWindowsPath(r"C:\other"), ks.KeyStoreCode.STORAGE),
])
def test_durable_gate_requires_effective_identity_profile_and_storage(field, value, code):
    boundary = FakeBoundary()
    boundary.evidence = replace(boundary.evidence, **{field: value})
    with pytest.raises(ks.KeyStoreError) as exc:
        boundary.store().load()
    assert_code(exc, code)
    assert boundary.protector.unsealed == []


@pytest.mark.parametrize("root", [
    r"C:\repo\keystore", r"C:\conversations", r"C:\sandbox\keystore",
    r"C:\sync\keystore", r"C:\OneDrive - Org\keystore", r"\\host\share\keys",
    r"C:\Sovereign\..\keystore",
])
def test_key_storage_rejects_prohibited_roots(root):
    boundary = FakeBoundary()
    path = PureWindowsPath(root)
    with pytest.raises(ks.KeyStoreError) as exc:
        replace(boundary.policy, root=path).require(replace(boundary.evidence, root=path))
    assert_code(exc, ks.KeyStoreCode.STORAGE)


def test_dedicated_nt_service_sid_is_supported():
    boundary = FakeBoundary()
    sid = "S-1-5-80-1-2-3-4-5"
    replace(boundary.policy, expected_sid=sid).require(replace(boundary.evidence, token_sid=sid))


@pytest.mark.parametrize("key,code", [(None, ks.KeyStoreCode.UNSEAL_FAILED), (b"short", ks.KeyStoreCode.KEY_INVALID)])
def test_unseal_failure_never_regenerates_a_key(key, code, monkeypatch, caplog):
    boundary = FakeBoundary()
    if key is None:
        boundary.protector.plaintext.clear()
    else:
        boundary.protector.plaintext[CIPHERTEXT] = key
    monkeypatch.setattr(ks.secrets, "token_bytes", lambda _: pytest.fail("Unexpected key generation"))
    with pytest.raises(ks.KeyStoreError) as exc:
        ks.startup_provenance_keys("durable", boundary.store())
    assert_code(exc, code)
    assert MARKER not in caplog.text


@pytest.mark.parametrize("mode", ["durable", "typo-" + MARKER], ids=["durable", "invalid"])
def test_fastapi_blocks_before_runtime_storage_or_ephemeral_signer(tmp_path, monkeypatch, caplog, mode):
    import backend.main as main

    def forbidden(*args, **kwargs):
        pytest.fail("Runtime services must remain untouched")

    path = tmp_path / "runtime"
    monkeypatch.setenv("PROVENANCE_KEY_MODE", mode)
    settings = config(path)
    assert settings.provenance_key_mode == mode
    monkeypatch.setattr(main, "ConversationMemory", forbidden)
    monkeypatch.setattr(main, "AuthStore", forbidden)
    monkeypatch.setattr(main, "ModelRouter", forbidden)
    app = main.create_app(settings)

    async def check():
        with pytest.raises(ks.KeyStoreError) as exc:
            async with main.lifespan(app):
                pytest.fail("Startup unexpectedly reached readiness")
        assert_code(exc, ks.KeyStoreCode.BLOCKED)

    immediate(check())
    assert not path.exists()
    assert not app.state.startup_complete
    assert ks.KeyStoreCode.BLOCKED.value in caplog.text
    assert MARKER not in caplog.text


def test_fastapi_wires_validated_mock_ring_into_memory(tmp_path, monkeypatch, owner):
    import backend.main as main

    boundary = FakeBoundary()
    monkeypatch.setenv("PROVENANCE_KEY_MODE", "durable")
    monkeypatch.setattr(main, "startup_provenance_keys", lambda mode: ks.startup_provenance_keys(mode, boundary.store()))
    monkeypatch.setattr(main, "initialize_admin_user_if_empty", lambda store: None)
    app = main.create_app(config(tmp_path))

    async def check():
        async with main.lifespan(app):
            assert app.state.startup_complete
            memory = app.state.engine._memory
            identity = memory.prepare_new_chat("s", "a")
            message = memory.record_plain_response("s", identity, "mocked startup", producer="fixture/model")
            assert message.extra_fields["access_provenance_v1"]["key_id"] == ACTIVE
            assert memory.verify_assistant_provenance("s", message)

    run_offline(check())
    assert not app.state.startup_complete


def test_rotation_disabled_without_protected_publisher(monkeypatch):
    boundary = FakeBoundary()
    monkeypatch.setattr(ks.secrets, "token_bytes", lambda _: pytest.fail("Unexpected key generation"))
    with pytest.raises(ks.KeyStoreError) as exc:
        boundary.store().rotate(CANDIDATE, TIME)
    assert_code(exc, ks.KeyStoreCode.BLOCKED)
    assert len(boundary.records) == 1


@pytest.mark.parametrize("point", ["generation", "seal", "round-trip"])
def test_rotation_failure_before_staging_preserves_anchor(monkeypatch, point):
    boundary = FakeBoundary()
    previous = boundary.trusted

    def fail(*args):
        raise RuntimeError(MARKER)

    monkeypatch.setattr(ks.secrets, "token_bytes", fail if point == "generation" else lambda _: NEXT_KEY)
    if point == "seal":
        monkeypatch.setattr(boundary.protector, "seal", fail)
    elif point == "round-trip":
        boundary.protector.plaintext[NEXT_CIPHERTEXT] = KEY
    with pytest.raises(ks.KeyStoreError) as exc:
        boundary.store(publisher=True).rotate(CANDIDATE, TIME)
    assert_code(exc, ks.KeyStoreCode.SEAL_FAILED)
    assert len(boundary.records) == 1
    assert boundary.trusted == previous


@pytest.mark.parametrize("point,code,committed", [
    ("before-stage", ks.KeyStoreCode.STAGE_FAILED, False),
    ("during-stage", ks.KeyStoreCode.STAGE_FAILED, False),
    ("before-publish", ks.KeyStoreCode.PUBLICATION_UNCERTAIN, False),
    ("after-commit", ks.KeyStoreCode.PUBLICATION_UNCERTAIN, True),
    ("unreadable-after-commit", ks.KeyStoreCode.SIGNER_UNCONFIRMED, True),
])
def test_rotation_crash_never_returns_unconfirmed_signer(point, code, committed, monkeypatch):
    boundary = FakeBoundary()
    monkeypatch.setattr(ks.secrets, "token_bytes", lambda _: NEXT_KEY)
    candidate_file = ROOT / "keys" / f"{CANDIDATE}.sealed"

    def stage(path, raw):
        if point == "during-stage":
            boundary.records[path] = raw[:5]
        if point in ("before-stage", "during-stage"):
            raise RuntimeError(MARKER)

    def publish(candidate):
        assert boundary.records[candidate_file] == sealed(CANDIDATE, 2, NEXT_CIPHERTEXT).serialize()
        if committed:
            boundary.commit(candidate)
        if point == "unreadable-after-commit":
            boundary.visible_anchor = b"unavailable"
            return
        raise RuntimeError(MARKER)

    boundary.stage_hook, boundary.publish_hook = stage, publish
    with pytest.raises(ks.KeyStoreError) as exc:
        boundary.store(publisher=True).rotate(CANDIDATE, TIME, retain=(ACTIVE,))
    assert_code(exc, code)
    if point == "unreadable-after-commit":
        with pytest.raises(ks.KeyStoreError) as recovery:
            boundary.store().load()
        assert_code(recovery, ks.KeyStoreCode.ANCHOR_UNAVAILABLE)
        boundary.visible_anchor = boundary.trusted.raw_anchor
    ring = boundary.store().load()
    assert ring.active_id == (CANDIDATE if committed else ACTIVE)
    assert ring.verification_key(ACTIVE) == KEY


def test_mocked_restart_rotation_and_v3_history(tmp_path, owner, monkeypatch):
    boundary = FakeBoundary()
    generated = []

    def generate(length):
        assert length == 32
        generated.append(length)
        return NEXT_KEY

    monkeypatch.setattr(ks.secrets, "token_bytes", generate)
    store = boundary.store(publisher=True)
    path = tmp_path / "memory.json"
    registry = ConversationRegistry(tmp_path / "registry.db")

    def reopen(ring):
        return ConversationMemory(path, security_registry=registry, provenance_keys=ring)

    first = reopen(ks.startup_provenance_keys("durable", store))
    identity = first.prepare_new_chat("s", "a")
    original = first.record_plain_response("s", identity, "authenticated", producer="fixture/model")
    legacy = first.add_assistant_message("s", "legacy")
    untouched = path.read_bytes()
    restored = reopen(ks.startup_provenance_keys("durable", boundary.store()))
    assert restored.verify_assistant_provenance("s", original)
    assert not restored.verify_assistant_provenance("s", legacy)
    assert path.read_bytes() == untouched
    assert first._provenance_key == (ACTIVE, KEY)
    assert generated == []

    original_evidence = boundary.evidence
    boundary.evidence = replace(original_evidence, token_sid="S-1-5-21-1-2-3-1002")
    with pytest.raises(ks.KeyStoreError) as wrong_identity:
        ks.startup_provenance_keys("durable", boundary.store())
    assert_code(wrong_identity, ks.KeyStoreCode.IDENTITY)
    boundary.evidence = original_evidence

    rotated_ring = store.rotate(CANDIDATE, TIME, retain=(ACTIVE,))
    assert generated == [32]
    assert rotated_ring.signing_key() == (CANDIDATE, NEXT_KEY)
    assert rotated_ring.verification_key("c" * 32) is None
    assert boundary.trusted.authoritative_epoch == 2
    assert path.read_bytes() == untouched
    rotated = reopen(boundary.store().load())
    new = rotated.record_plain_response("s", identity, "new", producer="calculator", calculation=True)
    assert new.extra_fields["access_provenance_v1"]["key_id"] == CANDIDATE
    assert rotated.verify_assistant_provenance("s", new)
    assert rotated.verify_assistant_provenance("s", original)
    assert MARKER.encode() not in path.read_bytes() and MARKER not in repr(rotated_ring)

    rotated.prepare_new_chat("other", "a")
    data = json.loads(path.read_bytes())
    data["other"]["messages"] = data["s"]["messages"]
    path.write_text(json.dumps(data), encoding="utf-8")
    replay = rotated.get_history("other")[0]
    assert replay == original
    assert not rotated.verify_assistant_provenance("other", replay)

    engine, _ = setup_engine(tmp_path / "api")
    engine._memory = rotated
    app = make_app(engine)

    async def check_history():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            response = await client.get("/api/chat/sessions/s", headers={"Authorization": "Bearer a"})
            assert response.status_code == 200
            assert [message["withheld"] for message in response.json()["messages"]] == [False, True, False]
            assert "key_id" not in response.text and "legacy" not in response.text

    immediate(check_history())
    retired = reopen(ks.ProvenanceKeyRing(CANDIDATE, {CANDIDATE: NEXT_KEY}))
    assert not retired.verify_assistant_provenance("s", original)
    assert retired.verify_assistant_provenance("s", new)
