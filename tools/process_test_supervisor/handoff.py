"""R5.5 v1 handoff for the fixed cooperative fixture.

The channel adapter owns a single, pre-bootstrapped connection and a pinned
peer handle. A wire message supplies neither a peer identity nor local rights.
"""
from dataclasses import asdict, dataclass
import json
from math import isfinite
import re
import secrets
import struct
import time
from uuid import UUID


_MAX_FRAME = 4096
_HEX_256 = re.compile(r'[0-9a-f]{64}')
_SID = re.compile(r'S-1-(?:[0-9]{1,10}-){0,14}[0-9]{1,10}')


class HandoffError(RuntimeError):
    pass


def _natural(value, upper=2**64 - 1):
    return type(value) is int and 0 < value <= upper


def _handle_slot(value):
    # This fixture supports ordinary 32-bit-significant Windows handle slots.
    # Reject the high pseudo-handle range and its 64-bit extensions.
    return _natural(value, 0xfffffff0)


def generation(value):
    if (type(value) not in (tuple, list) or len(value) != 2
            or not _natural(value[0], 2**32 - 1) or not _natural(value[1])):
        raise HandoffError('Invalid process generation')
    return tuple(value)


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise HandoffError('Duplicate protocol field')
        result[key] = value
    return result


def encode(message):
    body = json.dumps(message, separators=(',', ':'), allow_nan=False).encode('utf-8')
    if len(body) > _MAX_FRAME:
        raise HandoffError('Handoff frame exceeds bound')
    return struct.pack('<I', len(body)) + body


def decode(frame):
    if (type(frame) is not bytes or not 4 < len(frame) <= _MAX_FRAME + 4
            or struct.unpack_from('<I', frame)[0] != len(frame) - 4):
        raise HandoffError('Invalid handoff framing')
    try:
        message = json.loads(frame[4:].decode('utf-8', errors='strict'), object_pairs_hook=_unique)
    except (ValueError, RecursionError):
        raise HandoffError('Invalid handoff payload') from None
    if type(message) is not dict:
        raise HandoffError('Invalid handoff payload')
    return message


@dataclass(frozen=True)
class Binding:
    op: str
    scenario: str
    manifest: str
    nonce: str
    job_token: str

    def __post_init__(self):
        try:
            valid_op = type(self.op) is str and str(UUID(self.op)) == self.op
        except ValueError:
            valid_op = False
        if (not valid_op or self.scenario not in ('normal', 'stall', 'descendant', 'early_exit', 'reject_containment')
                or any(type(value) is not str or not _HEX_256.fullmatch(value)
                       for value in (self.manifest, self.nonce, self.job_token))):
            raise HandoffError('Invalid operation binding')

    @classmethod
    def fresh(cls, operation, scenario, manifest):
        return cls(operation, scenario, manifest, secrets.token_hex(32), secrets.token_hex(32))

    def message(self, kind, seq, **fields):
        return {**asdict(self), 'version': 1, 'type': kind, 'seq': seq, **fields}

    def fields(self, message, kind, seq, names):
        header = self.message(kind, seq)
        if (message.keys() != header.keys() | set(names)
                or any(type(message[key]) is not type(value) or message[key] != value for key, value in header.items())):
            raise HandoffError('Unexpected handoff message')
        return {key: message[key] for key in names}


@dataclass(frozen=True)
class PeerPrincipal:
    user_sid: str
    logon_sid: str
    session: int

    def __post_init__(self):
        if (any(type(sid) is not str or not _SID.fullmatch(sid) for sid in (self.user_sid, self.logon_sid))
                or not self.logon_sid.startswith('S-1-5-5-')
                or type(self.session) is not int or not 0 <= self.session <= 2**32 - 1):
            raise HandoffError('Invalid peer principal')


@dataclass(frozen=True)
class PeerIdentity:
    process: tuple
    principal: PeerPrincipal
    image: str

    def __post_init__(self):
        if (type(self.process) is not tuple or generation(self.process) != self.process
                or type(self.principal) is not PeerPrincipal or type(self.image) is not str
                or not self.image or len(self.image) > 32768 or '\x00' in self.image):
            raise HandoffError('Invalid pinned peer')


class AuthenticatedChannel:
    """One connection with OS evidence supplied by the reviewed transport.

    'client' means the remote party connected to this endpoint. Its actual
    pipe-token principal must be queried after the read, with impersonation
    reverted by the adapter before control returns.
    """
    def __init__(self, transport, native, peer_handle, expected, *, remote, clock=time.monotonic):
        if remote not in ('client', 'server') or type(expected) is not PeerIdentity or peer_handle is None:
            raise HandoffError('Invalid peer bootstrap')
        self._transport = transport
        self._native = native
        self.peer_handle = peer_handle
        self.expected = expected
        self._remote = remote
        self._clock = clock
        self._failed = False

    def check(self, deadline):
        if self._failed or self._transport.connected() is not True or self._clock() >= deadline:
            raise HandoffError('Handoff channel unavailable')
        native, handle = self._native, self.peer_handle
        pid = native.pipe_peer_pid(self._transport, self._remote)
        if (not _natural(pid, 2**32 - 1) or pid != self.expected.process[0]
                or native.running(handle) is not True
                or generation(native.process_identity(handle)) != self.expected.process
                or native.process_principal(handle) != self.expected.principal
                or native.process_image(handle) != self.expected.image
                or native.running(handle) is not True
                or self._transport.connected() is not True
                or self._clock() >= deadline):
            raise HandoffError('Handoff peer identity mismatch')

    def _check_received(self, deadline):
        self.check(deadline)
        if (self._remote == 'client'
                and self._native.pipe_client_principal(self._transport) != self.expected.principal):
            raise HandoffError('Handoff pipe token mismatch')
        self.check(deadline)

    def exchange(self, message, deadline):
        try:
            self.check(deadline)
            self._transport.send(encode(message), deadline)
            response = self._transport.receive(deadline)
            self._check_received(deadline)
            return decode(response)
        except BaseException:
            self._failed = True
            raise

    def receive(self, deadline):
        try:
            self.check(deadline)
            frame = self._transport.receive(deadline)
            self._check_received(deadline)
            return decode(frame)
        except BaseException:
            self._failed = True
            raise

    def send(self, message, deadline):
        try:
            self.check(deadline)
            self._transport.send(encode(message), deadline)
            self.check(deadline)
        except BaseException:
            self._failed = True
            raise


@dataclass(frozen=True)
class DescendantOffer:
    child: tuple
    process_slot: int
    thread_slot: int
    thread_id: int


class ControllerHandoff:
    """Single-operation state, called by the existing sole resume owner."""
    def __init__(self, binding, observer, native, deadline, *, root_channel=None, parentage=None, creation_audit=None,
                 clock=time.monotonic):
        if (type(binding) is not Binding or type(deadline) not in (int, float) or not isfinite(deadline)
                or type(observer) is not AuthenticatedChannel):
            raise HandoffError('Observer or deadline unavailable')
        self.binding = binding
        self.deadline = deadline
        self._observer = observer
        self._root_channel = root_channel
        self._parentage = parentage
        self._creation_audit = creation_audit
        self._native = native
        self._clock = clock
        self._job = None
        self._root = None
        self._offer = None
        self._released = False
        self._permits = {}
        self._observed = []
        self._failed = False
        self._authenticated = False

    def check_deadline(self):
        if self._failed or self._clock() >= self.deadline:
            raise HandoffError('Handoff operation expired or aborted')

    def abort(self):
        self._failed = True
        self._permits.clear()

    def check_peer(self):
        try:
            self.check_deadline()
            self._observer.check(self.deadline)
            if not self._authenticated:
                hello = self.binding.message('HELLO', 1)
                self.binding.fields(self._observer.exchange(hello, self.deadline), 'HELLO', 1, ())
                self._authenticated = True
        except BaseException:
            self.abort()
            raise

    def prepare(self, job, process, *, parent=None):
        self.check_deadline()
        native = self._native
        identity = generation(native.process_identity(process))
        if parent is None:
            if self._job is not None:
                raise HandoffError('Root handoff already attempted')
            self._job, self._root = job, identity
            kind, seq = 'ROOT_OBSERVED', 2
        else:
            if (self._job != job or self._offer is None or identity != self._offer.child
                    or self._root != parent or len(self._observed) != 1):
                raise HandoffError('Child handoff does not belong to operation')
            kind, seq = 'DESCENDANT_OBSERVED', 3
        if native.running(process) is not True or native.in_job(process, job) is not True:
            raise HandoffError('Process membership unavailable')
        self.check_peer()
        peer = self._observer.peer_handle
        # The observer retains the one query-only job duplicate for the operation.
        job_slot = native.duplicate_observation_job(job, peer) if parent is None else None
        process_slot = native.duplicate_observation_process(process, peer)
        if (not _handle_slot(process_slot) or process_slot == job_slot
                or (parent is None and not _handle_slot(job_slot))):
            raise HandoffError('Observation handle transfer unavailable')
        request = self.binding.message(
            'HANDLES', seq, process=list(identity), parent=list(parent) if parent else None,
            process_slot=process_slot, job_slot=job_slot,
        )
        fields = self.binding.fields(
            self._observer.exchange(request, self.deadline), kind, seq, ('process', 'parent', 'member'),
        )
        ack_parent = None if fields['parent'] is None else generation(fields['parent'])
        if (generation(fields['process']) != identity or ack_parent != parent
                or fields['member'] is not True):
            raise HandoffError('Observer acknowledgment mismatch')
        if parent is not None:
            released = self.binding.fields(
                self._root_channel.exchange(self.binding.message('TRANSFER_ACCEPTED', 2, child=list(identity)),
                                            self.deadline),
                'THREAD_RELEASED', 2, ('child',),
            )
            if generation(released['child']) != identity:
                raise HandoffError('Invalid child thread release')
            self._released = True
            self._root_channel.check(self.deadline)
        self.check_peer()
        self._observed.append(identity)
        if self.binding.scenario == 'reject_containment':
            raise HandoffError('Fixture rejects containment')
        permit = object()
        self._permits[permit] = (job, identity, parent)
        return permit, identity

    def consume(self, permit, job, identity, *, parent=None):
        """Local one-shot selection under the driver's resume/abort lock."""
        self.check_deadline()
        if (self._permits.pop(permit, None) != (job, identity, parent)
                or (parent is not None and not self._released)):
            raise HandoffError('Resume authorization unavailable')

    def claim_child(self, parent_handle, parent):
        self.check_deadline()
        if (self.binding.scenario != 'descendant' or self._root_channel is None
                or (self._parentage is None and self._creation_audit is None)
                or parent != self._root or self._offer is not None
                or self._native.running(parent_handle) is not True):
            raise HandoffError('Descendant offer unavailable')
        channel = self._root_channel(parent_handle, parent)
        if type(channel) is not AuthenticatedChannel or channel.expected.process != parent:
            raise HandoffError('Descendant channel belongs to a different root')
        fields = self.binding.fields(
            channel.exchange(self.binding.message('REQUEST_DESCENDANT', 1, parent=list(parent)), self.deadline),
            'DESCENDANT_OFFER', 1, ('parent', 'child', 'process_slot', 'thread_slot', 'thread_id'),
        )
        child = generation(fields['child'])
        slots = fields['process_slot'], fields['thread_slot']
        if (generation(fields['parent']) != parent or child[0] == parent[0] or slots[0] == slots[1]
                or not all(_handle_slot(slot) for slot in slots) or not _natural(fields['thread_id'], 2**32 - 1)):
            raise HandoffError('Invalid descendant offer')
        self._offer = DescendantOffer(child, *slots, fields['thread_id'])
        self._root_channel = channel
        return self._offer

    def verify_parentage(self, job, parent_handle, child_handle, parent, child):
        if self._creation_audit is not None:
            verified = self._creation_audit.verify(job, parent_handle, child_handle, parent, child, self.deadline)
        else:
            verified = self._parentage(parent, child, self.deadline)
        if verified is not True:
            raise HandoffError('Independent parentage unavailable')
        self.check_deadline()

    def confirm_parentage(self, job, parent_handle, child_handle, parent, child):
        if self._creation_audit is not None:
            self._creation_audit.confirm(job, parent_handle, child_handle, parent, child, self.deadline)
            # An audit call can consume the margin left by the last pipe exchange.
            self.check_peer()

    def observe_cleanup(self):
        # Cleanup evidence may be collected after the resume deadline, but
        # never creates a new permit. The outer VM deadline is independent.
        deadline = min(self.deadline + 4, self._clock() + 2)
        seq = len(self._observed) + 2
        if seq == 2 or (self.binding.scenario == 'descendant' and seq != 4):
            return False
        message = self._observer.exchange(self.binding.message('RELEASE', seq), deadline)
        fields = self.binding.fields(message, 'RELEASED', seq, ('exited', 'active'))
        return (type(fields['active']) is int and fields['active'] == 0
                and type(fields['exited']) is list
                and [generation(value) for value in fields['exited']] == self._observed
                and (self._creation_audit is None or self._creation_audit.finish(deadline) is True))


class ObserverHandoff:
    """Observer-side handler for target-local handles on an authenticated channel."""
    def __init__(self, binding, native, *, exact_job=False, creation_audit=None, resume_deadline=None):
        self._binding = binding
        self._native = native
        self._job = None
        self._processes = []
        self._closed = False
        self._authenticated = False
        self._exact_job = exact_job
        self._creation_audit = creation_audit
        self._resume_deadline = resume_deadline

    def serve_once(self, channel, deadline):
        try:
            response = self._handle(channel.receive(deadline), deadline=deadline)
            channel.send(response, deadline)
            return not self._closed
        except BaseException:
            self.close()
            raise

    def _handle(self, message, *, deadline=None):
        if self._closed:
            raise HandoffError('Observer handoff closed')
        if not self._authenticated:
            self._binding.fields(message, 'HELLO', 1, ())
            self._authenticated = True
            return self._binding.message('HELLO', 1)
        seq = len(self._processes) + 2
        if message.get('type') == 'RELEASE':
            self._binding.fields(message, 'RELEASE', seq, ())
            if self._job is None:
                raise HandoffError('Observer cleanup unavailable')
            active = self._native.active_processes(self._job)
            if type(active) is not int or active != 0:
                raise HandoffError('Observer cleanup unavailable')
            if self._exact_job and self._native.job_counts(self._job) != (len(self._processes), 0):
                raise HandoffError('Unexpected fixture process during cleanup')
            if self._creation_audit is not None and self._creation_audit.finish(deadline) is not True:
                raise HandoffError('Independent parentage cleanup unavailable')
            exited = []
            for identity, handle in self._processes:
                if self._native.running(handle) is not False or generation(self._native.process_identity(handle)) != identity:
                    raise HandoffError('Observer exit unverified')
                exited.append(list(identity))
            self.close()
            return self._binding.message('RELEASED', seq, exited=exited, active=0)
        fields = self._binding.fields(message, 'HANDLES', seq, ('process', 'parent', 'process_slot', 'job_slot'))
        if self._resume_deadline is not None and time.monotonic() >= self._resume_deadline:
            raise HandoffError('Observer resume deadline exceeded')
        identity = generation(fields['process'])
        handle, job = fields['process_slot'], fields['job_slot']
        if (seq > 3 or not _handle_slot(handle) or handle == self._job
                or any(handle == existing for _, existing in self._processes)):
            raise HandoffError('Unexpected observation handles')
        if seq == 2:
            if not _handle_slot(job) or job == handle or fields['parent'] is not None:
                raise HandoffError('Root observation unavailable')
            self._job = job
        elif (self._binding.scenario != 'descendant' or job is not None
              or generation(fields['parent']) != self._processes[0][0] or identity[0] == self._processes[0][0][0]):
            raise HandoffError('Unexpected descendant observation')
        self._processes.append((identity, handle))
        if (generation(self._native.process_identity(handle)) != identity
                or self._native.running(handle) is not True or self._native.in_job(handle, self._job) is not True
                or generation(self._native.process_identity(handle)) != identity
                or self._native.running(handle) is not True):
            raise HandoffError('Independent observation failed')
        if self._exact_job:
            expected = {pid for (pid, _), _ in self._processes}
            if (self._native.job_process_ids(self._job) != expected
                    or self._native.job_counts(self._job) != (len(expected), len(expected))):
                raise HandoffError('Observer job population mismatch')
        if seq == 3 and self._creation_audit is not None:
            parent, parent_handle = self._processes[0]
            if self._creation_audit.verify(self._job, parent_handle, handle, parent, identity,
                                           self._resume_deadline) is not True:
                raise HandoffError('Independent observer parentage unavailable')
        kind = 'ROOT_OBSERVED' if seq == 2 else 'DESCENDANT_OBSERVED'
        return self._binding.message(kind, seq, process=list(identity), parent=fields['parent'], member=True)

    def close(self):
        if self._closed:
            return
        self._closed = True
        failure = False
        for handle in [*(handle for _, handle in self._processes), self._job]:
            if handle is not None:
                try:
                    self._native.close(handle)
                except Exception:
                    failure = True
        if failure:
            raise HandoffError('Observer handle release unverified')


class CooperativeDescendant:
    """Fixture-side offer and thread release. The fixture never resumes a child."""
    def __init__(self, binding, parent, native, create_child):
        if binding.scenario != 'descendant':
            raise HandoffError('Unexpected descendant scenario')
        self._binding = binding
        self._parent = generation(parent)
        self._native = native
        self._create_child = create_child
        self._used = False

    def run(self, channel, deadline):
        if self._used:
            raise HandoffError('Descendant already requested')
        self._used = True
        offer = self._binding.fields(channel.receive(deadline), 'REQUEST_DESCENDANT', 1, ('parent',))
        if generation(offer['parent']) != self._parent:
            raise HandoffError('Fixture parent mismatch')
        process, thread = self._create_child()
        try:
            child = generation(self._native.process_identity(process))
            pid, thread_id = self._native.thread_identity(thread)
            if (pid != child[0] or not _natural(thread_id, 2**32 - 1)
                    or not _handle_slot(process) or not _handle_slot(thread) or process == thread):
                raise HandoffError('Fixture thread mismatch')
            channel.send(self._binding.message('DESCENDANT_OFFER', 1, parent=list(self._parent), child=list(child),
                                               process_slot=process, thread_slot=thread, thread_id=thread_id),
                         deadline)
            accepted = self._binding.fields(channel.receive(deadline), 'TRANSFER_ACCEPTED', 2, ('child',))
            if generation(accepted['child']) != child:
                raise HandoffError('Fixture transfer mismatch')
            releasing, thread = thread, None
            self._native.close(releasing)
            channel.send(self._binding.message('THREAD_RELEASED', 2, child=list(child)), deadline)
        finally:
            try:
                if thread is not None:
                    self._native.close(thread)
            finally:
                self._native.close(process)
