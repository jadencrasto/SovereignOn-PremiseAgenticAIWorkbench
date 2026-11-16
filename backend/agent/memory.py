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
import hashlib
import hmac
import secrets
import logging
import uuid
import os
import tempfile
import threading
import re
import time
from collections import OrderedDict
from backend.agent.numeric_receipts import NumericReceipt
from functools import wraps
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from pathlib import Path
from datetime import datetime, timezone

from backend.models.base import Message
from backend.config import PROJECT_ROOT

logger = logging.getLogger(__name__)

MEMORY_FILE = PROJECT_ROOT / "data" / "memory.json"


def safe_tool_history(events):
    """Allow only bounded, non-executable tool status metadata."""
    if not isinstance(events, list):
        return []
    result = []
    for event in events[:32]:
        if not isinstance(event, dict):
            continue
        name, call_id, success = event.get('tool'), event.get('invocation_id'), event.get('success')
        if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name)
                or not isinstance(call_id, str) or not re.fullmatch(r'[a-f0-9-]{36}', call_id)
                or not isinstance(success, bool)):
            continue
        result.append({'type': 'tool_result', 'tool': name, 'success': success, 'invocation_id': call_id})
    return result


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
                self._lifecycle_lock_owner = threading.get_ident()
                try:
                    self._store.clear()
                    self._lifecycle_load()
                    return method(self, *args, **kwargs)
                finally:
                    self._lock_depth -= 1
                    self._lifecycle_lock_owner = None
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
        security_registry=None,
    ) -> None:
        # Never persisted: signed plain-chat output is replayable only in this process.
        self._plain_chat_key = secrets.token_bytes(32)
        self._numeric_receipts: OrderedDict[str, NumericReceipt] = OrderedDict()
        self._store: Dict[str, Conversation] = {}
        self._lock = threading.RLock()
        self._lock_depth = 0
        self._lifecycle_lock_owner = None
        self._security_registry = security_registry
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
            self._lifecycle_load()
        if self._security_registry is not None and self._storage_file:
            self._reconcile_security_registry()

    @_synchronized
    def _reconcile_security_registry(self) -> None:
        """Quarantine mismatched active rows; never promote pending or legacy data."""
        for row in self._security_registry.rows():
            if row['state'] != 'active':
                continue
            conv = self._store.get(row['conversation_id'])
            if (conv is None or conv.owner_id != row['owner_id'] or
                    conv.extra_fields.get('conversation_generation') != row['generation']):
                self._lifecycle_registry_mutation('revoke', row['conversation_id'])

    @property
    def persistence_configured(self) -> bool:
        return self._storage_file is not None and self._security_registry is not None

    def attach_security_registry(self, registry) -> None:
        """Attach once at startup; persistent memory is required for reconciliation."""
        if self._security_registry is not None:
            raise RuntimeError('Conversation registry already attached')
        if self._storage_file is None:
            raise RuntimeError('Conversation registry requires persistent memory')
        self._security_registry = registry
        self._reconcile_security_registry()

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

    def _lifecycle_load(self) -> None:
        if not self._storage_file or not self._storage_file.exists():
            return
        try:
            with open(self._storage_file, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError('Invalid conversation storage')
            for sid, msg_dicts in data.items():
                owner_id = None
                extra_fields = {}
                stored_as_object = isinstance(msg_dicts, dict)
                if stored_as_object:
                    owner_id = msg_dicts['owner_id']
                    extra_fields = {k: v for k, v in msg_dicts.items() if k not in {'owner_id', 'messages'}}
                    msg_dicts = msg_dicts['messages']
                conv = Conversation(session_id=sid, owner_id=owner_id, extra_fields=extra_fields, stored_as_object=stored_as_object)
                if not isinstance(msg_dicts, list):
                    raise ValueError('Invalid conversation messages')
                seen_ids = set()
                for index, msg_dict in enumerate(msg_dicts):
                    if not isinstance(msg_dict, dict) or msg_dict.get('role') not in {'user', 'assistant', 'system'} or not isinstance(msg_dict.get('content'), str):
                        raise ValueError('Invalid conversation message')
                    known = {k: msg_dict[k] for k in ('role', 'content', 'images', 'id', 'created_at') if k in msg_dict}
                    if not known.get('id'):
                        known['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sovereign-conversation:{sid}:{index}"))
                    if not isinstance(known['id'], str) or known['id'] in seen_ids:
                        raise ValueError('Invalid or duplicate message ID')
                    seen_ids.add(known['id'])
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
        self._lifecycle_create_if_missing(sid, system_prompt, allow_recreate=True)
        return sid

    @_synchronized
    def session_exists(self, session_id: str) -> bool:
        self._authorize(session_id)
        return session_id in self._store

    @_synchronized
    def stream_identity(self, session_id: str):
        """Return a short-lived identity snapshot; never grants authorization."""
        self._authorize(session_id)
        conv = self._store.get(session_id)
        if conv is None:
            raise PermissionError('Conversation not found')
        return (conv.owner_id, conv.extra_fields.get('conversation_generation'))

    @_synchronized
    def numeric_receipt(self, session_id: str, *, following_message_id: str | None = None):
        owner, generation = self.stream_identity(session_id)
        receipt = self._numeric_receipts.get(session_id)
        if not receipt or not owner or owner != self._current_owner():
            return None
        if (receipt.owner, receipt.generation) != (owner, generation) or receipt.expires_at <= time.monotonic():
            self._numeric_receipts.pop(session_id, None)
            return None
        messages = self._store[session_id].messages
        expected = [receipt.message_id]
        if following_message_id is not None:
            expected.append(following_message_id)
        return receipt if [msg.id for msg in messages[-len(expected):]] == expected else None

    @_synchronized
    def remember_numeric_receipt(self, session_id: str, receipt: NumericReceipt, user_message_id: str):
        identity = self.stream_identity(session_id)
        if identity != (receipt.owner, receipt.generation) or receipt.owner != self._current_owner():
            return
        messages = self._store[session_id].messages
        if [msg.id for msg in messages[-2:]] != [user_message_id, receipt.message_id]:
            return
        previous = self._numeric_receipts.get(session_id)
        if previous and previous.task_id == receipt.task_id:
            return
        self._numeric_receipts[session_id] = receipt
        self._numeric_receipts.move_to_end(session_id)
        while len(self._numeric_receipts) > 256:
            self._numeric_receipts.popitem(last=False)

    @_synchronized
    def prepare_new_chat(self, session_id: str, owner_id: str, system_prompt: Optional[str] = None):
        """Create a registered new session and capture its binding before work."""
        if (not self._storage_file or self._security_registry is None or not owner_id
                or self._current_owner() != owner_id):
            raise PermissionError('Conversation unavailable')
        self._authorize(session_id)
        if session_id in self._store:
            return self.stream_identity(session_id)
        conv = self._lifecycle_create_if_missing(session_id, system_prompt)
        generation = conv.extra_fields.get('conversation_generation')
        try:
            active = self._security_registry.has_active_binding(session_id, generation, owner_id)
        except Exception:
            raise PermissionError('Conversation unavailable') from None
        if not active:
            raise PermissionError('Conversation unavailable')
        return (conv.owner_id, generation)

    @_synchronized
    def is_owned_by(self, session_id: str, owner_id: str) -> bool:
        """Check a persisted relationship without claiming a legacy conversation."""
        if self._observed_revoked(session_id):
            return False
        conv = self._store.get(session_id)
        return bool(conv and conv.owner_id and conv.owner_id == owner_id)

    @_synchronized
    def delete_session(self, session_id: str) -> bool:
        """Delete a session.  Returns True if it existed."""
        return self._lifecycle_delete(session_id)

    # ------------------------------------------------------------------
    # Message operations
    # ------------------------------------------------------------------

    def _plain_chat_mac(self, session_id: str, content: str) -> str:
        owner, generation = self.stream_identity(session_id)
        if not owner or not generation or owner != self._current_owner():
            raise PermissionError('Conversation unavailable')
        payload = json.dumps([session_id, owner, generation, content], ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        return hmac.new(self._plain_chat_key, payload, hashlib.sha256).hexdigest()

    def _incomplete_plain_chat_mac(self, session_id: str, message: Message) -> str:
        owner, generation = self.stream_identity(session_id)
        if not owner or not generation or owner != self._current_owner():
            raise PermissionError('Conversation unavailable')
        payload = json.dumps(['incomplete_plain_chat_v2', session_id, owner, generation,
                              message.id, message.role, message.content, message.created_at,
                              message.extra_fields.get('response_state_v1')],
                             ensure_ascii=False, separators=(',', ':')).encode('utf-8')
        return hmac.new(self._plain_chat_key, payload, hashlib.sha256).hexdigest()

    def sign_independent_plain_chat(self, session_id: str, content: str) -> dict:
        return {'version': 1, 'kind': 'ephemeral_independent_chat', 'sources': [],
                'mac': self._plain_chat_mac(session_id, content)}

    def verify_independent_plain_chat(self, session_id: str, message: Message) -> bool:
        proof = message.extra_fields.get('access_provenance_v1')
        if isinstance(proof, dict) and proof.get('kind') == 'ephemeral_incomplete_chat':
            if (message.role != 'assistant' or not message.id or not message.created_at
                    or message.extra_fields.get('response_state_v1') not in ('interrupted', 'failed')
                    or set(proof) != {'version', 'kind', 'sources', 'mac'}
                    or type(proof.get('version')) is not int or proof['version'] != 2
                    or proof.get('sources') != [] or not isinstance(proof.get('mac'), str)):
                return False
            try:
                return hmac.compare_digest(proof['mac'], self._incomplete_plain_chat_mac(session_id, message))
            except (PermissionError, TypeError, ValueError):
                return False
        if (message.role != 'assistant' or not isinstance(proof, dict) or
                set(proof) != {'version', 'kind', 'sources', 'mac'} or
                type(proof.get('version')) is not int or proof.get('version') != 1 or proof.get('kind') != 'ephemeral_independent_chat' or
                proof.get('sources') != [] or not isinstance(proof.get('mac'), str)):
            return False
        try:
            return hmac.compare_digest(proof['mac'], self._plain_chat_mac(session_id, message.content))
        except (PermissionError, TypeError, ValueError):
            return False

    @_synchronized
    def add_user_message(self, session_id: str, content: str, attachment=None) -> Message:
        """Append a user message to the session."""
        descriptor = None
        if attachment is not None:
            # This descriptor is server-produced; bytes and filesystem paths never enter history.
            identifier = str(uuid.UUID(str(attachment['attachment_id'])))
            filename = attachment['filename']
            if not isinstance(filename, str) or not filename or len(filename) > 255 or '/' in filename or '\\' in filename:
                raise ValueError('Invalid attachment filename')
            mime = attachment['mime_type']
            size = attachment['size_bytes']
            if mime not in {'image/png', 'image/jpeg', 'image/webp'} or not isinstance(size, int) or size <= 0 or size > 20 * 1024 * 1024:
                raise ValueError('Invalid attachment metadata')
            descriptor = {'attachment_id': identifier, 'filename': filename, 'mime_type': mime, 'size_bytes': size}
        conv = self._get_or_create(session_id)
        msg = conv.add_message("user", content)
        conv.extra_fields['updated_at'] = msg.created_at
        if descriptor:
            msg.extra_fields['attachments_v1'] = [descriptor]
        self._save()
        return msg

    @_synchronized
    def add_assistant_message(self, session_id: str, content: str, tool_events=None, access_provenance=None) -> Message:
        """Append an assistant message to the session."""
        conv = self._get_or_create(session_id)
        msg = conv.add_message("assistant", content)
        conv.extra_fields['updated_at'] = msg.created_at
        safe_events = safe_tool_history(tool_events)
        if safe_events:
            msg.extra_fields['tool_events_v1'] = safe_events
        if access_provenance is not None:
            msg.extra_fields['access_provenance_v1'] = access_provenance
        self._save()
        return msg

    @_synchronized
    def record_incomplete_plain_response(self, session_id, identity, content, status, access_provenance):
        """Record an incomplete plain response only for the original live conversation."""
        if status not in ('interrupted', 'failed') or not isinstance(content, str):
            raise ValueError('Invalid incomplete response')
        if self.stream_identity(session_id) != identity:
            raise PermissionError('Conversation unavailable')
        conv = self._store[session_id]
        msg = conv.add_message('assistant', content)
        msg.extra_fields['response_state_v1'] = status
        if access_provenance is not None:
            msg.extra_fields['access_provenance_v1'] = access_provenance
            if self.verify_independent_plain_chat(session_id, msg):
                msg.extra_fields['access_provenance_v1'] = {
                    'version': 2, 'kind': 'ephemeral_incomplete_chat', 'sources': [],
                    'mac': self._incomplete_plain_chat_mac(session_id, msg),
                }
        conv.extra_fields['updated_at'] = msg.created_at
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
    def get_timestamps(self, session_id: str):
        self._authorize(session_id)
        conv = self._store.get(session_id)
        if conv is None:
            raise KeyError(session_id)
        return (conv.extra_fields.get('created_at'), conv.extra_fields.get('updated_at'))

    @_synchronized
    def set_title(self, session_id: str, title: str) -> None:
        self._authorize(session_id)
        if session_id not in self._store:
            raise KeyError(session_id)
        conv = self._store[session_id]
        conv.stored_as_object = True
        conv.extra_fields['title'] = title
        conv.extra_fields['updated_at'] = datetime.now(timezone.utc).isoformat()
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
            return [sid for sid in self._store if not self._observed_revoked(sid)]
        from backend.auth.request_context import local_legacy_access
        return [sid for sid, conv in self._store.items()
                if (conv.owner_id == owner or (conv.owner_id is None and local_legacy_access.get() and owner == 'user_dev_admin'))
                and not self._observed_revoked(sid)
                and self._binding_active(conv, owner)]

    @_synchronized
    def session_count(self) -> int:
        return len(self.list_sessions())

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _observed_revoked(self, session_id: str) -> bool:
        if self._security_registry is None:
            return False
        try:
            return (self._security_registry.observed_revoked(session_id)
                    or self._security_registry.observed_pending(session_id))
        except Exception:
            raise PermissionError('Conversation access unavailable') from None

    def _binding_active(self, conv: Conversation, owner: str) -> bool:
        if self._security_registry is None:
            return True
        generation = conv.extra_fields.get('conversation_generation')
        if not conv.owner_id:
            return False  # unassigned legacy records cannot prove an active binding
        if conv.owner_id != owner or not isinstance(generation, str):
            return False
        try:
            return self._security_registry.has_active_binding(conv.session_id, generation, owner)
        except Exception:
            raise PermissionError('Conversation access unavailable') from None

    def _get_or_create(self, session_id: str) -> Conversation:
        return self._lifecycle_create_if_missing(session_id)

    def _lifecycle_create_if_missing(self, session_id: str, system_prompt: Optional[str] = None, *, allow_recreate: bool = False) -> Conversation:
        """Create or reuse an entry; caller holds the conversation lock."""
        self._authorize(session_id, allow_missing_revoked=allow_recreate)
        if session_id not in self._store:
            owner = self._current_owner()
            generation = (self._lifecycle_registry_mutation('begin', session_id, owner)
                          if self._security_registry is not None and owner and self._storage_file
                          else str(uuid.uuid4()))
            now = datetime.now(timezone.utc).isoformat()
            conv = Conversation(session_id=session_id, owner_id=owner,
                                extra_fields={'schema_version': 2, 'conversation_generation': generation,
                                              'created_at': now, 'updated_at': now},
                                stored_as_object=True)
            if system_prompt:
                conv.add_message('system', system_prompt)
            self._store[session_id] = conv
            self._save()
            if self._security_registry is not None and owner and self._storage_file:
                with self._storage_file.open('r', encoding='utf-8') as stored:
                    persisted = json.load(stored).get(session_id)
                if (not isinstance(persisted, dict) or persisted.get('owner_id') != owner or
                        persisted.get('conversation_generation') != generation):
                    raise RuntimeError('Conversation persistence could not be confirmed')
                self._lifecycle_registry_mutation('activate', session_id, generation, owner)
        return self._store[session_id]

    def _lifecycle_delete(self, session_id: str) -> bool:
        """Delete an authorized entry; caller holds the conversation lock."""
        self._authorize(session_id)
        existed = session_id in self._store
        if existed and self._security_registry is not None:
            self._lifecycle_registry_mutation('revoke', session_id)
        self._store.pop(session_id, None)
        self._numeric_receipts.pop(session_id, None)
        if existed:
            self._save()
        return existed

    def _lifecycle_bind_legacy_owner(self, conv: Conversation, owner: str) -> None:
        """Bind only after the existing local legacy policy has allowed it."""
        from backend.auth.request_context import effective_owner, local_legacy_access
        if conv.owner_id is not None or owner != 'user_dev_admin' or effective_owner.get() != owner or not local_legacy_access.get():
            raise PermissionError('Conversation not found')
        conv.owner_id = owner
        self._save()

    def _lifecycle_registry_mutation(self, operation: str, *args):
        """Call registry only inside the persistent conversation file lock.

        Direct registry calls do not participate in this protocol and are not
        supported as an authorization or conversation-lifecycle boundary.
        """
        if (not self._storage_file or not self._lock_depth or self._security_registry is None
                or self._lifecycle_lock_owner != threading.get_ident()):
            raise RuntimeError('Registry lifecycle mutation requires the conversation lock')
        if operation not in ('begin', 'activate', 'revoke'):
            raise ValueError('Unsupported registry lifecycle operation')
        return getattr(self._security_registry, operation)(*args)

    def _authorize(self, session_id: str, *, allow_missing_revoked: bool = False) -> None:
        from backend.auth.request_context import effective_owner, local_legacy_access
        owner = effective_owner.get()
        conv = self._store.get(session_id)
        if self._observed_revoked(session_id) and not (allow_missing_revoked and conv is None):
            raise PermissionError('Conversation not found')
        if not owner or not conv:
            return
        if conv.owner_id is None and local_legacy_access.get() and owner == 'user_dev_admin':
            if self._security_registry is not None:
                raise PermissionError('Conversation not found')
            self._lifecycle_bind_legacy_owner(conv, owner)
        if conv.owner_id != owner or not self._binding_active(conv, owner):
            raise PermissionError('Conversation not found')

    @staticmethod
    def _current_owner() -> Optional[str]:
        from backend.auth.request_context import effective_owner
        return effective_owner.get()

