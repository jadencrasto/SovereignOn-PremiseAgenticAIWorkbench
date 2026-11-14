# Post-Lifecycle evidence ledger

Production **NO-GO**. This ledger records available evidence as of this review. No tests, builds, VM access or fresh hashing were performed. Hashes identify bytes only when tied to the exact artifact and a separately established trust anchor; self-generated package inventories and development signatures are not independent approval.

| Evidence | Exact available value / outcome | Evidence class and limitation |
| --- | --- | --- |
| VM | Operator identifies `SovereignAI-Offline-Test`, Windows 10 Home x64, computer `DESKTOP-MPICP4M`, UUID `2b64d146-54ef-497c-be9b-9be0eb9b1a01`, VirtualBox; powered off; no network adapters/shared clipboard/drag-and-drop/shared folders/USB. | Operator-reported; VM settings, OS build and current power state not inspected in this review. |
| Lifecycle | Service PASS, synthetic Recovery PASS, elevated preflight PASS; no snapshot reversion needed after the passing runs. | Operator-reported; original full logs, exact execution time, scenario counts and binding to exact ISO/binary hashes are missing. A reported PASS is not a new test result here. |
| Snapshots | `Lifecycle-Configured-PreNative-20261112` and `Lifecycle-Diagnostics-PreNative-20261112` were previously named as restore points. Later operator reports obsolete snapshots removed and reusable snapshots retained. VM storage decreased from 50.46 GB to 22.77 GB. | Historical snapshot references only; the current snapshot names and retention of either named snapshot are **not verified**. No hypervisor inspection here. |
| Development signing certificate | SHA-1 thumbprint `41EC88510A662567E8B7E7C2C76E967F9261E2C6`; operator reports VM development trust and successful package/signature checks. | Thumbprint identifies a certificate, not the launcher, build or independent publisher approval. Current cert chain, SHA-256 public certificate fingerprint, status/time and passing run's exact signer are not independently established here. |
| Earlier transfer ISO, operator build | User supplied ISO SHA-256 `3e25ff6c5fc966a82fe2f428de81c5ce42626d481dc8cd140dc15aa9dd8ade67`; inventory SHA-256 `28826b13ee91c2ccc6c4414d942b0c4a0d95859b45cd887e2e43d61669fdf132`; 39 entries. | Operator-reported for an earlier O.1-era package. No evidence ties it to the later passing Service/Recovery execution. |
| Earlier local review ISO | `SovereignAI-Native-Security-Tests-verified.iso`: recorded ISO SHA-256 `538ea678b968ed506730a4bd8b4c0fbf7d85c2b254de910d01da3292fc44e5fa`, inventory SHA-256 `28826b13ee91c2ccc6c4414d942b0c4a0d95859b45cd887e2e43d61669fdf132`. | Builder report says it validated 39 files against its inventory and build inputs. Historical automated consistency evidence; not independently pinned and not known to be the passing VM package. `docs/audit/phase1_5/BATCH2B3O_NATIVE_VM_PACKAGE_REPORT.md`. |
| Earlier signed O.1 image | `SovereignAI-Native-Security-Tests-20261112T155732Z-8e038ab5.iso`: historical local validator reported ISO `e564485b7de2491755ca383e0e843aa99b48836e438a64ce0925c54016f12169`, inventory `19edbeedaa18abe03c9387356bf4ae391365fd067514a78b4b17bd5618d0f949`, bootstrap manifest `3cb2b1d622d3b8f9b954b74721ec57ca179c3b9ce8825bf585e08b945a7e1f37`. | The previous turn ran inventory validation of that historical file, not this review. It demonstrates internal consistency only, does not independently authenticate publisher or prove this image was used for the later passing run. |
| Passing ISO/launcher | Operator-supplied corrected ISO SHA-256 `00be0f929dd58a18f016509318143edceaf51f754539370294db98b67464ebe3`; launcher SHA-256 `07762f074edfb0fc5379f02230af863554b03d58cb1cfa0ce951692b6fa6eaee`. Operator reports both passing Service and Recovery runs used this package. | **Operator-reported build values and run linkage**, not freshly hashed, independently authenticated or verified against original build output in this review. Exact ISO filename, eight binary hashes, payload/inventory digest, original output and independently attested package-to-run chain remain missing. |
| Earlier J.4 failure | `initialization_pending`, START_PENDING=2, STOP returned 1052; prior runner expected 1061. Fixed test checks both state observations and final error code. Operator subsequently reports Service PASS. | Failure observations and mock checks are recorded in `docs/audit/phase1_5/BATCH2B3O3_J4_START_PENDING_FIX_REPORT.md`; exact successful retry log and hash are not available in this review. |
| Chroma offline write/restart | Prior operator reported synthetic ChromaDB 1.5.9 write/read in a separate process and again after Windows 11 VM restart; network-attempt capture unresolved. | Operator-reported; distinct VM/test from Lifecycle. Does not prove application authorization, reliable egress blocking or immutable model identity. |

## Historical artifact hashes from the earlier O.1 validator

These eight hashes were printed by the prior validation of the `20261112T155732Z-8e038ab5` image, **not** established as the hashes used in the passing Lifecycle run:

| File | SHA-256 |
| --- | --- |
| ServiceFoundation.exe | `60d72854e5786e7e154a520834e5edc9e2026bbdfdcd76a606a3f211978f7c80` |
| ServiceFoundation.dll | `b7aa039922446ce6250f156aafea7a5cb6f5f63ca26302fcc2dfc92d53f1a77d` |
| ServiceFoundation.deps.json | `a8b36a10e44f8709374b50fbbe7297fd767789490ce0ab3db5457adb1b0ff1f5` |
| ServiceFoundation.runtimeconfig.json | `256dda2706e1151bb95987b6b5be610bf68d64e058285b19e7e958222fc90414` |
| SecurityFoundation.exe | `523c81a7f93b914e7af840c6c928c964c615f5d98ff4c67616daf13a0160c649` |
| SecurityFoundation.dll | `e0c287fe9f4cbad8c6eac73910062f71023addf0d83e8cca2913ee2272f6aefe` |
| SecurityFoundation.deps.json | `6f19b9181cbee47843085715a5800bbc56b31be8115f2096f6a66d3789033877` |
| SecurityFoundation.runtimeconfig.json | `256dda2706e1151bb95987b6b5be610bf68d64e058285b19e7e958222fc90414` |

## Evidence to collect without touching the VM now

Locate the original host build output and operator screenshots/logs for the **passing** run: `CREATED`, ISO and launcher hash confirmation, inventory and eight artifact hashes; full Service and Recovery summaries; VM Windows build, timestamp and snapshot references; signer SHA-256 and signature-validation result. Record whether service removal was actually acknowledged. Match each evidence item to the same package and run; do not infer that linkage from a similar filename. No original passing screenshots or logs were inspected in this review. Ask the operator to provide already available records; do not restart the VM just to populate this ledger. Separate independent security review and production authorization remain absent.
