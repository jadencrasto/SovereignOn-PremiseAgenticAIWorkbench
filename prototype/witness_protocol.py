"""In-memory, non-authorizing witness protocol model. No OS or network trust."""
import hashlib
import hmac
import json
import re

OPS = frozenset(('issue', 'consume', 'revoke', 'expire', 'inspect'))
IDENT = re.compile(r'^[a-z][a-z0-9_]{0,31}$')


def encode(value):
    """Canonical ASCII JSON for this restricted, versioned synthetic schema."""
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=True, allow_nan=False).encode('ascii')


class ProtocolError(ValueError):
    pass


class SyntheticWitness:
    def __init__(self, key, epoch=1):
        if not isinstance(key, bytes) or len(key) < 16 or type(epoch) is not int or epoch < 1:
            raise ProtocolError('invalid_configuration')
        self._key = key
        self.epoch = epoch
        self.sequence = 0
        self.head = '0' * 64
        self.states = {}
        self.seen = set()
        self.history = []

    def sign(self, value):
        return hmac.new(self._key, encode(value), hashlib.sha256).hexdigest()

    def process(self, request, mac):
        if type(request) is not dict or set(request) != {'version', 'request_id', 'approval_id', 'operation', 'sequence', 'epoch'}:
            raise ProtocolError('malformed')
        if not isinstance(mac, str) or re.fullmatch(r'[0-9a-f]{64}', mac) is None or not hmac.compare_digest(self.sign(request), mac):
            raise ProtocolError('authentication_failed')
        if (type(request['version']) is not int or request['version'] != 1 or
            type(request['sequence']) is not int or request['sequence'] < 0 or
            type(request['epoch']) is not int or request['epoch'] != self.epoch):
            raise ProtocolError('epoch_or_format')
        for field in ('request_id', 'approval_id'):
            if not isinstance(request[field], str) or IDENT.fullmatch(request[field]) is None:
                raise ProtocolError('malformed')
        operation = request['operation']
        if not isinstance(operation, str) or operation not in OPS:
            raise ProtocolError('malformed')
        if request['request_id'] in self.seen:
            raise ProtocolError('replay')
        if request['sequence'] != self.sequence:
            raise ProtocolError('sequence_conflict')
        approval = request['approval_id']
        previous = self.states.get(approval)
        if operation == 'issue':
            if previous is not None: raise ProtocolError('invalid_transition')
            state = 'issued'
        elif operation in ('consume', 'expire'):
            if previous != 'issued': raise ProtocolError('invalid_transition')
            state = {'consume':'consumed', 'expire':'expired'}[operation]
        elif operation == 'revoke':
            if previous not in ('issued', 'consumed'): raise ProtocolError('invalid_transition')
            state = 'revoked' if previous == 'issued' else 'consumed_revoked'
        else:
            state = previous or 'absent'
        self.sequence += 1
        response = {'version':1, 'request_id':request['request_id'], 'approval_id':approval,
                    'operation':operation, 'sequence':self.sequence, 'epoch':self.epoch,
                    'state':state, 'previous_head':self.head}
        self.head = hashlib.sha256(encode(response)).hexdigest()
        response['head'] = self.head
        self.seen.add(request['request_id'])
        if operation != 'inspect': self.states[approval] = state
        self.history.append(response.copy())
        return response, self.sign(response)


class SyntheticClient:
    """Models local intent/commit; always denies operational authorization."""
    def __init__(self, key, epoch=1):
        self._key = key
        self.epoch = epoch
        self.sequence = 0
        self.head = '0' * 64
        self.pending = None
        self.phase = 'CLEAN'
        self.states = {}

    def _sign(self, value):
        return hmac.new(self._key, encode(value), hashlib.sha256).hexdigest()

    def begin(self, request_id, approval_id, operation):
        if self.phase != 'CLEAN': raise ProtocolError('quarantined')
        self.pending = {'version':1, 'request_id':request_id, 'approval_id':approval_id,
                        'operation':operation, 'sequence':self.sequence, 'epoch':self.epoch}
        self.phase = 'INTENT'
        return self.pending.copy(), self._sign(self.pending)

    def accept(self, response, mac, simulated_anchor):
        try:
            expected = {'version', 'request_id', 'approval_id', 'operation', 'sequence', 'epoch', 'state', 'previous_head', 'head'}
            if type(response) is not dict or set(response) != expected or not isinstance(mac, str) or re.fullmatch(r'[0-9a-f]{64}', mac) is None or not hmac.compare_digest(self._sign(response), mac):
                raise ProtocolError('invalid_response')
            unsigned = dict(response); head = unsigned.pop('head')
            if (self.phase != 'INTENT' or self.pending is None or
                any(response[k] != self.pending[k] for k in ('version','request_id','approval_id','operation','epoch')) or
                type(response['sequence']) is not int or response['sequence'] != self.sequence + 1 or
                response['previous_head'] != self.head or head != hashlib.sha256(encode(unsigned)).hexdigest() or
                simulated_anchor != (response['epoch'], response['sequence'], head)):
                raise ProtocolError('stale_or_conflicting_response')
            self.sequence, self.head = response['sequence'], head
            if response['operation'] != 'inspect':
                self.states[response['approval_id']] = response['state']
            self.pending = None
            self.phase = 'CLEAN'
            return 'RECORDED_UNAUTHORIZED'
        except (ProtocolError, TypeError, ValueError, KeyError):
            self.phase = 'QUARANTINED'
            raise ProtocolError('invalid_or_stale_response')

    def interrupted(self):
        self.phase = 'QUARANTINED'

    def reconcile(self, witness, simulated_anchor):
        """Compare independent-looking simulated snapshots; never authorize."""
        if witness is None or type(simulated_anchor) is not tuple or len(simulated_anchor) != 3:
            self.phase = 'QUARANTINED'; return 'QUARANTINED'
        if (simulated_anchor != (witness.epoch, witness.sequence, witness.head) or
            self.epoch != witness.epoch or self.sequence != witness.sequence or self.head != witness.head or
            self.pending is not None or self.states != witness.states):
            self.phase = 'QUARANTINED'; return 'QUARANTINED'
        self.phase = 'CLEAN'
        return 'CONSISTENT_UNAUTHORIZED'

    def external_effect(self):
        self.phase = 'QUARANTINED'
        return 'EFFECT_UNSUPPORTED'

    def authorized(self):
        return False
