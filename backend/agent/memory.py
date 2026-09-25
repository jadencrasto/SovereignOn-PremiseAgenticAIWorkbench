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
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path

from backend.models.base import Message
from backend.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

MEMORY_FILE = PROJECT_ROOT / "data" / "memory.json"

@dataclass
class Conversation:
    """
    A single conversation session.

    session_id : client-visible UUID
    messages   : ordered list of Message objects (system, user, assistant)
    """
    session_id: str
    messages: List[Message] = field(default_factory=list)

    def add_message(self, role: str, content: str) -> Message:
        msg = Message(role=role, content=content)
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

    def __init__(self) -> None:
        self._store: Dict[str, Conversation] = {}
        self._load()

    def _save(self) -> None:
        try:
            MEMORY_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for sid, conv in self._store.items():
                data[sid] = [msg.model_dump() for msg in conv.messages]
            with open(MEMORY_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save memory: {e}")

    def _load(self) -> None:
        if not MEMORY_FILE.exists():
            return
        try:
            with open(MEMORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            for sid, msg_dicts in data.items():
                conv = Conversation(session_id=sid)
                for msg_dict in msg_dicts:
                    conv.messages.append(Message(**msg_dict))
                self._store[sid] = conv
            logger.info(f"Loaded {len(self._store)} sessions from {MEMORY_FILE}")
        except Exception as e:
            logger.error(f"Failed to load memory: {e}")

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

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
        if sid in self._store:
            # Session already exists — return it unchanged
            logger.debug("Reusing existing session %s", sid)
            return sid

        conv = Conversation(session_id=sid)
        if system_prompt:
            conv.add_message("system", system_prompt)
            logger.debug("Created session %s with system prompt (%d chars)", sid, len(system_prompt))
        else:
            logger.debug("Created session %s (no system prompt)", sid)

        self._store[sid] = conv
        self._save()
        return sid

    def session_exists(self, session_id: str) -> bool:
        return session_id in self._store

    def delete_session(self, session_id: str) -> bool:
        """Delete a session.  Returns True if it existed."""
        existed = session_id in self._store
        self._store.pop(session_id, None)
        if existed:
            logger.debug("Deleted session %s", session_id)
            self._save()
        return existed

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    def add_user_message(self, session_id: str, content: str) -> Message:
        """Append a user message to the session."""
        msg = self._get_or_create(session_id).add_message("user", content)
        self._save()
        return msg

    def add_assistant_message(self, session_id: str, content: str) -> Message:
        """Append an assistant message to the session."""
        msg = self._get_or_create(session_id).add_message("assistant", content)
        self._save()
        return msg

    def add_system_message(self, session_id: str, content: str) -> Message:
        """Prepend or append a system message (for mid-session injection)."""
        msg = self._get_or_create(session_id).add_message("system", content)
        self._save()
        return msg

    def get_history(self, session_id: str) -> List[Message]:
        """Return the full message history for a session."""
        if session_id not in self._store:
            return []
        return self._store[session_id].get_history()

    def clear_session(self, session_id: str) -> None:
        """Clear messages but keep the session entry."""
        if session_id in self._store:
            self._store[session_id].clear()
            self._save()

    # ------------------------------------------------------------------
    # Introspection
    # ------------------------------------------------------------------

    def list_sessions(self) -> List[str]:
        return list(self._store.keys())

    def session_count(self) -> int:
        return len(self._store)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _get_or_create(self, session_id: str) -> Conversation:
        if session_id not in self._store:
            logger.debug("Auto-creating session %s", session_id)
            self._store[session_id] = Conversation(session_id=session_id)
            self._save()
        return self._store[session_id]

