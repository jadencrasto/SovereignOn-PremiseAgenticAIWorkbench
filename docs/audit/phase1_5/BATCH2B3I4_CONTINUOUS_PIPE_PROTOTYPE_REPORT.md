# Batch 2B.3I.4 — Continuous named-pipe prototype

**Status: blocked; implementation and native harness not delivered. Production: NO-GO.** The existing broker still closes and recreates its sole instance between clients. Continuous ownership is not established. No reusable-pipe entry point was enabled, and no native test was run by Codex.

## Findings

The current implementation creates a native pipe with one instance, first-instance protection, `PIPE_REJECT_REMOTE_CLIENTS`, and a restricted explicit DACL, then wraps the handle in `NamedPipeServerStream`. It disposes that stream after each connection or timed wait. That creates a turnover gap; neither the previous successful operator run nor the launcher observation rules out squatting in that gap.

Windows documents reconnecting the same server instance after `DisconnectNamedPipe`, and notes that unread data is discarded. The managed `NamedPipeServerStream.Disconnect()` updates managed state, whereas calling the native function directly behind the stream would bypass that state; their compatibility under failures and cancellation is not established here. The .NET 10 documentation says managed `Disconnect()` can block until sent characters are read. Its API provides no cancellation token. The current byte-mode server cannot assume the peer will drain data. The API documentation alone therefore does not establish the requested finite disconnect deadline.

The server awaits asynchronous connection, read and write tasks, but the evidence available here does not independently establish that all underlying overlapped operations have completed and been drained on every cancellation, disconnect, timeout race, shutdown and exception before handle reuse. Microsoft documents separate immediate and pending completion paths and retrieval of pending results. A cancellation request is not itself evidence of completion. Reusing a handle in this state or running a blocking disconnect on an abandoned worker would not satisfy the requested safety boundary. For this reason no executable prototype or placeholder runner was added. This is an implementation blocker, not a finding that .NET or Windows cannot implement a safe solution.

## Narrow alternative for independent review

A dedicated development-only Windows native handle owner could retain one first-instance handle, use one `OVERLAPPED` and event per operation, distinguish immediate completion from pending completion, retain buffers and handles, call `CancelIoEx` on deadline, and drain the actual completion before disconnect/reconnect. If completion cannot be established within an independently supervised deadline, terminate that process and report ownership lost rather than reuse the handle. The owner must preserve the existing exact ACL and single-instance limit; its security review must cover partial messages, impersonation and reversion, response delivery, and the race where a connection completes as a timeout occurs. A job object with verified assignment and kill-on-close could bound a failed owner and its test children, subject to enclosing-job compatibility. No such host, job-object implementation or tests exist in this batch.

## Verification and test preparation

Source, prior reports, existing runners and Git status were inspected. No application source, ACL, host setting, existing runner, or production gate was changed. No build or offline tests were run because no executable change was made. Native IPC, handle reuse, duplicate creation, stalled client, cancellation and restart are **NOT RUN**.

Before operator testing, implement and independently review a bounded runner and native owner. The runner must create a unique endpoint; use synthetic traffic; supervise only owned processes; test repeated ping, disconnect, malformed/oversized/stalled requests, duplicate and competing creates during turnover, shutdown while connecting, and restart. A finite collection of competing attempts cannot prove absence of every scheduling race. Process-tree termination without a verified job object remains best effort. There is no operator command for the requested reusable prototype yet. Existing H.8 and I.2 runners remain available but cannot validate continuous ownership.

Successful tests would demonstrate only tested development behavior, not trusted server identity, same-account isolation, storage authorization or production readiness.

References: [managed Disconnect](https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.namedpipeserverstream.disconnect?view=net-10.0), [DisconnectNamedPipe](https://learn.microsoft.com/en-us/windows/win32/api/namedpipeapi/nf-namedpipeapi-disconnectnamedpipe), [overlapped pipe server](https://learn.microsoft.com/en-us/windows/win32/ipc/named-pipe-server-using-overlapped-i-o), and [WaitForConnectionAsync](https://learn.microsoft.com/en-us/dotnet/api/system.io.pipes.namedpipeserverstream.waitforconnectionasync?view=net-10.0).
