# R7.1 — Frontend authorization package

**Selected first operation: read-only dependency and executable provenance verification. Decision: BLOCKED BY DEPENDENCY PROVENANCE.** The authorization package is prepared; no frontend execution is authorized by it. Starting branch `main`, HEAD `2b96fee1f24777247f4efb7a18293eef4275e9cb`; existing uncommitted changes retained. R6.6's historical approval-stream discrepancy was subsequently corrected in R6.7; the existing frontend fixtures remain unexecuted.

This operation is selected because installed metadata matching `package-lock.json` does not prove the bytes of Node, the test harness, TypeScript or transitive packages. A type-check, unit test, build or browser run would execute unverified code. No frontend executable is needed for this first operation.

## Exact proposed operation — NOT APPROVED OR EXECUTED

On the approved isolated Windows host, after the operator has reviewed the manifest and confirmed the paths, from `C:\SovereignReview`:

```powershell
Get-FileHash -Algorithm SHA256 -LiteralPath (Get-Content -LiteralPath 'C:\SovereignReview\approved-files.txt')
```

This is one read-only **hash-collection operation**, not an automated integrity verdict. The independently supplied `approved-files.txt` must contain only reviewed absolute file paths under the disposable review workspace and the separately reviewed PowerShell executable; paths must be validated for containment and reparse points before use. Its complete, independently trusted signed expected-hash manifest must be supplied separately. The operator must compare **every** output to that manifest, verify source and manifest authenticity, enumerate and reject unexpected or missing files and links using independently reviewed procedures, and confirm the list covers the entire approved dependency closure. The command alone cannot detect omitted or extra files, prove manifest trust, detect a change between hashing and later execution, or establish network isolation. Any unresolved link, file change, read failure or missing file makes the result inconclusive; it must not be used to approve execution. Freeze or otherwise protect the reviewed snapshot during comparison and subsequent use.

The proposed executable is `C:\Program Files\PowerShell\7\pwsh.exe` **only if present and independently reviewed on the selected host**; its version and hash are not established by this report. Run the command in an already operator-started approved PowerShell session; no child process is required by the command itself. `Get-Content` and `Get-FileHash` are PowerShell commands and use the reviewed PowerShell/.NET runtime. The command reads the approved list and the listed files and writes hashes to stdout; it does not intentionally write repository files or contact a registry. Output capture, PowerShell profile/module autoload, host telemetry and OS behavior require review and independent enforcement; do not assume the command text proves zero network activity. Use a clean profile and controlled environment approved by the operator, without changing execution policy. No credentials, protected documents or production data belong in the workspace or manifest.

## Inventory and prerequisites

The independently reviewed manifest must cover the actual intended future scope: `node.exe`, its required runtime libraries, `frontend/package.json`, `package-lock.json`, the exact source and test snapshot, every installed package file used by that later operation, relevant native binaries, and the hash-collection executable/runtime. For potential later SSE fixture testing, `frontend/tests/a2-stream-client.test.cjs` loads the TypeScript compiler and production TypeScript modules; R6.5 lists TypeScript 6.0.3 and Node file version 22.20.0.0, but the full required dependency closure must be determined and reviewed by the operator. These are **not** authorization to execute either tool. For type-check/build, the broader lockfile and platform-specific closure would need a separate review and authorization.

| Prerequisite | Classification / evidence |
| --- | --- |
| Current branch and HEAD | VERIFIED locally by read-only Git inspection; uncommitted source is not represented by HEAD alone. |
| `frontend/package-lock.json` | PRESENT; local SHA-256 `d4d92726d2c75bfe742f71bdc1546403064cb493d0c1755055b3e7c50b25e854`, matching R6.5. Not independently authenticated. |
| Installed packages and Node | PRESENT BUT UNVERIFIED; R6.5 reports matching package metadata, not file integrity. Node was observed at `C:\Program Files\nodejs\node.exe`, file version 22.20.0.0; its binary hash and origin remain unverified. |
| Approved complete signed expected-hash manifest, file list and dependency closure | MISSING. No executable hash or external provenance is invented here. |
| Approved disposable workspace, non-administrative account, no production data, controlled read-only inputs and writable output | MISSING / not independently verified. R6.5 does not establish these. |
| Independently enforced and monitored outbound network denial, including loopback policy | MISSING / not independently verified; do not modify host firewall or networking to improvise it. |
| Independent process ownership, external deadline and cleanup authority | MISSING / not independently verified. R5.5 native supervisor remains unverified; do not use it as proof. |
| Operation-specific operator/security approval | REQUIRES EXTERNAL AUTHORIZATION. The previous frontend build denial remains in force. |

## Operator decision and execution contract

The operator and security reviewer should record: (1) names and approval date; (2) approved host/environment and non-admin account; (3) independently authenticated manifest identity and its signer; (4) exact reviewed PowerShell binary and runtime hashes/version; (5) reviewed absolute source/dependency paths and immutable snapshot identity; (6) monitored network-denial and loopback controls; (7) approved output/temporary directory; (8) external deadline and process-tree cleanup owner; and (9) explicit approval for **this hash-collection command only**. No supplied organizational control or approved environment has been verified in this repository.

Proposed maximum duration is **120 seconds**, subject to operator review of file count and size; an independent outer controller must enforce the deadline and terminate only owned processes. If that cannot be established, do not execute. The command has no intended disk writes; if the operator captures stdout/stderr, write only to an approved disposable output directory, retain the bounded evidence without secrets and verify cleanup. A trusted external authority must identify the PowerShell process and confirm its exit; failure to confirm cleanup is inconclusive. Do not start a VM, change host security/network policy, install or resolve dependencies, run npm/npx/pnpm/yarn, TypeScript, Vite, tests, browser, application, Ollama or Chroma under this approval.

**PASS** requires successful collection and independently reviewed comparison of the complete immutable inventory with a trusted manifest, no unexpected or missing entries, approved isolation and verified cleanup. **FAIL** means a confirmed hash/identity mismatch or prohibited activity. **INCONCLUSIVE** means incomplete inventory, changed files, read errors, timeout, uncertain isolation or cleanup. **BLOCKED** means an approval, manifest, environment or control is absent. Hash collection alone is never a PASS for dependency provenance or authorization to run frontend code.

**One next action:** the operator/security reviewer must provide an independently authenticated manifest and select an approved isolated environment with a verified deadline and cleanup authority for this read-only hash collection.
