# Batch 2B.3J.1 — Native SCM host

**Partial implementation; production NO-GO.** A development-only native SCM adapter compiles, but actual SCM behavior and several required failure/race cases are unverified. This batch does not establish a protected service identity, endpoint, storage authority or production readiness. Existing broker and launcher were not modified.

## Implementation

`prototype/windows_service_foundation/NativeScm.cs` adds an own-process SCM adapter. `Program.cs` adds explicitly selected `service` and `scm-selftest` modes and preserves the existing self-test and deny-only behavior. No package reference or NuGet source was added. No pipe, SQLite, Chroma, document, artifact, tool or FastAPI operation is present.

The adapter P/Invokes `StartServiceCtrlDispatcherW(SERVICE_TABLE_ENTRYW*) -> BOOL`, `RegisterServiceCtrlHandlerExW(LPCWSTR, HandlerEx, void*) -> SERVICE_STATUS_HANDLE`, and `SetServiceStatus(SERVICE_STATUS_HANDLE, SERVICE_STATUS*) -> BOOL`. The table contains pointer-sized name/function entries followed by a null entry. `SERVICE_STATUS` is seven 32-bit unsigned fields; callbacks use `CallingConvention.Winapi` with `void(uint, IntPtr)` and `uint(uint, uint, IntPtr, IntPtr)` signatures. Static delegate references and `GC.KeepAlive` preserve callback lifetimes. The fixed service name is `SovereignDevBrokerFoundation` and extra SCM startup arguments are rejected. Console invocation of service mode fails if it cannot connect to the SCM.

| State | Intended behavior |
|---|---|
| START_PENDING | Register handler; validate fixed startup arguments. |
| RUNNING | Accept STOP/SHUTDOWN; no worker, endpoint or authority. |
| STOP_PENDING | Main callback observes the stop event and reports pending. |
| STOPPED | Report success or a service-specific error. |

The control callback sets an event and returns without waiting for worker cleanup. The service main waits on that event; since no worker or cleanup is implemented, shutdown work is minimal. However no independently enforced hard deadline exists if a native API or callback hangs. STOP while startup is pending is modeled only indirectly: SCM normally does not send controls until advertised, and the code checks the stop event before RUNNING. Status publication failures are checked, but failure of the very first START_PENDING publication currently returns without a confirmed STOPPED publication. This needs correction and injectable native-boundary tests before operational use. Registration failure has no status handle and cannot be reported through `SetServiceStatus`. Callback exception and SetServiceStatus failure paths are not proven under Windows.

## Verification

* `dotnet build prototype/windows_service_foundation/ServiceFoundation.csproj --no-restore`: PASS, exit 0, 0 errors, 0 warnings.
* `dotnet prototype/windows_service_foundation/bin/Debug/net10.0/ServiceFoundation.dll selftest`: PASS, exit 0, existing deny-only checks.
* `dotnet prototype/windows_service_foundation/bin/Debug/net10.0/ServiceFoundation.dll scm-selftest`: PASS, exit 0, synthetic normal transition, invalid post-stop transition, initial status failure and startup-to-stopped checks.
* Existing H.8 PowerShell parser: PASS. Native SCM dispatch, actual callback ABI, lifecycle behavior, STOP/SHUTDOWN, and service registration: NOT RUN. No service was installed or started.

The test does not cover the full requested matrix: actual handler-registration failure, failed later status publication, repeated STOP/SHUTDOWN, worker failure, cancellation during initialization, callback exceptions and bounded cleanup need injectable adapter and VM tests. No worker exists to fail. No claim of independently verified SCM compatibility is made.

## VM-only proposal — separately approved disposable VM required

These commands modify the VM's SCM and must **not** be run on the normal host. Take an independently verified snapshot and use a synthetic isolated VM. First build offline and review the executable, runtime dependencies and file hash. Example template, after resolving placeholders and independent approval:

```powershell
dotnet build .\prototype\windows_service_foundation\ServiceFoundation.csproj --configfile .\prototype\windows_service_foundation\NuGet.Config
Get-FileHash -Algorithm SHA256 '<reviewed-absolute-path>\ServiceFoundation.exe'
sc.exe create SovereignDevBrokerFoundation binPath= '"<reviewed-absolute-path>\ServiceFoundation.exe" service' start= demand
sc.exe start SovereignDevBrokerFoundation
sc.exe query SovereignDevBrokerFoundation
sc.exe stop SovereignDevBrokerFoundation
sc.exe query SovereignDevBrokerFoundation
```

`sc create` installs a service in the VM, `start` launches it, and `stop` sends a control request. The name is fixed by this prototype, so do not run if it already exists. A runtime-dependent build requires the corresponding .NET runtime installed in the VM; no installation is authorized by this document. Test invalid startup configuration and repeat/SHUTDOWN control behavior using separately reviewed bounded SCM tools; the current code lacks a safe injected startup-failure switch. Use an external hard deadline and revert the whole disposable VM snapshot afterward. The adapter must first be corrected and independently reviewed for the status-publication gap above; these commands are a plan, not a recommendation to run the current build.

References: [dispatcher](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-startservicectrldispatcherw), [handler registration](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-registerservicectrlhandlerexw), [status](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-setservicestatus), [service transitions](https://learn.microsoft.com/en-us/windows/win32/services/service-status-transitions).
