"""Protected Windows file, token and authority observations are entirely synthetic."""
import ctypes
import hashlib
import struct
from contextlib import contextmanager
from dataclasses import replace
from pathlib import PureWindowsPath

import pytest

from backend.security import provenance_dpapi, provenance_keystore as ks
from backend.security import provenance_windows as windows
from backend.security import provenance_windows_native as native
from test_a21_conversation_offline import immediate
from test_r13_lifecycle_readiness import config
from test_r814_dpapi_keystore_offline import (
    ACTIVE, CANDIDATE, CIPHERTEXT, KEY, MARKER, NEXT_CIPHERTEXT, NEXT_KEY, SID, TIME,
    FakeProtector, sealed,
)

ROOT = PureWindowsPath(r"C:\Sovereign\keystore")
KEYS = ROOT / "keys"
POLICY = ks.KeyStorePolicy(SID, ROOT, PureWindowsPath(r"C:\repo"), PureWindowsPath(r"C:\conversations"),
                           PureWindowsPath(r"C:\sandbox"), (PureWindowsPath(r"C:\cloud"),))
ACTIVE_PATH = POLICY.record_path(ACTIVE)
CANDIDATE_PATH = POLICY.record_path(CANDIDATE)


def sid_bytes(sid):
    _, revision, authority, *parts = sid.split("-")
    return (bytes((int(revision), len(parts))) + int(authority).to_bytes(6, "big")
            + b"".join(int(part).to_bytes(4, "little") for part in parts))


def acl_for(*entries):
    aces = []
    for sid, flags, rights in entries:
        encoded = sid_bytes(sid)
        aces.append(struct.pack("<BBHI", 0, flags, 8 + len(encoded), rights) + encoded)
    return struct.pack("<BBHHH", 2, 0, 8 + sum(map(len, aces)), len(aces), 0) + b"".join(aces)


def private_acl(*, sid=SID, directory=False):
    flags = 3 if directory else 0
    return native.FileSecurity(native.SYSTEM_SID, 0x1004, acl_for(
        (native.SYSTEM_SID, flags, native.FILE_ALL_ACCESS), (sid, flags, native.FILE_ALL_ACCESS),
    ))


def snapshot(record=None, *, epoch=1, retained=()):
    record = sealed() if record is None else record
    digest = hashlib.sha256(record.serialize()).hexdigest()
    anchor = ks.KeyAnchor(1, ks.SERVICE_IDENTITY, record.key_id, record.epoch, digest, TIME)
    return ks.TrustedAnchorSnapshot(anchor.serialize(), epoch, retained)


class FakeWindows:
    def __init__(self, policy=POLICY):
        self.policy = policy
        self.token = native.TokenFacts(policy.expected_sid, True, policy.expected_sid, 5)
        self.account = policy.expected_sid
        self.profile = 0
        self.impersonating = False
        self.handles = {}
        self.close_failure = None
        self.facts = {}
        self.permissions = {}
        self.cloud = set()
        self.bad_drives = set()
        self.volume_ids = {}
        self.paths = {path for location in (
            policy.root / "keys", policy.repository, policy.conversation_storage, policy.sandbox_storage,
            *policy.cloud_sync_roots,
        ) for path in (location, *location.parents)}
        self.records = {policy.record_path(ACTIVE): sealed().serialize()}
        self.events = []
        self.fail_flush = False
        self.read_override = None
        self._counter = 0

    def _open(self, target):
        self._counter += 1
        self.handles[self._counter] = target
        self.events.append(("open", target))
        return self._counter

    def open_thread_token(self):
        return self._open("thread") if self.impersonating else None

    def open_process_token(self):
        return self._open("process")

    def token_facts(self, handle):
        assert self.handles[handle] == "process"
        return self.token

    def service_sid(self, virtual):
        assert virtual is self.policy.expected_sid.startswith("S-1-5-80-")
        return self.account

    def profile_flags(self):
        return self.profile

    def close(self, handle):
        target = self.handles.pop(handle)
        self.events.append(("close", target))
        if target == self.close_failure:
            raise OSError(MARKER)

    def open_drive(self, path):
        return self._open(path)

    def volume_root(self, handle):
        drive = self.handles[handle]
        if drive in self.bad_drives:
            raise OSError(MARKER)
        return self.volume_ids.get(drive, drive.drive)

    def open_child(self, parent, name, directory, *, inspect_acl):
        path = self.handles[parent] / name
        assert len(PureWindowsPath(name).parts) == 1
        if directory and path not in self.paths or not directory and path not in self.records:
            raise FileNotFoundError(MARKER)
        if path in (self.policy.root, self.policy.root / "keys") or not directory:
            assert inspect_acl
        return self._open(path)

    def file_facts(self, handle):
        path = self.handles[handle]
        facts = native.FileFacts(path, 101, native.DIRECTORY if path in self.paths else 0,
                                 1, len(self.records.get(path, b"")))
        return replace(facts, **self.facts.get(path, {}))

    def outside_cloud_root(self, handle):
        return self.handles[handle] not in self.cloud

    def security(self, handle):
        path = self.handles[handle]
        return self.permissions.get(path, private_acl(sid=self.policy.expected_sid, directory=path in self.paths))

    def read(self, handle, count):
        path = self.handles[handle]
        assert path.parent in self.handles.values()
        self.events.append(("read", path, count))
        if self.read_override is not None:
            return self.read_override(path)
        return self.records[path][:count]

    @contextmanager
    def create_record(self, parent, name, service_sid):
        path = self.handles[parent] / name
        assert path.parent == self.policy.root / "keys" and service_sid == self.policy.expected_sid
        if path in self.records:
            raise FileExistsError(MARKER)
        self.events.append(("create", path))
        self.records[path] = b""
        handle = self._open(path)
        try:
            yield handle
        finally:
            self.close(handle)

    def write(self, handle, raw):
        path = self.handles[handle]
        self.events.append(("write", path))
        self.records[path] = raw

    def flush(self, handle):
        self.events.append(("flush", self.handles[handle]))
        if self.fail_flush:
            raise OSError(MARKER)


class FakeAuthority:
    def __init__(self):
        self.state = snapshot()
        self.busy = False
        self.locked = False
        self.writer = None
        self.swaps = 0
        self.fail_release = False

    def read(self):
        return self.state

    @contextmanager
    def rotation_window(self):
        if self.busy or self.locked:
            yield None
            return
        self.locked = True
        try:
            yield self
        finally:
            self.locked = False
            if self.fail_release:
                raise OSError(MARKER)

    def compare_and_swap(self, previous, candidate):
        assert self.locked
        self.swaps += 1
        if self.writer:
            return self.writer(previous, candidate)
        assert previous == self.state
        self.state = candidate
        return True


@pytest.fixture(autouse=True)
def no_windows_calls(monkeypatch):
    def deny(*args):
        pytest.fail("Windows or DPAPI call attempted in offline tests")

    monkeypatch.setattr(native.WindowsKeyStoreNative, "_function", deny)
    monkeypatch.setattr(provenance_dpapi, "_load_api", deny)


@pytest.fixture
def providers(monkeypatch):
    monkeypatch.setattr(ks.secrets, "token_bytes", lambda length: NEXT_KEY)
    return FakeWindows(), FakeAuthority(), FakeProtector()


def rejected(code, operation):
    with pytest.raises(ks.KeyStoreError) as exc:
        operation()
    assert exc.value.code is code
    assert str(exc.value) == code.value
    assert MARKER not in str(exc.value)


@pytest.mark.parametrize("sid", [SID, "S-1-5-80-1-2-3-4-5"])
def test_service_logon_identity_matches_pinned_and_resolved_sid(sid):
    api = FakeWindows(replace(POLICY, expected_sid=sid))
    assert windows.WindowsServiceIdentity(sid, api).require() == sid
    assert api.events == [("open", "process"), ("close", "process")]


@pytest.mark.parametrize("case", [
    "wrong-sid", "wrong-local-account", "interactive-logon", "foreign-session", "impersonation",
    "no-process-token", "no-token-evidence", "invalid-token-evidence", "malformed-config", "close-failure",
])
def test_identity_rejects_and_releases_token_handles(case, monkeypatch):
    api = FakeWindows()
    sid = SID
    other = SID[:-1] + "2"
    if case == "wrong-sid":
        api.token = replace(api.token, sid=other)
    elif case == "wrong-local-account":
        api.account = other
    elif case == "interactive-logon":
        api.token = replace(api.token, logon_type=2)
    elif case == "foreign-session":
        api.token = replace(api.token, logon_sid=other)
    elif case == "impersonation":
        api.impersonating = True
    elif case == "no-process-token":
        monkeypatch.setattr(api, "open_process_token", lambda: None)
    elif case == "no-token-evidence":
        api.token = None
    elif case == "invalid-token-evidence":
        api.token = replace(api.token, primary=1)
    elif case == "malformed-config":
        sid = "S-1-5-21-1-02-3-1001"
    else:
        api.close_failure = "process"
    rejected(ks.KeyStoreCode.IDENTITY, windows.WindowsServiceIdentity(sid, api).require)
    assert not api.handles
    if case == "impersonation":
        assert api.events == [("open", "thread"), ("close", "thread")]


@pytest.mark.parametrize("flags", [None, False, 1, 2, 4, 8])
def test_profile_missing_roaming_temporary_mandatory_or_unknown_fails(flags):
    api = FakeWindows()
    api.profile = flags
    rejected(ks.KeyStoreCode.PROFILE, windows.WindowsServiceIdentity(SID, api).require)
    assert not api.handles


@pytest.mark.parametrize("case", [
    "broad", "inherited", "null", "unprotected", "wrong-owner", "incomplete-rights",
    "unknown-ace", "truncated", "duplicate",
])
def test_acl_rejects_broad_or_ambiguous_effective_permissions(case):
    api = FakeWindows()
    acl = private_acl(directory=True)
    full = native.FILE_ALL_ACCESS
    if case == "broad":
        acl = replace(acl, dacl=acl_for((SID, 0, full), ("S-1-5-11", 0, full)))
    elif case == "inherited":
        acl = replace(acl, dacl=acl_for((SID, 0x10, full), (native.SYSTEM_SID, 0, full)))
    elif case == "null":
        acl = replace(acl, dacl=None)
    elif case == "unprotected":
        acl = replace(acl, control=4)
    elif case == "wrong-owner":
        acl = replace(acl, owner="S-1-5-32-544")
    elif case == "incomplete-rights":
        acl = replace(acl, dacl=acl_for((SID, 0, full), (native.SYSTEM_SID, 0, 1)))
    elif case == "unknown-ace":
        acl = replace(acl, dacl=acl.dacl[:8] + b"\x05" + acl.dacl[9:])
    elif case == "truncated":
        acl = replace(acl, dacl=acl.dacl[:-1])
    else:
        acl = replace(acl, dacl=acl_for((SID, 0, full), (SID, 0, full)))
    api.permissions[KEYS] = acl
    rejected(ks.KeyStoreCode.STORAGE, windows.WindowsProtectedKeyFiles(POLICY, api).attest)
    assert not api.handles


@pytest.mark.parametrize("case", [
    "reparse-parent", "cloud-registered", "cloud-placeholder", "onedrive", "cloud-exclusion",
    "missing-root", "missing-keys", "bad-filesystem", "wrong-volume", "unverified-exclusion",
    "cross-drive-alias", "close-directory",
])
def test_vault_rejects_unverified_filesystem_layout(case):
    policy = POLICY
    if case == "onedrive":
        policy = replace(policy, root=PureWindowsPath(r"C:\OneDrive - Org\vault"))
    elif case == "cloud-exclusion":
        policy = replace(policy, root=POLICY.cloud_sync_roots[0] / "vault")
    elif case == "cross-drive-alias":
        policy = replace(policy, repository=PureWindowsPath(r"X:\Sovereign"))
    api = FakeWindows(policy)
    if case == "reparse-parent":
        api.facts[ROOT.parent] = {"attributes": native.DIRECTORY | 0x400}
    elif case == "cloud-registered":
        api.cloud.add(ROOT)
    elif case == "cloud-placeholder":
        api.facts[ROOT] = {"attributes": native.DIRECTORY | 0x400000}
    elif case == "missing-root":
        api.paths.remove(ROOT)
    elif case == "missing-keys":
        api.paths.remove(KEYS)
    elif case == "bad-filesystem":
        api.bad_drives.add(PureWindowsPath("C:/"))
    elif case == "wrong-volume":
        api.facts[KEYS] = {"volume": 202}
    elif case == "unverified-exclusion":
        api.facts[policy.repository] = {"path": ROOT}
    elif case == "cross-drive-alias":
        api.volume_ids = {PureWindowsPath("C:/"): "same", PureWindowsPath("X:/"): "same"}
    elif case == "close-directory":
        api.close_failure = ROOT
    rejected(ks.KeyStoreCode.STORAGE, windows.WindowsProtectedKeyFiles(policy, api).attest)
    assert not api.handles


def test_attestation_and_bounded_same_handle_read():
    api = FakeWindows()
    files = windows.WindowsProtectedKeyFiles(POLICY, api)
    POLICY.require(files.attest())
    raw = sealed().serialize()
    assert files.read(ACTIVE_PATH, len(raw)) == raw
    assert ("read", ACTIVE_PATH, len(raw)) in api.events
    assert not api.handles


@pytest.mark.parametrize("path", [
    KEYS / ".." / (ACTIVE + ".sealed"), KEYS / ("z" * 32 + ".sealed"),
    KEYS / (ACTIVE.upper() + ".sealed"), KEYS / (ACTIVE + ".sealed:stream"),
    KEYS / (ACTIVE + ".Sealed"), PureWindowsPath(r"\\host\share") / (ACTIVE + ".sealed"),
    PureWindowsPath(ACTIVE + ".sealed"),
])
def test_file_paths_rejected_before_open(path):
    api = FakeWindows()
    files = windows.WindowsProtectedKeyFiles(POLICY, api)
    rejected(ks.KeyStoreCode.RECORD_INVALID, lambda: files.read(path, ks.MAX_RECORD_BYTES))
    assert not api.events


@pytest.mark.parametrize("case,code", [
    ("oversized", ks.KeyStoreCode.RECORD_INVALID), ("hard-link", ks.KeyStoreCode.RECORD_INVALID),
    ("redirected", ks.KeyStoreCode.STORAGE), ("reparse", ks.KeyStoreCode.STORAGE),
    ("acl", ks.KeyStoreCode.STORAGE), ("short-read", ks.KeyStoreCode.RECORD_INVALID),
    ("changed-size", ks.KeyStoreCode.RECORD_INVALID), ("close-failure", ks.KeyStoreCode.RECORD_UNAVAILABLE),
])
def test_invalid_file_read_rejects_bytes_and_closes_handles(case, code):
    api = FakeWindows()
    if case == "oversized":
        api.facts[ACTIVE_PATH] = {"size": ks.MAX_RECORD_BYTES + 1}
    elif case == "hard-link":
        api.facts[ACTIVE_PATH] = {"links": 2}
    elif case == "redirected":
        api.facts[ACTIVE_PATH] = {"path": ROOT.parent / ACTIVE_PATH.name}
    elif case == "reparse":
        api.facts[ACTIVE_PATH] = {"attributes": 0x400}
    elif case == "acl":
        api.permissions[ACTIVE_PATH] = replace(private_acl(), dacl=None)
    elif case == "short-read":
        api.read_override = lambda _: b"s"
    elif case == "changed-size":
        def change(path):
            api.facts[path] = {"size": 1}
            return api.records[path]
        api.read_override = change
    elif case == "close-failure":
        api.close_failure = ACTIVE_PATH
    files = windows.WindowsProtectedKeyFiles(POLICY, api)
    rejected(code, lambda: files.read(ACTIVE_PATH, ks.MAX_RECORD_BYTES))
    assert not api.handles
    if case not in ("short-read", "changed-size", "close-failure"):
        assert not any(event[0] == "read" for event in api.events)


def test_ntcreatefile_uses_single_component_relative_no_follow_and_create_new(monkeypatch):
    api = native.WindowsKeyStoreNative()
    calls = []

    def nt_create(out, access, attrs, iosb, allocation, flags, share, disposition, options, ea, ea_size):
        args = ctypes.cast(attrs, ctypes.POINTER(native._ObjectAttributes)).contents
        assert args.root == 41 and args.attributes == 0x40
        assert args.name.contents.buffer == ACTIVE_PATH.name
        assert args.name.contents.length == len(ACTIVE_PATH.name.encode("utf-16-le"))
        assert share == 1 and options & 0x00200000 and options & 0x20 and not options & 1
        calls.append((disposition, options & 0x42, args.security_descriptor))
        ctypes.cast(out, ctypes.POINTER(ctypes.c_void_p)).contents.value = 70
        return 0

    def get_function(library, name, *args):
        assert (library, name) == ("ntdll", "NtCreateFile")
        return nt_create

    monkeypatch.setattr(api, "_function", get_function)
    assert api.open_child(41, ACTIVE_PATH.name, True, inspect_acl=True) == 70
    assert api.open_child(41, ACTIVE_PATH.name, False, inspect_acl=True) == 70
    assert api._open_relative(41, ACTIVE_PATH.name, 1, descriptor=42) == 70
    assert calls == [(1, 0, None), (1, 0x40, None), (2, 0x42, 42)]
    with pytest.raises(OSError):
        api.open_child(41, r"..\escape", False, inspect_acl=True)


@pytest.mark.parametrize("status,is_outside", [(0x80070178, True), (0, False), (0x80070005, False)])
def test_cloud_probe_requires_positive_absence_evidence(status, is_outside, monkeypatch):
    api = native.WindowsKeyStoreNative()

    def get_function(library, name, *args):
        assert (library, name) == ("cldapi", "CfGetSyncRootInfoByHandle")
        return lambda *args: ctypes.c_int32(status).value

    monkeypatch.setattr(api, "_function", get_function)
    assert api.outside_cloud_root(41) is is_outside


def test_native_staging_releases_descriptor_even_if_file_close_fails(monkeypatch):
    api = native.WindowsKeyStoreNative()
    closed = []

    def convert(sddl, revision, descriptor, size):
        assert sddl == f"D:P(A;;FA;;;SY)(A;;FA;;;{SID})" and revision == 1
        ctypes.cast(descriptor, ctypes.POINTER(ctypes.c_void_p)).contents.value = 50
        return 1

    def close(handle):
        closed.append(handle)
        return 0

    def free(descriptor):
        closed.append(descriptor.value)
        return None

    functions = {
        "ConvertStringSecurityDescriptorToSecurityDescriptorW": convert, "CloseHandle": close, "LocalFree": free,
    }
    monkeypatch.setattr(api, "_function", lambda library, name, *args: functions[name])
    monkeypatch.setattr(api, "_open_relative", lambda *args, **kwargs: 60)
    with pytest.raises(OSError):
        with api.create_record(41, CANDIDATE_PATH.name, SID) as handle:
            assert handle == 60
    assert closed == [60, 50]


def test_staging_validates_canonical_bytes_and_never_overwrites():
    api = FakeWindows()
    files = windows.WindowsProtectedKeyFiles(POLICY, api)
    candidate = sealed(CANDIDATE, 2, NEXT_CIPHERTEXT).serialize()
    for bad in (candidate + b"\n", sealed().serialize()):
        rejected(ks.KeyStoreCode.RECORD_INVALID, lambda: files.stage_candidate(CANDIDATE_PATH, bad))
    files.stage_candidate(CANDIDATE_PATH, candidate)
    rejected(ks.KeyStoreCode.STAGE_FAILED, lambda: files.stage_candidate(CANDIDATE_PATH, candidate))
    assert [event for event in api.events if event[0] in ("create", "write", "flush")] == [
        ("create", CANDIDATE_PATH), ("write", CANDIDATE_PATH), ("flush", CANDIDATE_PATH),
    ]
    assert files.read(CANDIDATE_PATH, len(candidate)) == candidate
    assert not api.handles


def rotate(providers):
    api, authority, protector = providers
    return windows.rotate_windows_provenance_keys(
        POLICY, CANDIDATE, TIME, retain=(ACTIVE,), authority=authority, api=api, protector=protector,
    )


def restart(providers):
    api, authority, protector = providers
    store = windows.windows_provenance_store(POLICY, anchor=authority, protector=protector, api=api)
    return ks.startup_provenance_keys("durable", store)


def test_rotation_stages_flushes_and_reads_before_cas_and_retains_older_key(providers):
    api, authority, _ = providers

    def commit(previous, candidate):
        assert previous == authority.state
        assert api.events.index(("flush", CANDIDATE_PATH)) < next(
            i for i, event in enumerate(api.events) if event[:2] == ("read", CANDIDATE_PATH)
        )
        assert not api.handles
        authority.state = candidate
        return True

    authority.writer = commit
    assert rotate(providers).signing_key() == (CANDIDATE, NEXT_KEY)
    assert not authority.locked and authority.swaps == 1
    ring = restart(providers)
    assert ring.signing_key() == (CANDIDATE, NEXT_KEY)
    assert ring.verification_key(ACTIVE) == KEY


@pytest.mark.parametrize("case,code,committed", [
    ("busy", ks.KeyStoreCode.ROTATION_CONFLICT, False),
    ("cas-conflict", ks.KeyStoreCode.ROTATION_CONFLICT, False),
    ("stale", ks.KeyStoreCode.ANCHOR_EPOCH, False),
    ("prepublication", ks.KeyStoreCode.STAGE_FAILED, False),
    ("bad-readback", ks.KeyStoreCode.STAGE_FAILED, False),
    ("unknown-outcome", ks.KeyStoreCode.PUBLICATION_UNCERTAIN, False),
    ("committed-no-ack", ks.KeyStoreCode.PUBLICATION_UNCERTAIN, True),
    ("unconfirmed", ks.KeyStoreCode.SIGNER_UNCONFIRMED, False),
    ("failed-release", ks.KeyStoreCode.PUBLICATION_UNCERTAIN, True),
])
def test_rotation_failures_recover_only_from_authoritative_snapshot(case, code, committed, providers):
    api, authority, _ = providers
    old = authority.state
    if case == "busy":
        authority.busy = True
    elif case == "stale":
        def read():
            return replace(old, authoritative_epoch=2) if ("flush", CANDIDATE_PATH) in api.events else old
        authority.read = read
    elif case == "prepublication":
        api.fail_flush = True
    elif case == "bad-readback":
        api.read_override = lambda path: b"corrupt" if path == CANDIDATE_PATH else api.records[path]
    elif case == "failed-release":
        authority.fail_release = True
    else:
        def attempt(previous, candidate):
            if case in ("unknown-outcome", "committed-no-ack"):
                if committed:
                    authority.state = candidate
                raise OSError(MARKER)
            return case == "unconfirmed"
        authority.writer = attempt
    rejected(code, lambda: rotate(providers))
    assert not authority.locked and not api.handles
    if case in ("busy", "stale", "prepublication", "bad-readback"):
        assert authority.swaps == 0
    if case == "busy":
        assert api.events == []
    if case == "stale":
        authority.read = lambda: authority.state
    reopened = restart(providers)
    assert reopened.active_id == (CANDIDATE if committed else ACTIVE)
    assert reopened.verification_key(ACTIVE) == KEY


def test_missing_anchor_or_missing_protector_blocks_without_access(providers):
    api, authority, protector = providers
    rejected(ks.KeyStoreCode.BLOCKED, lambda: windows.windows_provenance_store(POLICY, protector=protector, api=api))
    rejected(ks.KeyStoreCode.BLOCKED, lambda: windows.windows_provenance_store(POLICY, anchor=authority, api=api))
    rejected(ks.KeyStoreCode.BLOCKED, lambda: windows.rotate_windows_provenance_keys(
        POLICY, CANDIDATE, TIME, protector=protector, api=api,
    ))
    assert not api.events


def test_native_composition_and_read_only_anchor_cannot_start_rotation(providers):
    api, authority, protector = providers
    native_api = native.WindowsKeyStoreNative()
    for missing_api in (None, native_api):
        rejected(ks.KeyStoreCode.BLOCKED, lambda: windows.windows_provenance_store(
            POLICY, anchor=authority, protector=protector, api=missing_api,
        ))
        rejected(ks.KeyStoreCode.BLOCKED, lambda: windows.rotate_windows_provenance_keys(
            POLICY, CANDIDATE, TIME, authority=authority, protector=protector, api=missing_api,
        ))

    class ReadOnlyAnchor:
        def read(self):
            return authority.read()

    rejected(ks.KeyStoreCode.BLOCKED, lambda: windows.rotate_windows_provenance_keys(
        POLICY, CANDIDATE, TIME, authority=ReadOnlyAnchor(), protector=protector, api=api,
    ))
    assert not api.events


@pytest.mark.parametrize("case,code", [
    ("missing-anchor", ks.KeyStoreCode.ANCHOR_MISSING),
    ("rollback", ks.KeyStoreCode.ANCHOR_EPOCH),
    ("wrong-identity", ks.KeyStoreCode.IDENTITY),
    ("invalid-acl", ks.KeyStoreCode.STORAGE),
    ("missing-key", ks.KeyStoreCode.RECORD_UNAVAILABLE),
    ("corrupt-key", ks.KeyStoreCode.DIGEST_MISMATCH),
])
def test_startup_never_unseals_on_untrusted_infrastructure(case, code, providers):
    api, authority, protector = providers
    if case == "missing-anchor":
        authority.state = None
    elif case == "rollback":
        authority.state = replace(authority.state, authoritative_epoch=2)
    elif case == "wrong-identity":
        api.token = replace(api.token, sid=SID[:-1] + "2")
    elif case == "invalid-acl":
        api.permissions[KEYS] = replace(private_acl(), dacl=None)
    elif case == "missing-key":
        del api.records[ACTIVE_PATH]
    elif case == "corrupt-key":
        api.records[ACTIVE_PATH] = b"corrupted-record-" + MARKER.encode()
    rejected(code, lambda: restart(providers))
    assert not protector.unsealed and not api.handles


def test_identity_change_after_file_read_blocks_dpapi(providers, monkeypatch):
    api, _, protector = providers
    facts = api.token_facts

    def changed(handle):
        if any(event[0] == "read" for event in api.events):
            return replace(facts(handle), logon_type=2)
        return facts(handle)

    monkeypatch.setattr(api, "token_facts", changed)
    rejected(ks.KeyStoreCode.IDENTITY, lambda: restart(providers))
    assert not protector.unsealed and not api.handles


@pytest.mark.parametrize("injected", [False, True])
def test_fastapi_remains_unready_on_protected_startup_failure(tmp_path, monkeypatch, caplog, providers, injected):
    from backend import main

    def forbidden(*args, **kwargs):
        pytest.fail("Partial initialization or fallback signer")

    runtime = tmp_path / "runtime"
    settings = config(runtime)
    settings.provenance_key_mode = "durable"
    monkeypatch.setattr(main, "ConversationMemory", forbidden)
    monkeypatch.setattr(main, "AuthStore", forbidden)
    code = ks.KeyStoreCode.BLOCKED
    if injected:
        api, authority, protector = providers
        api.records[ACTIVE_PATH] = MARKER.encode()
        store = windows.windows_provenance_store(POLICY, anchor=authority, protector=protector, api=api)
        monkeypatch.setattr(main, "startup_provenance_keys", lambda mode: ks.startup_provenance_keys(mode, store))
        code = ks.KeyStoreCode.DIGEST_MISMATCH
    app = main.create_app(settings)

    async def try_startup():
        async with main.lifespan(app):
            pytest.fail("Unexpected startup")

    rejected(code, lambda: immediate(try_startup()))
    assert not app.state.startup_complete and not runtime.exists()
    assert MARKER not in caplog.text and code.value in caplog.text
