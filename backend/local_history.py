"""Per-user retention preference and versioned SQLite storage for local text chat."""

import json
import os
import secrets
import sqlite3
import stat
import tempfile
from contextlib import closing, contextmanager, nullcontext
from pathlib import Path
from uuid import uuid4

from backend.agent.conversation_registry import ConversationRegistry
from backend.agent.sqlite_transaction import immediate_transaction
from backend.auth.models import AuthStore
from backend.config import PROJECT_ROOT
from backend.security.provenance_keystore import ProvenanceKeyRing

_APPLICATION_ID = 0x534C4831
_SCHEMA_VERSION = 1
_DATABASE = "history.sqlite3"
_PREFERENCE = "retention.json"
_LOCK = "history.lock"
_MODES = {"disposable", "durable"}
_TABLES = {"users", "sessions", "failed_logins", "conversation_security_registry",
           "local_history_identity", "local_history_transcripts"}


def history_location():
    return Path.home().resolve() / ".sovereignai-local-history"


def _safe_path(path, *, directory=False):
    try:
        info = path.lstat()
    except FileNotFoundError:
        return False
    kind = stat.S_ISDIR if directory else stat.S_ISREG
    if (not kind(info.st_mode)
            or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
            or (not directory and info.st_nlink != 1)):
        raise ValueError("Local history storage has an unsafe file or directory.")
    if os.name != "nt" and (info.st_uid != os.getuid() or info.st_mode & 0o077):
        raise ValueError("Local history storage must be accessible only to the current user.")
    return True


def _strict_json(data):
    def unique(pairs):
        result = dict(pairs)
        if len(result) != len(pairs):
            raise ValueError("Duplicate local history fields.")
        return result

    return json.loads(data, object_pairs_hook=unique)


class LocalRetention:
    def __init__(self, settings):
        self.override = settings.local_history_mode
        self.root = history_location()
        if self.override == "disposable":
            self.mode = "disposable"
            return
        protected = [PROJECT_ROOT, settings.tasks_dir, settings.tasks_db_path, settings.conversation_memory_path,
                     settings.upload_dir, settings.sandbox_dir, settings.log_dir, settings.chroma_persist_dir]
        if (self.root != self.root.resolve() or
                any(self.root.is_relative_to(path.resolve()) or path.resolve().is_relative_to(self.root)
                    for path in protected if path is not None)):
            raise ValueError("Local history must have a dedicated location outside application and production storage.")
        self.mode = self.override or self.preference()

    def ensure_root(self):
        self.root.mkdir(mode=0o700, exist_ok=True)
        _safe_path(self.root, directory=True)

    def preference(self):
        if not _safe_path(self.root, directory=True):
            return "disposable"
        path = self.root / _PREFERENCE
        if not _safe_path(path):
            return "disposable"
        with path.open("rb") as source:
            encoded = source.read(1025)
        if len(encoded) > 1024:
            raise ValueError("Invalid local history preference.")
        data = _strict_json(encoded)
        if (not isinstance(data, dict) or set(data) != {"version", "mode"}
                or type(data["version"]) is not int or data["version"] != 1
                or not isinstance(data["mode"], str) or data["mode"] not in _MODES):
            raise ValueError("Invalid local history preference.")
        return data["mode"]

    def status(self):
        return {"mode": self.mode, "next_mode": self.override or self.preference(),
                "launch_override": self.override is not None}

    def set_next_mode(self, mode):
        if self.override or mode not in _MODES:
            raise ValueError("Local history preference cannot be changed.")
        if mode == self.preference():
            return self.status()
        self.ensure_root()
        path = self.root / _PREFERENCE
        _safe_path(path)
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self.root,
                                             prefix=".retention-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump({"version": 1, "mode": mode}, file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return self.status()


@contextmanager
def exclusive_history(root):
    path = root / _LOCK
    _safe_path(path)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
    # Closing this handle releases the lifetime lock on normal exit or process death.
    with os.fdopen(fd, "r+b") as file:
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise RuntimeError("Durable local history is already in use or could not be locked.") from None
        yield


class LocalHistoryStore:
    """SQLite persistence adapter for the existing ConversationMemory."""

    def __init__(self, retention, viewer):
        retention.ensure_root()
        self.path = retention.root / _DATABASE
        self._connection = None
        existing = _safe_path(self.path)
        for suffix in ("-wal", "-shm", "-journal"):
            if _safe_path(self.path.with_name(self.path.name + suffix)) and not existing:
                raise RuntimeError("Local history has a journal without its database.")
        if not existing:
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            os.close(fd)
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True)) as conn:
            if existing:
                if (conn.execute("PRAGMA application_id").fetchone()[0] != _APPLICATION_ID
                        or conn.execute("PRAGMA user_version").fetchone()[0] != _SCHEMA_VERSION
                        or conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]
                        or conn.execute("PRAGMA foreign_key_check").fetchone() is not None):
                    raise RuntimeError("Local history is damaged or has an unsupported schema.")
                objects = set(conn.execute(
                    "SELECT type, name FROM sqlite_schema WHERE substr(name, 1, 7) <> 'sqlite_'"
                ))
                expected = {("table", name) for name in _TABLES} | {("index", "idx_conversation_registry_live")}
                if objects != expected:
                    raise RuntimeError("Local history has an unexpected database layout.")
            if conn.execute("PRAGMA journal_mode=WAL").fetchone()[0] != "wal":
                raise RuntimeError("Local history requires SQLite WAL mode.")
        with self.transaction() as conn:
            if not existing:
                AuthStore.initialize_schema(conn)
                ConversationRegistry.initialize_schema(conn)
                conn.execute("""CREATE TABLE local_history_identity (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                    owner_id TEXT NOT NULL REFERENCES users(id),
                    proof_id TEXT NOT NULL, proof_key BLOB NOT NULL CHECK(length(proof_key)=32)
                )""")
                conn.execute("""CREATE TABLE local_history_transcripts (
                    id TEXT PRIMARY KEY, snapshot TEXT NOT NULL
                ) WITHOUT ROWID""")
                AuthStore.insert_user(conn, viewer)
                conn.execute("INSERT INTO local_history_identity VALUES (1, ?, ?, ?)",
                             (viewer.id, uuid4().hex, secrets.token_bytes(32)))
                conn.execute(f"PRAGMA application_id={_APPLICATION_ID}")
                conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
            identity = conn.execute(
                "SELECT owner_id, proof_id, proof_key FROM local_history_identity WHERE singleton=1"
            ).fetchone()
            accounts = conn.execute("SELECT id, username, role, clearance, is_active FROM users").fetchall()
            if (identity is None or accounts != [(identity[0], "local_dev", "viewer", "L1", 1)]
                    or not identity[0].startswith("local_dev_")):
                raise RuntimeError("Local history viewer identity is invalid.")
            self.owner = identity[0]
            self.keys = ProvenanceKeyRing(identity[1], {identity[1]: identity[2]})

    def renew_login(self, password_hash):
        # Restart never carries login sessions or plaintext credentials forward.
        with self.transaction() as conn:
            conn.execute("DELETE FROM sessions")
            conn.execute("DELETE FROM failed_logins")
            conn.execute("UPDATE users SET password_hash=?, must_change_password=0 WHERE id=?",
                         (password_hash, self.owner))

    @contextmanager
    def transaction(self):
        with immediate_transaction(self.path, require_existing=True, full_sync=True) as conn:
            conn.execute("PRAGMA secure_delete=ON")
            self._connection = conn
            try:
                yield conn
            finally:
                self._connection = None

    def borrow_connection(self):
        if self._connection is None:
            raise RuntimeError("Local history access requires a conversation transaction.")
        return nullcontext(self._connection)

    def load(self):
        with self.borrow_connection() as conn:
            return {sid: _strict_json(data) for sid, data in
                    conn.execute("SELECT id, snapshot FROM local_history_transcripts ORDER BY id")}

    def save(self, snapshot):
        with self.borrow_connection() as conn:
            stored = dict(conn.execute("SELECT id, snapshot FROM local_history_transcripts"))
            conn.executemany("DELETE FROM local_history_transcripts WHERE id=?",
                             ((sid,) for sid in stored.keys() - snapshot.keys()))
            for sid, conversation in snapshot.items():
                data = json.dumps(conversation, separators=(",", ":"), sort_keys=True)
                if stored.get(sid) != data:
                    conn.execute("""INSERT INTO local_history_transcripts VALUES (?, ?)
                        ON CONFLICT(id) DO UPDATE SET snapshot=excluded.snapshot""", (sid, data))
