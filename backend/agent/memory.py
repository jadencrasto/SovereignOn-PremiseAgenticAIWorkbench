"""
backend/agent/memory.py
-----------------------
In-memory conversation store for Phase 1, modified to persist to disk.

Each session is identified by a UUID.  Messages are stored in an ordered
list per session.  The state is persisted to data/memory.json so it
survives backend restarts and allows continuous chat per user session.
"""

from __future__ import annotations

import json
import logging
import uuid
import os
import tempfile
import threading
from functools import wraps
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime, timezone

from backend.models.base import Message
from backend.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

MEMORY_FILE = PROJECT_ROOT / "data" / "memory.json"


def _synchronized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            if self._storage_file is None or self._lock_depth:
                return method(self, *args, **kwargs)
            self._storage_file.parent.mkdir(parents=True, exist_ok=True)
            lock_path = self._storage_file.with_name(self._storage_file.name + '.lock')
            with open(lock_path, 'a+b') as lock_file:
                if lock_file.tell() == 0:
                    lock_file.write(b'0')
                    lock_file.flush()
                lock_file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(lock_file.fileno(), msvcrt.LK_LOCK, 1)
                else:
                    import fcntl
                    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
                self._lock_depth += 1
                try:
                    self._store.clear()
                    self._load()
                    return method(self, *args, **kwargs)
                finally:
                    self._lock_depth -= 1
                    lock_file.seek(0)
                    if os.name == 'nt':
                        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
                    else:
                        fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)
    return wrapper

@dataclass
class Conversation:
    """
    A single conversation session.

    session_id : client-visible UUID
    messages   : ordered list of Message objects (system, user, assistant)
    """
    session_id: str
    messages: List[Message] = field(default_factory=list)
    owner_id: Optional[str] = None
    extra_fields: dict = field(default_factory=dict, repr=False)
    stored_as_object: bool = False

    def add_message(self, role: str, content: str) -> Message:
        msg = Message(role=role, content=content, id=str(uuid.uuid4()),
                      created_at=datetime.now(timezone.utc).isoformat())
        self.messages.append(msg)
        return msg

    def get_history(self) -> List[Message]:
        """Return all messages in chronological order."""
        return list(self.messages)

    def clear(self) -> None:
        """Remove all messages from this conversation."""
        self.messages.clear()
        logger.debug("Cleared conversation %s", self.session_id)


class ConversationMemory:
    """
    Thread-safe in-memory store of Conversation objects, keyed by session_id,
    persisted to disk.

    Usage:
        memory = ConversationMemory()
        session_id = memory.create_session(system_prompt="You are helpful.")
        memory.add_user_message(session_id, "Hello")
        memory.add_assistant_message(session_id, "Hi there!")
        history = memory.get_history(session_id)
    """

    def __init__(
        self,
        storage_file: Optional[Path | str] = None,
        persist: Optional[bool] = None,
    ) -> None:
        self._store: Dict[str, Conversation] = {}
        self._lock = threading.RLock()
        self._lock_depth = 0
        # Isolate tests: in test environments (e.g. pytest), do not load/save disk state unless explicitly requested
        is_test_env = "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("ENVIRONMENT") == "test"
        if persist is None:
            persist = not is_test_env or storage_file is not None

        if storage_file is not None:
            self._storage_file = Path(storage_file)
        elif persist:
            self._storage_file = MEMORY_FILE
        else:
            self._storage_file = None

        if self._storage_file:
            self._load()

    def _save(self) -> None:
        if not self._storage_file:
            return
        tmp_name = None
        try:
            self._storage_file.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for sid, conv in self._store.items():
                messages = [
                    {**msg.extra_fields,
                        "role": msg.role,
                        "content": msg.content,
                        "images": getattr(msg, "images", []),
                        "id": msg.id,
                        "created_at": msg.created_at,
                    }
                    for msg in conv.messages
                ]
                data[sid] = {**conv.extra_fields, 'owner_id': conv.owner_id, 'messages': messages} if conv.owner_id or conv.stored_as_object else messages
            with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=self._storage_file.parent,
                                             prefix=".memory-", suffix=".tmp", delete=False) as f:
                tmp_name = f.name
                json.dump(data, f, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, self._storage_file)
        except Exception as e:
            logger.error(f"Failed to save memory: {e}")
            raise
        finally:
            if tmp_name and os.path.exists(tmp_name):
                os.unlink(tmp_name)

    def _load(self) -> None:
        if not self._storage_file or not self._storage_file.exists():
            return
        try:
            with open(self._storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            for sid, msg_dicts in data.items():
                owner_id = None
                extra_fields = {}
                stored_as_object = isinstance(msg_dicts, dict)
                if stored_as_object:
                    owner_id = msg_dicts['owner_id']
                    extra_fields = {k: v for k, v in msg_dicts.items() if k not in {'owner_id', 'messages'}}
                    msg_dicts = msg_dicts['messages']
                conv = Conversation(session_id=sid, owner_id=owner_id, extra_fields=extra_fields, stored_as_object=stored_as_object)
                for index, msg_dict in enumerate(msg_dicts):
                    known = {k: msg_dict[k] for k in ('role', 'content', 'images', 'id', 'created_at') if k in msg_dict}
                    if not known.get('id'):
                        known['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sovereign-conversation:{sid}:{index}"))
                    known['extra_fields'] = {k: v for k, v in msg_dict.items() if k not in {'role', 'content', 'images', 'id', 'created_at'}}
                    conv.messages.append(Message(**known))
                self._store[sid] = conv
            logger.info(f"Loaded {len(self._store)} sessions from {self._storage_file}")
        except Exception as e:
            logger.error(f"Failed to load memory: {e}")
            raise

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    @_synchronized
    def create_session(
        self,
        system_prompt: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """
        Create a new conversation session.

        Args:
            system_prompt: Optional system message prepended to every
                           request to the model.
            session_id:    Reuse an existing ID (e.g. client-provided).
                           Generates a new UUID if None.

        Returns:
            The session_id string.
        """
        sid = session_id or str(uuid.uuid4())
        self._authorize(sid)
        if sid in self._store:
            # Session already exists — return it unchanged
            logger.debug("Reusing existing session %s", sid)
            return sid

        conv = Conversation(session_id=sid, owner_id=self._current_owner(),
                            extra_fields={'schema_version': 2}, stored_as_object=True)
        if system_prompt:
            conv.add_message("system", system_prompt)
            logger.debug("Created session %s with system prompt (%d chars)", sid, len(system_prompt))
        else:
            logger.debug("Created session %s (no system prompt)", sid)

        self._store[sid] = conv
        self._save()
        return sid

    @_synchronized
    def session_exists(self, session_id: str) -> bool:
        self._authorize(session_id)
        return session_id in self._store

    @_synchronized
    def delete_session(self, session_id: str) -> bool:
        """Delete a session.  Returns True if it existed."""
        self._authorize(session_id)
        existed = session_id in self._store
        self._store.pop(session_id, None)
        if existed:
            logger.debug("Deleted session %s", session_id)
            self._save()
        return existed

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    @_synchronized
    def add_user_message(self, session_id: str, content: str) -> Message:
        """Append a user message to the session."""
        msg = self._get_or_create(session_id).add_message("user", content)
        self._save()
        return msg

    @_synchronized
    def add_assistant_message(self, session_id: str, content: str) -> Message:
        """Append an assistant message to the session."""
        msg = self._get_or_create(session_id).add_message("assistant", content)
        self._save()
        return msg

    @_synchronized
    def add_system_message(self, session_id: str, content: str) -> Message:
        """Prepend or append a system message (for mid-session injection)."""
        msg = self._get_or_create(session_id).add_message("system", content)
        self._save()
        return msg

    @_synchronized
    def get_history(self, session_id: str) -> List[Message]:
        """Return the full message history for a session."""
        self._authorize(session_id)
        if session_id not in self._store:
            return []
        return self._store[session_id].get_history()

    @_synchronized
    def get_title(self, session_id: str) -> Optional[str]:
        self._authorize(session_id)
        if session_id not in self._store:
            raise KeyError(session_id)
        title = self._store[session_id].extra_fields.get('title')
        return title if isinstance(title, str) else None

    @_synchronized
    def set_title(self, session_id: str, title: str) -> None:
        self._authorize(session_id)
        if session_id not in self._store:
            raise KeyError(session_id)
        conv = self._store[session_id]
        conv.stored_as_object = True
        conv.extra_fields['title'] = title
        self._save()

    @_synchronized
    def clear_session(self, session_id: str) -> None:
        """Clear messages but keep the session entry."""
        self._authorize(session_id)
        if session_id in self._store:
            self._store[session_id].clear()
            self._save()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    @_synchronized
    def list_sessions(self) -> List[str]:
        owner = self._current_owner()
        if owner is None:
            return list(self._store.keys())
        from backend.auth.request_context import local_legacy_access
        return [sid for sid, conv in self._store.items()
                if conv.owner_id == owner or (conv.owner_id is None and local_legacy_access.get() and owner == 'user_dev_admin')]

    @_synchronized
    def session_count(self) -> int:
        return len(self.list_sessions())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create(self, session_id: str) -> Conversation:
        self._authorize(session_id)
        if session_id not in self._store:
            logger.debug("Auto-creating session %s", session_id)
            self._store[session_id] = Conversation(session_id=session_id, owner_id=self._current_owner(),
                                                   extra_fields={'schema_version': 2}, stored_as_object=True)
            self._save()
        return self._store[session_id]

    def _authorize(self, session_id: str) -> None:
        from backend.auth.request_context import effective_owner, local_legacy_access
        owner = effective_owner.get()
        conv = self._store.get(session_id)
        if not owner or not conv:
            return
        if conv.owner_id is None and local_legacy_access.get() and owner == 'user_dev_admin':
            conv.owner_id = owner
            self._save()
        if conv.owner_id != owner:
            raise PermissionError('Conversation not found')

    @staticmethod
    def _current_owner() -> Optional[str]:
        from backend.auth.request_context import effective_owner
        return effective_owner.get()

