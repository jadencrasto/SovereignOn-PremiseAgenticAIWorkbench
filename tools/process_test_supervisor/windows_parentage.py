"""Read-only creator evidence for the fixed, non-breakaway Windows fixture."""
import ctypes as C
from ctypes import wintypes as W
from contextlib import ExitStack
from dataclasses import dataclass
from datetime import datetime
from math import ceil
import os
import re
import time
from xml.etree import ElementTree

from tools.process_test_supervisor.handoff import HandoffError, generation
from tools.process_test_supervisor.windows_pipe import _bind, _check


_NS = {'e': 'http://schemas.microsoft.com/win/2004/08/events/event'}
_PROVIDER = 'Microsoft-Windows-Security-Auditing'
_AUDIT_DISRUPTION = {1100, 1101, 1102, 1104, 1108, 4719}
_STAMP = re.compile(r'(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(?:\.(\d{1,7}))?Z')
_EPOCH = datetime(1601, 1, 1)
_MAX_RECORDS = 1024
_PAGE_RECORDS = 32
_MAX_WAIT_MS = 25
_MAX_XML_BYTES = 128 * 1024
_MAX_PROVIDER_CHARS = 256


@dataclass(frozen=True, slots=True)
class Record:
    number: int
    code: int
    provider: str
    created: int
    creator: int | None = None
    child: int | None = None
    # Only relevant creation evidence is retained, never command lines.


def _filetime(text):
    stamp = _STAMP.fullmatch(text or '')
    if stamp is None:
        raise HandoffError('Invalid Windows audit timestamp')
    delta = datetime.fromisoformat(stamp[1]) - _EPOCH
    return (delta.days * 86400 + delta.seconds) * 10**7 + int((stamp[2] or '').ljust(7, '0'))


def audit_record(xml):
    if type(xml) is not str or not 0 < len(xml) <= _MAX_XML_BYTES // 2 or '<!' in xml:
        raise HandoffError('Invalid Windows audit record')
    root = ElementTree.fromstring(xml)
    system = root.find('e:System', _NS)
    if system is None or system.findtext('e:Channel', namespaces=_NS) != 'Security':
        raise HandoffError('Unexpected Windows audit channel')
    row = int(system.findtext('e:EventRecordID', namespaces=_NS))
    code = int(system.findtext('e:EventID', namespaces=_NS))
    provider = system.find('e:Provider', _NS).get('Name')
    stamp = _filetime(system.find('e:TimeCreated', _NS).get('SystemTime'))
    if row <= 0 or stamp <= 0 or not provider or len(provider) > _MAX_PROVIDER_CHARS:
        raise HandoffError('Windows audit identity missing')
    if code != 4688:
        return Record(row, code, provider, stamp)
    if provider != _PROVIDER:
        raise HandoffError('Unexpected process audit provider')
    pids = {}
    for entry in root.findall('e:EventData/e:Data', _NS):
        name = entry.get('Name')
        if name in ('NewProcessId', 'ProcessId'):
            text = entry.text or ''
            if name in pids or not re.fullmatch(r'0x[0-9a-fA-F]{1,8}', text):
                raise HandoffError('Invalid process audit PID')
            pids[name] = int(text, 16)
    if len(pids) != 2 or not pids['NewProcessId']:
        raise HandoffError('Process audit evidence incomplete')
    return Record(row, code, provider, stamp, pids['ProcessId'], pids['NewProcessId'])


class WindowsProcessAudit:
    """Bounded local Security channel reads. No audit policy changes."""
    def __init__(self, *, clock=time.monotonic):
        if os.name != 'nt':
            raise HandoffError('Windows process audit unavailable')
        self._evt = evt = C.WinDLL('wevtapi.dll', use_last_error=True, winmode=0x800)
        self._clock = clock
        p_handle, p_dword = C.POINTER(W.HANDLE), C.POINTER(W.DWORD)
        _bind(evt, 'EvtQuery', W.HANDLE, W.HANDLE, W.LPCWSTR, W.LPCWSTR, W.DWORD)
        _bind(evt, 'EvtNext', W.BOOL, W.HANDLE, W.DWORD, p_handle, W.DWORD, W.DWORD, p_dword)
        _bind(evt, 'EvtRender', W.BOOL, W.HANDLE, W.HANDLE, W.DWORD, W.DWORD, W.LPVOID, p_dword, p_dword)
        _bind(evt, 'EvtClose', W.BOOL, W.HANDLE)

    def _render(self, handle):
        required, props = W.DWORD(), W.DWORD()
        result = self._evt.EvtRender(None, handle, 1, 0, None, C.byref(required), C.byref(props))
        if result or C.get_last_error() != 122 or not 0 < required.value <= _MAX_XML_BYTES:
            raise HandoffError('Windows audit XML size unavailable')
        size = required.value
        width = C.sizeof(C.c_wchar)
        if size % width:
            raise HandoffError('Windows audit XML layout invalid')
        output = C.create_unicode_buffer(size // width)
        _check(self._evt.EvtRender(None, handle, 1, size, output, C.byref(required), C.byref(props))
               and 0 < required.value <= size, 'Windows audit XML unavailable')
        return audit_record(output.value)

    def _remaining(self, deadline):
        remaining = deadline - self._clock()
        if not remaining > 0:
            raise HandoffError('Process creation audit deadline exceeded')
        return remaining

    def _close(self, handle):
        _check(self._evt.EvtClose(handle), 'Windows audit handle close failed')

    def _rows(self, xpath, deadline, *, reverse=False, maximum):
        self._remaining(deadline)
        query = self._evt.EvtQuery(None, 'Security', xpath, 1 | (0x200 if reverse else 0x100))
        _check(query, 'Windows process audit read denied')
        rows = []
        try:
            while len(rows) < maximum:
                size = min(_PAGE_RECORDS, maximum - len(rows))
                items, returned = (W.HANDLE * size)(), W.DWORD()
                wait_ms = ceil(min(_MAX_WAIT_MS, self._remaining(deadline) * 1000))
                result = self._evt.EvtNext(query, size, items, wait_ms, 0, C.byref(returned))
                error = 0 if result else C.get_last_error()
                with ExitStack() as page:
                    handles = {handle for handle in items if handle}
                    for handle in handles:
                        page.callback(self._close, handle)
                    self._remaining(deadline)
                    if not result:
                        if error == 259 and not handles and returned.value == 0:
                            break
                        raise OSError(error, 'Windows process audit read failed')
                    count = returned.value
                    if not 0 < count <= size or len(handles) != count or not all(items[:count]):
                        raise HandoffError('Windows audit page incomplete')
                    for handle in items[:count]:
                        self._remaining(deadline)
                        rows.append(self._render(handle))
                        self._remaining(deadline)
        finally:
            self._close(query)
        self._remaining(deadline)
        return tuple(rows)

    def latest(self, deadline):
        rows = self._rows('*', deadline, reverse=True, maximum=1)
        if len(rows) != 1:
            raise HandoffError('Windows audit watermark unavailable')
        return rows[0]

    def since(self, number, deadline):
        if type(number) is not int or not 0 < number <= 2**64 - 1:
            raise HandoffError('Invalid Windows audit watermark')
        # Keep every channel record in the sequence and read one extra to detect truncation.
        return self._rows(f'*[System[EventRecordID >= {number}]]', deadline, maximum=_MAX_RECORDS + 1)


class ParentageEvidence:
    """Bind a new 4688 event to two still-retained generations and a private job.

    The cursor is taken before any workload is created. Job lifetime accounting
    independently detects unexpected assigned processes that already exited.
    The trusted fixture is deliberately limited to one direct child.
    """
    def __init__(self, audit, native, controller, deadline, *, clock=time.monotonic, sleep=time.sleep):
        self._audit, self._native = audit, native
        self._clock, self._sleep = clock, sleep
        self._anchor = audit.latest(deadline)
        self._controller = generation(controller)
        self._accepted = None

    def _records(self, deadline):
        if self._clock() >= deadline:
            raise HandoffError('Process creation audit deadline exceeded')
        records = self._audit.since(self._anchor.number, deadline)
        if len(records) > _MAX_RECORDS:
            raise HandoffError('Windows audit record budget exceeded')
        if (not records or records[0] != self._anchor
                or any(row.number != self._anchor.number + offset
                       or (offset and row.code in _AUDIT_DISRUPTION)
                       for offset, row in enumerate(records))):
            raise HandoffError('Windows audit continuity lost')
        if self._clock() >= deadline:
            raise HandoffError('Process creation audit deadline exceeded')
        return records[1:]

    def _retained(self, job, handles, identities):
        native = self._native
        expected_pids = {pid for pid, _ in identities}
        if (native.job_process_ids(job) != expected_pids
                or any(native.running(handle) is not True
                       or generation(native.process_identity(handle)) != identity
                       or native.in_job(handle, job) is not True
                       for handle, identity in zip(handles, identities, strict=True))
                or native.job_counts(job) != (len(identities), len(identities))):
            raise HandoffError('Unexpected fixture job population')

    def verify(self, job, parent_handle, child_handle, parent, child, deadline):
        if self._accepted is not None or parent[0] == child[0] or not self._anchor.created < parent[1] < child[1]:
            raise HandoffError('Unexpected fixture process creation order')
        handles, identities = (parent_handle, child_handle), (parent, child)
        while self._clock() < deadline:
            self._retained(job, handles, identities)
            records = self._records(deadline)
            parent_events = [row for row in records if row.child == parent[0] and row.created >= parent[1]]
            child_events = [row for row in records if row.child == child[0] and row.created >= child[1]]
            offers = [row for row in records if row.creator == parent[0] and row.created >= parent[1]]
            if (len(parent_events) > 1 or len(child_events) > 1 or len(offers) > 1
                    or (offers and offers[0] not in child_events)
                    or (child_events and child_events[0].creator != parent[0])):
                raise HandoffError('Unexpected process creator evidence')
            if parent_events and child_events:
                if (parent_events[0].creator != self._controller[0]
                        or not self._controller[1] < parent[1]
                        or parent_events[0].number >= child_events[0].number
                        or parent_events[0].created >= child[1]):
                    raise HandoffError('Unexpected audit creation order')
                self._retained(job, handles, identities)
                if self._clock() >= deadline:
                    break
                self._accepted = parent_events[0], child_events[0]
                return True
            self._sleep(min(0.025, max(0, deadline - self._clock())))
        raise HandoffError('Process creation audit deadline exceeded')

    def finish(self, deadline):
        if self._accepted is None:
            return False
        parent, child = self._accepted
        records = self._records(deadline)
        return (parent in records and child in records
                and [row for row in records if row.creator == child.creator and row.created >= parent.created] == [child]
                and all(row.creator != child.child or row.created < child.created for row in records)
                and self._clock() < deadline)

    def confirm(self, job, parent_handle, child_handle, parent, child, deadline):
        if self.finish(deadline) is not True:
            raise HandoffError('Windows parentage evidence changed')
        self._retained(job, (parent_handle, child_handle), (parent, child))
