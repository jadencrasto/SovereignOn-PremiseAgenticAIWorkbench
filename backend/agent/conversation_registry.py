"""Non-authorizing conversation lifecycle registry; task access remains denied."""
import sqlite3
import uuid
from contextlib import closing
from enum import Enum
from datetime import datetime, timezone
from pathlib import Path

from backend.agent.sqlite_transaction import immediate_transaction


class ConversationClassification(str, Enum):
    """Non-authorizing classification; unknown must never grant access."""
    UNKNOWN = 'unknown_quarantined'
    REVOKED = 'revoked'


def require_registry_for_existing_history(memory_path: Path, registry_path: Path) -> None:
    """Never silently initialize a missing security DB alongside existing history."""
    if Path(memory_path).exists() and not Path(registry_path).is_file():
        raise RuntimeError('Conversation security registry is missing; recovery is required')


class ConversationRegistry:
    def __init__(self, db_path: Path):
        self.path = Path(db_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with immediate_transaction(self.path) as conn:
            conn.execute('''CREATE TABLE IF NOT EXISTS conversation_security_registry (
                conversation_id TEXT NOT NULL, generation TEXT PRIMARY KEY,
                owner_id TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('pending','active','revoked')),
                revision INTEGER NOT NULL CHECK(revision > 0), created_at TEXT NOT NULL,
                revoked_at TEXT, UNIQUE(conversation_id, revision))''')
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_registry_live ON conversation_security_registry(conversation_id) WHERE state IN ('pending','active')")

    def begin(self, conversation_id: str, owner_id: str) -> str:
        if not conversation_id or not owner_id:
            raise ValueError('An owner and conversation are required')
        generation = str(uuid.uuid4())
        with immediate_transaction(self.path, require_existing=True) as conn:
            revision = conn.execute('SELECT COALESCE(MAX(revision),0)+1 FROM conversation_security_registry WHERE conversation_id=?', (conversation_id,)).fetchone()[0]
            conn.execute('INSERT INTO conversation_security_registry VALUES (?,?,?,?,?,?,NULL)',
                         (conversation_id, generation, owner_id, 'pending', revision, datetime.now(timezone.utc).isoformat()))
        return generation

    def activate(self, conversation_id: str, generation: str, owner_id: str) -> None:
        with immediate_transaction(self.path, require_existing=True) as conn:
            count = conn.execute("UPDATE conversation_security_registry SET state='active' WHERE conversation_id=? AND generation=? AND owner_id=? AND state='pending'", (conversation_id, generation, owner_id)).rowcount
            if count != 1:
                raise ValueError('Pending identity unavailable')

    def revoke(self, conversation_id: str) -> None:
        with immediate_transaction(self.path, require_existing=True) as conn:
            conn.execute("UPDATE conversation_security_registry SET state='revoked', revoked_at=? WHERE conversation_id=? AND state IN ('pending','active')", (datetime.now(timezone.utc).isoformat(), conversation_id))

    def rows(self):
        with closing(sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro', uri=True, timeout=1)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute('SELECT * FROM conversation_security_registry')]

    def classify(self, conversation_id: str) -> ConversationClassification:
        """Return only conservative states; SQLite alone cannot prove freshness.

        This result is not connected to conversation or task authorization.
        Read-only mode prevents a missing database from being silently recreated.
        A restored older database can look valid, so active is still unknown.
        """
        if not isinstance(conversation_id, str) or not conversation_id:
            return ConversationClassification.UNKNOWN
        try:
            revoked = self.observed_revoked(conversation_id)
        except (sqlite3.Error, OSError, ValueError):
            return ConversationClassification.UNKNOWN
        if revoked:
            return ConversationClassification.REVOKED
        return ConversationClassification.UNKNOWN

    def observed_revoked(self, conversation_id: str) -> bool:
        """Read the latest state; propagate storage errors to security callers."""
        with closing(sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro', uri=True, timeout=1)) as conn:
            row = conn.execute(
                'SELECT state FROM conversation_security_registry WHERE conversation_id=? ORDER BY revision DESC LIMIT 1',
                (conversation_id,),
            ).fetchone()
        return bool(row and row[0] == 'revoked')

    def has_active_binding(self, conversation_id: str, generation: str, owner_id: str) -> bool:
        """Check current row; this is not verified provenance or authorization."""
        with closing(sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro', uri=True, timeout=1)) as conn:
            row = conn.execute("SELECT 1 FROM conversation_security_registry WHERE conversation_id=? AND generation=? AND owner_id=? AND state='active'", (conversation_id, generation, owner_id)).fetchone()
        return bool(row)

    def observed_pending(self, conversation_id: str) -> bool:
        with closing(sqlite3.connect(f'{self.path.resolve().as_uri()}?mode=ro', uri=True, timeout=1)) as conn:
            row = conn.execute('SELECT state FROM conversation_security_registry WHERE conversation_id=? ORDER BY revision DESC LIMIT 1', (conversation_id,)).fetchone()
        return bool(row and row[0] == 'pending')
