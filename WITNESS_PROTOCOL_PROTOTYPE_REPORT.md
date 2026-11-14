# Independent freshness witness protocol prototype

**Simulation only. Production NO-GO.** No approval, task, document, artifact or execution gate is changed. The prototype does not start a server, access production state, use real identities or establish an independent freshness authority.

## Files and state model

- `prototype/witness_protocol.py`: in-memory synthetic witness/client with distinct request IDs, approval IDs, sequence and epoch. Messages use a fixed schema and sorted compact ASCII JSON. Synthetic HMAC authenticates requests/responses with a test-supplied shared key. Each accepted operation advances a sequence and hash-linked response head; repeat request IDs, bad authentication, invalid states, stale sequence and unknown epochs fail. Issue -> issued; consume/expire -> consumed/expired; revoke -> revoked, or a protective `consumed_revoked` after consume. Inspection is non-authorizing and also advances the model sequence.
- Synthetic client models `CLEAN`, `INTENT` and `QUARANTINED`. It requires a correctly authenticated response, matching request/epoch/sequence/prior head and caller-supplied simulated current anchor. Missing/mismatched witness or anchor, differing histories and unresolved intents quarantine; a consistent reconciliation is `CONSISTENT_UNAUTHORIZED`. External effect requests are unsupported and quarantine. `authorized()` always returns false. Lost acknowledgements do not cause automatic local repair or execution.
- `tests/backend/test_witness_protocol_offline.py`: in-memory deterministic synthetic cases only.

No durable transaction, independent process, real network loss, actual crash, key isolation, trusted time or native security boundary is modeled. Failure injection changes model state rather than crashing a process. The fixture cannot safely authorize even when it reports consistency. Synthetic HMAC uses a shared in-process key available to both simulated roles; it is not independently provisioned authentication or role separation. Sequences, history and the supplied anchor all live in memory and can be rolled back together. An attacker controlling both witness and anchor cannot be detected. The prototype does not authenticate the supplied anchor or protect it from concurrent changes; a delayed reply is only detected when the test supplies a newer simulated anchor. A compromised holder of the shared key can forge requests and responses. It is not a complete interoperable wire specification or an implementation of the production architecture.

## Results

External pytest plugin autoload disabled. Direct reviewed standard-library tests used `python -I -B`, no network or services.

| Command | Passed | Failed | Skipped |
| --- | ---: | ---: | ---: |
| `python -I -B tests/backend/test_witness_protocol_offline.py` | 12 | 0 | 0 |
| `python -I -B tests/backend/test_freshness_assessment_offline.py` | 2 | 0 | 0 |

Tests cover normal synthetic transitions, duplicate/replayed requests, stale sequence, unknown epoch, bad authentication, conflicting history, rollback of either simulated side, missing witness/anchor, delayed acknowledgement, synthetic interruption and unsupported external effects. The existing freshness regression uses an actual temporary SQLite backup/restore to show low-level approval replay after rollback; this does not authorize a task.

## Required work before any operational witness

Independently provision an authenticated, separately administered witness and protected high-water mark outside the SQLite restore domain; protect identities, keys, executable/configuration and Windows storage handles/ACLs. Define explicit actor-specific authorization, issuance assertions, trusted time and expiry, durable CAS/flush, authenticated challenge-bound freshness, concurrent writers, revocation release barriers, key rotation, crash ordering and audited disaster recovery. Verify all actual protocol states, mismatched/rollbacked witness and checkpoint, process termination and external effect uncertainty under separately approved isolated conditions. Exactly-once effects cannot be assumed. Existing egress and protected-service gates remain blocked. Independent security and organizational approval are required before production integration.
