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
import math
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
from backend.security.provenance_keystore import ProvenanceKeyRing

logger = logging.getLogger(__name__)

MEMORY_FILE = PROJECT_ROOT / "data" / "memory.json"
_PLAIN_CHAT = 'independent_plain_chat'
_CALCULATION = 'deterministic_calculation'
_KNOWLEDGE = 'local_source_grounded'
_PROVENANCE_VERSION = 3
LOCAL_TURN_FIELD = 'local_turn_v1'
LOCAL_PLAN_LIMIT = 3
_LOCAL_CALCULATOR = 'local-verified-calculator'
_LOCAL_CONVERSION = 'local-verified-unit-conversion'
_LOCAL_NUMERIC_FIELD = 'numeric_result_v1'
LOCAL_NUMERIC_SOURCE_FIELD = 'numeric_source_v1'
LOCAL_RECALL_PRODUCER = 'local-verified-result-recall'


def _valid_local_numeric_source(source):
    keys = {'turn_id', 'attempt', 'invocation_id'}

    def valid_link(link):
        return (link.keys() == keys
                and all(isinstance(link[key], str) and re.fullmatch(r'[a-f0-9-]{36}', link[key])
                        for key in ('turn_id', 'invocation_id'))
                and type(link['attempt']) is int and link['attempt'] >= 1)

    return (isinstance(source, dict)
            and valid_link({key: value for key, value in source.items() if key != 'secondary'})
            and ('secondary' not in source or (isinstance(source['secondary'], dict) and valid_link(source['secondary']))))


def _local_number(value):
    return (type(value) is float and math.isfinite(value)
            or type(value) is int and value.bit_length() <= 2048)


def _local_result_value(value, tool, unit):
    from typing import get_args
    from backend.tools.unit_conversion import LocalUnit

    if tool == 'calculator':
        return (unit is None or unit in get_args(LocalUnit)) and _local_number(value)
    return tool == 'unit_conversion' and unit in get_args(LocalUnit) and type(value) is float and _local_number(value)


class LocalTurnConflict(Exception):
    """The turn ID, attempt, or conversation state does not permit submission."""


def public_local_turn(record):
    fields = ('attempt', 'state', 'model', 'error_code', 'assistant_id')
    public = {key: record[key] for key in fields if key in record}
    if record.get('tools_enabled') is False:
        public['tools_enabled'] = False
    if 'knowledge_scope' in record:
        public['knowledge_scope'] = record['knowledge_scope']['scope']
    return public


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


def safe_image_attachment(item):
    """Project one validated metadata descriptor without exposing bytes or paths."""
    if not isinstance(item, dict):
        return None
    try:
        identifier = str(uuid.UUID(str(item.get('attachment_id'))))
    except ValueError:
        return None
    filename, mime, size = item.get('filename'), item.get('mime_type'), item.get('size_bytes')
    if (not isinstance(filename, str) or not filename.strip() or len(filename) > 255
            or re.search(r'[/\\:\x00-\x1f\x7f]', filename)
            or mime not in ('image/png', 'image/jpeg', 'image/webp')
            or type(size) is not int or not 0 < size <= 20 * 1024 * 1024):
        return None
    return {'attachment_id': identifier, 'filename': filename, 'mime_type': mime, 'size_bytes': size}


def _synchronized(method):
    @wraps(method)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            if not self._has_storage or self._lock_depth:
                return method(self, *args, **kwargs)
            if self._sqlite_store is not None:
                with self._sqlite_store.transaction():
                    return self._locked_call(method, args, kwargs)
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
                try:
                    return self._locked_call(method, args, kwargs)
                finally:
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
        *,
        provenance_keys: ProvenanceKeyRing | None = None,
        sqlite_store=None,
    ) -> None:
        self._provenance_keys = provenance_keys
        self._provenance_key = (provenance_keys.signing_key() if provenance_keys is not None
                                else (uuid.uuid4().hex, secrets.token_bytes(32)))
        self._numeric_receipts: OrderedDict[str, NumericReceipt] = OrderedDict()
        self._store: Dict[str, Conversation] = {}
        self._lock = threading.RLock()
        self._lock_depth = 0
        self._lifecycle_lock_owner = None
        self._security_registry = security_registry
        self._sqlite_store = sqlite_store
        # Isolate tests: in test environments (e.g. pytest), do not load/save disk state unless explicitly requested
        is_test_env = "PYTEST_CURRENT_TEST" in os.environ or os.environ.get("ENVIRONMENT") == "test"
        if persist is None:
            persist = not is_test_env or storage_file is not None

        if sqlite_store is not None:
            if storage_file is not None or security_registry is None:
                raise ValueError('SQLite history requires its own conversation registry')
            self._storage_file = None
        elif storage_file is not None:
            self._storage_file = Path(storage_file)
        elif persist:
            self._storage_file = MEMORY_FILE
        else:
            self._storage_file = None

        if self._storage_file:
            self._lifecycle_load()
        if self._security_registry is not None and self._storage_file:
            self._reconcile_security_registry()

    @property
    def _has_storage(self):
        return self._storage_file is not None or self._sqlite_store is not None

    def _locked_call(self, method, args, kwargs):
        self._lock_depth += 1
        self._lifecycle_lock_owner = threading.get_ident()
        try:
            self._store.clear()
            self._lifecycle_load()
            return method(self, *args, **kwargs)
        finally:
            self._lock_depth -= 1
            self._lifecycle_lock_owner = None

    @_synchronized
    def recover_local_history(self, owner):
        """Validate a dedicated local store and interrupt abandoned attempts without executing them."""
        if self._sqlite_store is None or self._current_owner() != owner:
            raise PermissionError('Local history recovery is unavailable')
        rows = self._security_registry.rows()
        if any(row['owner_id'] != owner or row['state'] not in ('active', 'revoked') for row in rows):
            raise ValueError('Invalid local conversation ownership')
        active = {row['conversation_id']: row['generation'] for row in rows if row['state'] == 'active'}
        if active.keys() != self._store.keys():
            raise ValueError('Local history does not match its ownership registry')
        changed = False
        for sid, conv in self._store.items():
            if (conv.owner_id != owner or conv.extra_fields.get('schema_version') != 2
                    or conv.extra_fields.get('conversation_generation') != active[sid]):
                raise ValueError('Invalid local conversation binding')
            expected_assistant = None
            for index, message in enumerate(conv.messages):
                if message.images:
                    raise ValueError('Local history contains unexpected attachments')
                if expected_assistant:
                    if message.id != expected_assistant or not self.verify_local_reply(sid, message):
                        raise ValueError('Invalid completed local reply')
                    expected_assistant = None
                    continue
                if index == 0 and message.role == 'system' and not message.extra_fields:
                    continue
                turn = message.extra_fields.get(LOCAL_TURN_FIELD)
                if (message.role != 'user' or set(message.extra_fields) != {LOCAL_TURN_FIELD}
                        or not isinstance(turn, dict) or type(turn.get('attempt')) is not int
                        or not 1 <= turn['attempt'] <= 2_147_483_647
                        or not isinstance(turn.get('model'), str) or not turn['model']):
                    raise ValueError('Invalid local turn metadata')
                state = turn.get('state')
                fields = {'attempt', 'model', 'state'}
                if 'tools_enabled' in turn:
                    if (type(turn['tools_enabled']) is not bool
                            or (turn['tools_enabled'] is False and 'calculator_progress' in turn)):
                        raise ValueError('Invalid local tool preference')
                    fields.add('tools_enabled')
                if 'numeric_tool' in turn:
                    if turn['numeric_tool'] not in ('calculator', 'unit_conversion') or turn.get('tools_enabled') is False:
                        raise ValueError('Invalid local numeric tool')
                    fields.add('numeric_tool')
                if 'calculator_progress' in turn and state != 'completed':
                    self._verified_local_progress(sid, (owner, active[sid]), message)
                    fields.add('calculator_progress')
                if 'knowledge_scope' in turn:
                    self._verified_knowledge_scope(sid, (owner, active[sid]), message)
                    if 'numeric_tool' in turn or 'calculator_progress' in turn:
                        raise ValueError('Invalid mixed local turn state')
                    fields.add('knowledge_scope')
                if state == 'completed' and set(turn) == fields | {'assistant_id'}:
                    expected_assistant = turn['assistant_id']
                    if not isinstance(expected_assistant, str) or not expected_assistant:
                        raise ValueError('Invalid local reply reference')
                elif state == 'running' and set(turn) == fields and index == len(conv.messages) - 1:
                    turn.update(state='interrupted', error_code='TURN_INTERRUPTED')
                    conv.extra_fields['updated_at'] = datetime.now(timezone.utc).isoformat()
                    changed = True
                elif (state not in ('failed', 'cancelled', 'interrupted') or set(turn) != fields | {'error_code'}
                      or not isinstance(turn['error_code'], str) or not re.fullmatch(r'[A-Z_]{1,50}', turn['error_code'])):
                    raise ValueError('Invalid local attempt state')
            if expected_assistant:
                raise ValueError('Completed local turn is missing its reply')
        if changed:
            self._save()

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
        return self._has_storage and self._security_registry is not None

    def attach_security_registry(self, registry) -> None:
        """Attach once at startup; persistent memory is required for reconciliation."""
        if self._security_registry is not None:
            raise RuntimeError('Conversation registry already attached')
        if self._storage_file is None:
            raise RuntimeError('Conversation registry requires persistent memory')
        self._security_registry = registry
        self._reconcile_security_registry()

    def _encode_conversation(self, conv):
        messages = [
            {**msg.extra_fields, "role": msg.role, "content": msg.content,
             "images": getattr(msg, "images", []), "id": msg.id, "created_at": msg.created_at}
            for msg in conv.messages
        ]
        if conv.owner_id or conv.stored_as_object:
            return {**conv.extra_fields, 'owner_id': conv.owner_id, 'messages': messages}
        return messages

    def _save(self) -> None:
        if not self._has_storage:
            return
        data = {sid: self._encode_conversation(conv) for sid, conv in self._store.items()}
        if self._sqlite_store is not None:
            self._sqlite_store.save(data)
            return
        tmp_name = None
        try:
            self._storage_file.parent.mkdir(parents=True, exist_ok=True)
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
        if self._sqlite_store is None and (not self._storage_file or not self._storage_file.exists()):
            return
        try:
            if self._sqlite_store is not None:
                data = self._sqlite_store.load()
            else:
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
                        if self._sqlite_store is not None:
                            raise ValueError('Invalid durable conversation message ID')
                        known['id'] = str(uuid.uuid5(uuid.NAMESPACE_URL, f"sovereign-conversation:{sid}:{index}"))
                    if not isinstance(known['id'], str) or known['id'] in seen_ids:
                        raise ValueError('Invalid or duplicate message ID')
                    seen_ids.add(known['id'])
                    known['extra_fields'] = {k: v for k, v in msg_dict.items() if k not in {'role', 'content', 'images', 'id', 'created_at'}}
                    conv.messages.append(Message(**known))
                self._store[sid] = conv
            if self._sqlite_store is None:
                logger.info(f"Loaded {len(self._store)} sessions from {self._storage_file}")
        except Exception as e:
            if self._sqlite_store is None:
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
        if (not self._has_storage or self._security_registry is None or not owner_id
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

    @staticmethod
    def _assistant_mac(key, session_id, identity, message, proof):
        owner, generation = identity
        fields = [
            'sovereignai.assistant-history', proof['version'], proof['kind'], proof['key_id'],
            proof['producer'], session_id, owner, generation, proof['position'],
            message.id, message.created_at, message.role, message.content,
            message.extra_fields.get('response_state_v1', 'completed'),
        ]
        if 'tool_events_v1' in message.extra_fields:
            fields.append(message.extra_fields['tool_events_v1'])
        if _LOCAL_NUMERIC_FIELD in message.extra_fields:
            fields.append(message.extra_fields[_LOCAL_NUMERIC_FIELD])
        if LOCAL_NUMERIC_SOURCE_FIELD in message.extra_fields:
            fields.append(message.extra_fields[LOCAL_NUMERIC_SOURCE_FIELD])
        from backend.rag.local_knowledge import KNOWLEDGE_FIELD
        if KNOWLEDGE_FIELD in message.extra_fields:
            fields.append(message.extra_fields[KNOWLEDGE_FIELD])
        payload = json.dumps(fields, ensure_ascii=True, separators=(',', ':'), sort_keys=True).encode('ascii')
        return hmac.new(key, payload, hashlib.sha256).hexdigest()

    @_synchronized
    def verify_assistant_provenance(self, session_id: str, message: Message, *, plain_only=False) -> bool:
        from backend.rag.local_knowledge import KNOWLEDGE_FIELD, KNOWLEDGE_PRODUCER, KnowledgeReceipt

        proof = message.extra_fields.get('access_provenance_v1')
        status = message.extra_fields.get('response_state_v1', 'completed')
        allowed_fields = {'access_provenance_v1', 'response_state_v1'}
        knowledge_claim = None
        if isinstance(proof, dict) and proof.get('kind') == _KNOWLEDGE:
            if proof.get('producer') != KNOWLEDGE_PRODUCER or KNOWLEDGE_FIELD not in message.extra_fields:
                return False
            try:
                knowledge_claim = KnowledgeReceipt.model_validate(message.extra_fields[KNOWLEDGE_FIELD])
            except ValueError:
                return False
            allowed_fields.add(KNOWLEDGE_FIELD)
        if isinstance(proof, dict) and LOCAL_NUMERIC_SOURCE_FIELD in message.extra_fields:
            if (proof.get('producer') not in (_LOCAL_CALCULATOR, _LOCAL_CONVERSION, LOCAL_RECALL_PRODUCER)
                    or not _valid_local_numeric_source(message.extra_fields[LOCAL_NUMERIC_SOURCE_FIELD])):
                return False
            allowed_fields.add(LOCAL_NUMERIC_SOURCE_FIELD)
        if (isinstance(proof, dict) and proof.get('kind') == _CALCULATION
                and proof.get('producer') in (_LOCAL_CALCULATOR, _LOCAL_CONVERSION)):
            events = message.extra_fields.get('tool_events_v1')
            tool = 'calculator' if proof['producer'] == _LOCAL_CALCULATOR else 'unit_conversion'
            if (not isinstance(events, list) or not 1 <= len(events) <= LOCAL_PLAN_LIMIT
                    or safe_tool_history(events) != events
                    or events[-1]['tool'] != tool
                    or any(event['tool'] not in ('calculator', 'unit_conversion')
                           or event['success'] is not True for event in events)):
                return False
            allowed_fields.add('tool_events_v1')
            if _LOCAL_NUMERIC_FIELD in message.extra_fields:
                numeric = message.extra_fields[_LOCAL_NUMERIC_FIELD]
                if (not isinstance(numeric, dict)
                        or numeric.keys() != {'turn_id', 'attempt', 'invocation_id', 'value', 'unit'}
                        or not isinstance(numeric['turn_id'], str)
                        or type(numeric['attempt']) is not int or numeric['attempt'] < 1
                        or numeric['invocation_id'] != events[-1]['invocation_id']
                        or not _local_result_value(numeric['value'], tool, numeric['unit'])):
                    return False
                allowed_fields.add(_LOCAL_NUMERIC_FIELD)
            elif tool == 'unit_conversion':
                return False
        if (message.role != 'assistant' or message.images != [] or not isinstance(message.content, str)
                or not message.id or not isinstance(message.created_at, str) or not message.created_at
                or not isinstance(proof, dict)
                or set(proof) != {'version', 'kind', 'key_id', 'producer', 'position', 'mac'}
                or type(proof['version']) is not int or proof['version'] != _PROVENANCE_VERSION
                or proof['kind'] not in (_PLAIN_CHAT, _CALCULATION, _KNOWLEDGE)
                or (plain_only and proof['kind'] != _PLAIN_CHAT)
                or not isinstance(proof['producer'], str) or not proof['producer']
                or type(proof['position']) is not int or proof['position'] < 0
                or not isinstance(proof['mac'], str) or not re.fullmatch(r'[0-9a-f]{64}', proof['mac'])
                or set(message.extra_fields) - allowed_fields
                or ('response_state_v1' in message.extra_fields and
                    (proof['kind'] != _PLAIN_CHAT or status not in ('interrupted', 'failed')))):
            return False
        try:
            identity = self.stream_identity(session_id)
        except PermissionError:
            return False
        owner, generation = identity
        messages = self._store[session_id].messages
        position = proof['position']
        if (not owner or owner != self._current_owner() or not generation
                or position >= len(messages) or messages[position].id != message.id):
            return False
        if knowledge_claim is not None:
            if position == 0:
                return False
            source_turn = messages[position - 1]
            record = source_turn.extra_fields.get(LOCAL_TURN_FIELD, {})
            if (source_turn.role != 'user' or source_turn.id != knowledge_claim.turn_id
                    or not isinstance(record, dict) or record.get('attempt') != knowledge_claim.attempt
                    or record.get('state') != 'completed'
                    or record.get('assistant_id') != message.id
                    or hashlib.sha256(source_turn.content.encode('utf-8')).hexdigest() != knowledge_claim.request_digest):
                return False
            try:
                scope = self._verified_knowledge_scope(session_id, identity, source_turn)
            except ValueError:
                return False
            if scope != knowledge_claim.scope:
                return False
        if self._provenance_keys is None:
            key_id, key = self._provenance_key
            if proof['key_id'] != key_id:
                return False
        else:
            key = self._provenance_keys.verification_key(proof['key_id'])
        return key is not None and hmac.compare_digest(
            proof['mac'], self._assistant_mac(key, session_id, identity, message, proof)
        )

    def verify_independent_plain_chat(self, session_id: str, message: Message) -> bool:
        return self.verify_assistant_provenance(session_id, message, plain_only=True)

    def verify_local_reply(self, session_id: str, message: Message) -> bool:
        proof = message.extra_fields.get('access_provenance_v1')
        if isinstance(proof, dict) and proof.get('kind') == _KNOWLEDGE:
            return self.verify_assistant_provenance(session_id, message)
        if isinstance(proof, dict) and proof.get('producer') in (_LOCAL_CALCULATOR, _LOCAL_CONVERSION):
            return proof.get('kind') == _CALCULATION and self.verify_assistant_provenance(session_id, message)
        return self.verify_independent_plain_chat(session_id, message)

    @_synchronized
    def add_user_message(self, session_id: str, content: str, attachment=None) -> Message:
        """Append a user message to the session."""
        from backend.multimodal.text_file import safe_text_attachment

        text_descriptor = safe_text_attachment(attachment)
        descriptor = text_descriptor or safe_image_attachment(attachment)
        if attachment is not None and descriptor is None:
            raise ValueError('Invalid attachment metadata')
        conv = self._get_or_create(session_id)
        msg = conv.add_message("user", content)
        conv.extra_fields['updated_at'] = msg.created_at
        if text_descriptor:
            msg.extra_fields['text_attachment_v1'] = text_descriptor
        elif descriptor:
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
    def claim_local_turn(self, session_id, identity, turn_id, attempt, content, model, *, tools_enabled=True,
                         knowledge_scope=None):
        """Atomically persist admission or return the existing attempt's state."""
        if self.stream_identity(session_id) != identity:
            raise PermissionError('Conversation unavailable')
        conv = self._store[session_id]
        turn = next((message for message in conv.messages if message.id == turn_id), None)
        if turn is not None:
            record = turn.extra_fields.get(LOCAL_TURN_FIELD)
            if not record or turn.role != 'user' or turn.content != content or record['model'] != model:
                raise LocalTurnConflict('Turn ID is already bound to a different submission.')
            if record.get('tools_enabled', tools_enabled) is not tools_enabled:
                raise LocalTurnConflict('Turn ID is already bound to a different tool preference.')
            if self._verified_knowledge_scope(session_id, identity, turn) != knowledge_scope:
                raise LocalTurnConflict('Turn ID is already bound to a different local source scope.')
            if attempt == record['attempt']:
                return dict(record), False
            if (attempt != record['attempt'] + 1 or record['state'] not in {'failed', 'cancelled', 'interrupted'}
                    or turn is not conv.messages[-1]):
                raise LocalTurnConflict('Only the latest unsuccessful turn can be retried with the next attempt.')
        else:
            if attempt != 1:
                raise LocalTurnConflict('A new turn must start at attempt 1.')
            if any(message.extra_fields.get(LOCAL_TURN_FIELD, {}).get('state') == 'running'
                   for message in conv.messages):
                raise LocalTurnConflict('A response is already running in this conversation.')
            turn = conv.add_message('user', content)
            turn.id = turn_id
        progress = self._verified_local_progress(session_id, identity, turn)
        record = {'attempt': attempt, 'model': model, 'state': 'running', 'tools_enabled': tools_enabled}
        prior_tool = turn.extra_fields.get(LOCAL_TURN_FIELD, {}).get('numeric_tool')
        if prior_tool is not None:
            record['numeric_tool'] = prior_tool
        if progress is not None:
            record['calculator_progress'] = progress
        if knowledge_scope is not None:
            scope = knowledge_scope.model_dump()
            record['knowledge_scope'] = {
                'scope': scope, 'mac': self._knowledge_scope_mac(session_id, identity, turn, model, scope),
            }
        turn.extra_fields[LOCAL_TURN_FIELD] = record
        conv.extra_fields['updated_at'] = datetime.now(timezone.utc).isoformat()
        self._save()
        return dict(record), True

    @_synchronized
    def read_local_turn(self, session_id, identity, turn_id):
        turn = self._owned_local_turn(session_id, identity, turn_id)
        self._verified_knowledge_scope(session_id, identity, turn)
        return dict(turn.extra_fields[LOCAL_TURN_FIELD])

    def _knowledge_scope_mac(self, session_id, identity, turn, model, scope):
        payload = json.dumps(['local-knowledge-scope-v1', session_id, *identity, turn.id, turn.content, model, scope],
                             sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        return hmac.new(self._provenance_key[1], payload, hashlib.sha256).hexdigest()

    def _verified_knowledge_scope(self, session_id, identity, turn):
        from backend.rag.local_knowledge import KnowledgeScope

        record = turn.extra_fields[LOCAL_TURN_FIELD]
        if 'knowledge_scope' not in record:
            return None
        binding = record['knowledge_scope']
        if (not isinstance(binding, dict) or binding.keys() != {'scope', 'mac'}
                or not isinstance(binding['mac'], str) or not re.fullmatch(r'[a-f0-9]{64}', binding['mac'])):
            raise ValueError('Invalid local knowledge scope')
        scope = KnowledgeScope.model_validate(binding['scope'])
        expected = self._knowledge_scope_mac(session_id, identity, turn, record['model'], scope.model_dump())
        if not hmac.compare_digest(binding['mac'], expected):
            raise ValueError('Invalid local knowledge scope proof')
        return scope

    @_synchronized
    def end_local_turn(self, session_id, identity, turn_id, attempt, state, error_code):
        """Store an unsuccessful terminal state without inserting an assistant message."""
        turn = self._owned_local_turn(session_id, identity, turn_id)
        record = turn.extra_fields[LOCAL_TURN_FIELD]
        if record['attempt'] == attempt and record['state'] == 'running':
            record.update(state=state, error_code=error_code)
            self._store[session_id].extra_fields['updated_at'] = datetime.now(timezone.utc).isoformat()
            self._save()

    def _owned_local_turn(self, session_id, identity, turn_id):
        if self.stream_identity(session_id) != identity:
            raise PermissionError('Conversation unavailable')
        for message in self._store[session_id].messages:
            if message.id == turn_id and message.role == 'user' and LOCAL_TURN_FIELD in message.extra_fields:
                return message
        raise KeyError(turn_id)

    @_synchronized
    def mark_local_numeric_tool(self, session_id, identity, turn_id, attempt, name):
        turn = self._owned_local_turn(session_id, identity, turn_id)
        record = turn.extra_fields[LOCAL_TURN_FIELD]
        if (record['attempt'] != attempt or record['state'] != 'running' or record.get('tools_enabled') is False
                or name not in ('calculator', 'unit_conversion') or record.get('numeric_tool', name) != name):
            raise LocalTurnConflict('Turn is no longer eligible to execute.')
        if 'numeric_tool' not in record:
            record['numeric_tool'] = name
            self._save()

    def _local_prior_result(self, session_id, source, assistant):
        from backend.tools.unit_conversion import BelowAbsoluteZero, validate_absolute_temperature

        prior = source.extra_fields.get(LOCAL_TURN_FIELD, {})
        if (source.role != 'user' or prior.get('state') != 'completed' or prior.get('assistant_id') != assistant.id
                or not self.verify_local_reply(session_id, assistant)):
            return None, None
        events = assistant.extra_fields.get('tool_events_v1')
        tool = ('calculator' if any(event['tool'] == 'calculator' for event in events) else 'unit_conversion') if events else None
        numeric = assistant.extra_fields.get(_LOCAL_NUMERIC_FIELD)
        if (not isinstance(numeric, dict) or numeric.get('turn_id') != source.id
                or numeric.get('attempt') != prior.get('attempt')):
            return tool, None
        # Previously signed temperatures remain readable. An unphysical legacy
        # value cannot be used as an operand under the current quantity rules.
        try:
            validate_absolute_temperature(numeric['value'], numeric['unit'])
        except BelowAbsoluteZero:
            return tool, None
        return tool, dict(numeric)

    @_synchronized
    def local_numeric_result(self, session_id, identity, turn_id, attempt, reference):
        """Resolve a bounded selector against completed, owned turn proofs."""
        from backend.tools.unit_conversion import local_unit_dimension

        turn = self._owned_local_turn(session_id, identity, turn_id)
        messages = self._store[session_id].messages
        current = turn.extra_fields[LOCAL_TURN_FIELD]
        if (len(messages) < 3 or messages[-1] is not turn or current['state'] != 'running'
                or current['attempt'] != attempt or current.get('tools_enabled') is False):
            return None
        _, adjacent = self._local_prior_result(session_id, messages[-3], messages[-2])
        if reference.kind == 'adjacent' and adjacent is None:
            recall = messages[-2]
            source = recall.extra_fields.get(LOCAL_NUMERIC_SOURCE_FIELD)
            proof = recall.extra_fields.get('access_provenance_v1')
            prior = messages[-3].extra_fields.get(LOCAL_TURN_FIELD, {})
            if (isinstance(proof, dict) and proof.get('producer') == LOCAL_RECALL_PRODUCER
                    and prior.get('state') == 'completed' and prior.get('assistant_id') == recall.id
                    and source and self.verify_local_reply(session_id, recall)):
                for origin, reply in zip(messages[:-3], messages[1:-2]):
                    if origin.id == source['turn_id']:
                        _, value = self._local_prior_result(session_id, origin, reply)
                        if (value and value['attempt'] == source['attempt']
                                and value['invocation_id'] == source['invocation_id']):
                            return value
                        break
        if reference.kind == 'adjacent' or (reference.kind == 'before' and adjacent is None):
            return adjacent
        if reference.kind == 'before':
            for index in range(len(messages) - 4, 0, -1):
                _, numeric = self._local_prior_result(session_id, messages[index - 1], messages[index])
                if numeric is not None:
                    return numeric
            return None

        matches = []
        for source, assistant in zip(messages, messages[1:]):
            if assistant is turn and source.role == 'assistant':
                break
            prior = source.extra_fields.get(LOCAL_TURN_FIELD)
            if prior is None:
                continue
            tool, numeric = self._local_prior_result(session_id, source, assistant)
            tool = tool or prior.get('numeric_tool')
            if reference.quantity:
                # A damaged completed result could change which reading an ordinal names.
                if (numeric is None and prior['state'] == 'completed'
                        and (tool or _LOCAL_NUMERIC_FIELD in assistant.extra_fields)
                        and not self.verify_local_reply(session_id, assistant)):
                    return None
                if numeric is None or numeric['unit'] is None or (reference.tool and reference.tool != tool):
                    continue
                unit = numeric['unit']
                quantity = local_unit_dimension(unit)
                if quantity == 'temperature':
                    quantity = 'temperature_difference' if unit.startswith('Δ') else 'absolute_temperature'
                if quantity != reference.quantity:
                    continue
            else:
                # An old incomplete turn has no signed answer or known tool category.
                # Treat it as ambiguous rather than silently shifting a named ordinal.
                if (tool is None and prior['state'] != 'completed' and prior.get('tools_enabled') is not False
                        and prior.get('error_code') not in {'LOCAL_TOOL_CONTEXT', 'LOCAL_TOOL_REFERENCE_UNIT',
                                                           'LOCAL_TOOL_UNIT', 'LOCAL_TOOL_UNAUTHORIZED',
                                                           'LOCAL_TOOL_DIMENSION', 'LOCAL_TOOL_TEMPERATURE',
                                                           'LOCAL_TOOL_TARGET_UNIT', 'LOCAL_TOOL_ABSOLUTE_ZERO'}):
                    return None
                if tool != reference.tool:
                    continue
            matches.append(numeric)
            if reference.kind == 'ordinal' and len(matches) == reference.ordinal:
                return numeric
        if reference.kind == 'latest' and matches:
            return matches[-1]
        return matches[0] if reference.kind == 'unique' and len(matches) == 1 else None

    def _local_progress_mac(self, session_id, identity, turn, plan_digest, steps):
        payload = json.dumps(
            ['local-calculator-progress-v1', session_id, *identity, turn.id, turn.content,
             turn.extra_fields[LOCAL_TURN_FIELD]['model'], plan_digest, steps],
            separators=(',', ':'), allow_nan=False, sort_keys=True,
        ).encode('ascii')
        return hmac.new(self._provenance_key[1], payload, hashlib.sha256).hexdigest()

    def _verified_local_progress(self, session_id, identity, turn):
        record = turn.extra_fields.get(LOCAL_TURN_FIELD, {})
        if 'calculator_progress' not in record:
            return None
        progress = record['calculator_progress']
        if (not isinstance(progress, dict) or progress.keys() != {'plan', 'steps', 'mac'}
                or not isinstance(progress['plan'], str) or not re.fullmatch(r'[a-f0-9]{64}', progress['plan'])
                or not isinstance(progress['mac'], str) or not re.fullmatch(r'[a-f0-9]{64}', progress['mac'])
                or not isinstance(progress['steps'], list) or not 1 <= len(progress['steps']) <= LOCAL_PLAN_LIMIT):
            raise ValueError('Invalid local calculator progress')
        for receipt in progress['steps']:
            if (not isinstance(receipt, dict)
                    or receipt.keys() not in ({'invocation_id', 'result'}, {'invocation_id', 'result', 'tool', 'unit'})
                    or not isinstance(receipt['invocation_id'], str)
                    or not re.fullmatch(r'[a-f0-9-]{36}', receipt['invocation_id'])
                    or not _local_result_value(receipt['result'], receipt.get('tool', 'calculator'), receipt.get('unit'))):
                raise ValueError('Invalid local calculator receipt')
        if not hmac.compare_digest(
            progress['mac'], self._local_progress_mac(session_id, identity, turn, progress['plan'], progress['steps'])
        ):
            raise ValueError('Invalid local calculator proof')
        return progress

    @_synchronized
    def local_calculator_receipts(self, session_id, identity, turn_id, attempt, plan_digest):
        turn = self._owned_local_turn(session_id, identity, turn_id)
        record = turn.extra_fields[LOCAL_TURN_FIELD]
        if record['state'] != 'running' or record['attempt'] != attempt or record.get('tools_enabled') is False:
            raise LocalTurnConflict('Turn is no longer eligible to execute.')
        progress = self._verified_local_progress(session_id, identity, turn)
        if progress is not None and progress['plan'] != plan_digest:
            raise LocalTurnConflict('The calculator plan differs from the saved attempt.')
        return progress['steps'] if progress is not None else []

    @_synchronized
    def record_local_calculator_step(self, session_id, identity, turn_id, attempt, plan_digest, index, receipt):
        steps = self.local_calculator_receipts(session_id, identity, turn_id, attempt, plan_digest)
        if index != len(steps) or index >= LOCAL_PLAN_LIMIT:
            raise LocalTurnConflict('Calculator step is no longer eligible to complete.')
        turn = self._owned_local_turn(session_id, identity, turn_id)
        steps = [*steps, receipt]
        turn.extra_fields[LOCAL_TURN_FIELD]['calculator_progress'] = {
            'plan': plan_digest, 'steps': steps,
            'mac': self._local_progress_mac(session_id, identity, turn, plan_digest, steps),
        }
        self._save()

    @_synchronized
    def record_plain_response(
        self, session_id, identity, content, *, status=None, producer=None,
        access_provenance=None, calculation=False, local_turn=None, local_plan_digest=None, local_numeric_source=None,
        local_knowledge=None,
    ):
        """Finalize the original live conversation and authenticate only eligible output."""
        from backend.rag.local_knowledge import KNOWLEDGE_FIELD, KNOWLEDGE_PRODUCER, KnowledgeReceipt

        if local_knowledge is not None:
            if (local_turn is None or producer != KNOWLEDGE_PRODUCER or local_plan_digest is not None
                    or local_numeric_source is not None or calculation or status is not None):
                raise ValueError('Invalid local knowledge response')
        if (status not in (None, 'interrupted', 'failed') or not isinstance(content, str)
                or (calculation and (status is not None or not producer))
                or (local_numeric_source is not None and (local_turn is None or not _valid_local_numeric_source(local_numeric_source)
                    or (local_plan_digest is None and producer != LOCAL_RECALL_PRODUCER)))
                or (producer is not None and (not isinstance(producer, str) or not producer or access_provenance is not None))):
            raise ValueError('Invalid assistant response')
        if self.stream_identity(session_id) != identity:
            raise PermissionError('Conversation unavailable')
        owner, generation = identity
        if producer is not None and (not owner or owner != self._current_owner() or not generation):
            raise PermissionError('Conversation unavailable')
        conv = self._store[session_id]
        events = None
        if local_turn is not None:
            turn_id, attempt = local_turn
            turn = self._owned_local_turn(session_id, identity, turn_id)
            record = turn.extra_fields[LOCAL_TURN_FIELD]
            if (turn is not conv.messages[-1] or record['attempt'] != attempt or record['state'] != 'running'
                    or not producer or status is not None or not content.strip()):
                raise LocalTurnConflict('Turn is no longer eligible to complete.')
            if local_plan_digest is not None:
                steps = self.local_calculator_receipts(session_id, identity, turn_id, attempt, local_plan_digest)
                if not steps:
                    raise LocalTurnConflict('Verified local tool receipts are unavailable.')
                events = [{'type': 'tool_result', 'tool': step.get('tool', 'calculator'), 'success': True,
                           'invocation_id': step['invocation_id']} for step in steps]
                producer = _LOCAL_CONVERSION if events[-1]['tool'] == 'unit_conversion' else _LOCAL_CALCULATOR
                calculation = True
            elif 'calculator_progress' in record:
                raise LocalTurnConflict('Saved calculator work requires the original plan.')
            if local_knowledge is not None:
                receipt = KnowledgeReceipt.model_validate({
                    **local_knowledge, 'turn_id': turn_id, 'attempt': attempt,
                    'request_digest': hashlib.sha256(turn.content.encode('utf-8')).hexdigest(),
                })
                if receipt.scope != self._verified_knowledge_scope(session_id, identity, turn):
                    raise LocalTurnConflict('Local knowledge receipt does not match the admitted source scope.')
                local_knowledge = receipt.model_dump(exclude_none=True)
        msg = conv.add_message('assistant', content)
        if local_knowledge is not None:
            msg.extra_fields[KNOWLEDGE_FIELD] = local_knowledge
        if local_numeric_source is not None:
            msg.extra_fields[LOCAL_NUMERIC_SOURCE_FIELD] = dict(local_numeric_source)
        if events is not None:
            msg.extra_fields['tool_events_v1'] = events
            final = steps[-1]
            msg.extra_fields[_LOCAL_NUMERIC_FIELD] = {
                'turn_id': turn_id, 'attempt': attempt, 'invocation_id': final['invocation_id'],
                'value': final['result'], 'unit': final.get('unit'),
            }
        if status is not None:
            msg.extra_fields['response_state_v1'] = status
        if producer is not None:
            key_id, key = self._provenance_key
            proof = {
                'version': _PROVENANCE_VERSION,
                'kind': _KNOWLEDGE if local_knowledge is not None else _CALCULATION if calculation else _PLAIN_CHAT,
                'key_id': key_id,
                'producer': producer,
                'position': len(conv.messages) - 1,
            }
            proof['mac'] = self._assistant_mac(key, session_id, identity, msg, proof)
            msg.extra_fields['access_provenance_v1'] = proof
        elif access_provenance is not None:
            msg.extra_fields['access_provenance_v1'] = access_provenance
        if local_turn is not None:
            record.pop('calculator_progress', None)
            record.update(state='completed', assistant_id=msg.id)
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
                          if self._security_registry is not None and owner and self._has_storage
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
            if self._security_registry is not None and owner and self._has_storage:
                if self._sqlite_store is not None:
                    persisted = self._sqlite_store.load().get(session_id)
                else:
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
        if (not self._has_storage or not self._lock_depth or self._security_registry is None
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

