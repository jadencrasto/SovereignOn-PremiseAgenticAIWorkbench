# Batch 2B.3H.10 — Ping response diagnostic

## Finding and status

The actual response behind the operator's `unexpected_ping` is **not established**: the previous runner discarded its values. The observed result means that the client read a newline-terminated response and exited zero, and that PowerShell accepted JSON whose version and development/trust flags passed its checks. The subsequent ping check saw `ok` as false-like, or `result` different from `pong`. This does not prove which one, nor that the peer check produced `DENIED`. A client exit code of zero in that version did not validate the JSON or indicate `ok=true`.

The server's expected successful ping is `version=1`, `ok=true`, `development_only=true`, `trusted_broker=false`, `result=pong`, `error=null`. Its peer-name comparison emits `ok=false`, `result=null`, `error=DENIED` when names differ. Other malformed or unexpected outputs, including PowerShell value interpretation, remain possible; the old output cannot identify encoding, framing, extra output, or the exact mismatch. `broker_exit_code=-1` was printed after the runner terminated the still-running broker during failure cleanup, so it is not evidence that the broker exited spontaneously.

## Changes

* `prototype/windows_broker_dotnet/Program.cs`: the client now validates a complete JSON response against the fixed six-field schema, types, flags and known response values, rejects duplicate properties and unexpected fields, and serializes a sanitized validated response. Exit 0 means a valid positive response; exit 3 means a valid structured negative response, including expected denial test scenarios; exit 1 means transport or protocol failure. Exit 3 is an explicit protocol denial, not a successful authorization. The `stall` and `disconnect` synthetic scenarios intentionally return after their test action without claiming a response.
* `tests/backend/run_batch2b3h8_dotnet_ipc.ps1`: reports whether its JSON parse succeeded and prints only allowlisted response values: version, ok, development and trusted flags, known result or error code. Unknown values are rendered `unrecognized`, never echoed. Exit 3 is parsed for diagnostics and then checked against the scenario. Invalid client protocol responses and transport failures remain failures.
* This report was added. No DACL, identity check, supported command, production gate or storage integration was changed.

The client still has no authenticated server identity and same-account name comparison is not protection against hostile processes under that account. This batch makes no change to the impersonation level or peer verification behavior. Further Windows-specific diagnosis is needed if a real response is `DENIED`. A successful ping demonstrates functional IPC only.

## Verification

* `dotnet build prototype/windows_broker_dotnet/Broker.csproj --no-restore`: **PASS**, exit 0; 0 errors and 6 CA1416 warnings.
* `dotnet prototype/windows_broker_dotnet/bin/Debug/net10.0/Broker.dll selftest`: **PASS**, exit 0; includes synthetic valid ping, valid denial, invalid schema and unrecognized result checks.
* PowerShell AST parser and synthetic response formatter: **PASS**, zero syntax errors; 3 cases (ping, DENIED, unknown sensitive-looking value), 0 failures. These are offline synthetic checks.
* Native named-pipe integration: **NOT RUN by Codex**. No real response mismatch is claimed as diagnosed until the operator supplies the next run's sanitized output.

## Operator rerun

From the repository root in ordinary operator-controlled Windows PowerShell:

```powershell
dotnet build .\prototype\windows_broker_dotnet\Broker.csproj --no-restore
if ($LASTEXITCODE -eq 0) { powershell -NoProfile -ExecutionPolicy Bypass -File .\tests\backend\run_batch2b3h8_dotnet_ipc.ps1 }
```

Share the `response_json_parsed` and other sanitized diagnostic lines. The runner uses a unique development pipe and only its own processes, and can wait up to the configured server lifetime before the restart test.

**Production release decision: NO-GO.**
