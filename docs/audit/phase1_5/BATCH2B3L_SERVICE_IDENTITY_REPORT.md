# Batch 2B.3L — Protected Windows service identity

**Production NO-GO.** The read-only identity inspector and evidence policy are implemented. Windows observations await an independently approved VM and provisioned service.

## Scope and implementation

The repository search found no earlier definitions for K, L, M or N. This run uses the supplied scopes. J.4 remains offline-tested only. The existing broker, SCM host, launcher, storage layout and release gates were preserved.

The isolated .NET 10 project is `prototype/windows_security_foundation/SecurityFoundation.csproj`, with an offline `NuGet.Config` and a local build-output ignore file. `ServiceIdentity.cs` parses an immutable expected manifest whose SHA-256 must come from independent review. It rejects duplicate or unknown JSON properties, unsupported paths and service configurations, and absent or changed pins. `Program.cs` exposes a read-only observation entry point and self-tests. `SecurityTests.cs` exercises the production-of-evidence policy with synthetic snapshots.

| Evidence | Implementation |
|---|---|
| SCM identity and configuration | Fixed service name, local SCM, query/config/read-control rights only. `QueryServiceConfigW`, `QueryServiceConfig2W` and `QueryServiceStatusEx` observe the exact binary command, logon account, own-process type, demand start, restricted SID mode and RUNNING PID. The service object owner and DACL must match the reviewed policy and descriptor pin. |
| Process and token | A retained query/synchronize handle provides liveness, PID and image path. `OpenProcessToken` and `GetTokenInformation` inspect the user, enabled service SID, restricted SID, integrity RID and privilege list. The service SID must also resolve locally as the fixed SCM service account name. An administrator group or a listed privileged capability causes denial even when that privilege is disabled. |
| Installation and configuration files | Inspect the fixed NTFS drive root and every parent before the child. Retain read-sharing handles opened with `OPEN_REPARSE_POINT` and inspect final path, attributes, volume and file ID, link count, owner and DACL. Reject an untrusted mutation grant, including inherited grants. Hash only the four reviewed apphost, DLL, runtime config and dependency files, using the same retained handle after checking identity and permissions. |
| Missing or inconsistent evidence | Distinct missing, configuration, identity, installation, process, inspection and unsupported outcomes. Matching observations carry no native authority. |

The inspector checks native buffer lengths and embedded token/configuration offsets before reading. It uses safe handles for SCM, process, token and file resources and frees native security descriptor buffers. The ABI layout test covers SCM config and status, SID-and-attributes and both file identity structures. The layout check is offline and does not call the Windows inspection APIs. [SCM configuration API](https://learn.microsoft.com/en-us/windows/win32/api/winsvc/nf-winsvc-queryserviceconfigw) · [Restricted service SID semantics](https://learn.microsoft.com/en-us/openspecs/windows_protocols/ms-scmr/ea1a9acd-4bb2-473f-ae5b-55969c5960fc)

## Verification

- .NET SDK 10.0.404, `dotnet build prototype/windows_security_foundation/SecurityFoundation.csproj --no-restore`, **0 errors, 0 warnings**.
- `SecurityFoundation.dll selftest identity`, **24 passed, 0 failed**. Cases cover expected evidence, missing configuration and pins, changed or exited processes, user/SID/integrity and privilege mismatches, administrative tokens, changed command/account/SCM ACL, file identity/hash changes, reparse and path substitution, hard links, untrusted ownership or inherited writes, malformed and tampered trust anchors, and structure layouts.
- Existing .NET service foundation build, foundation self-test, **33 SCM cases**, **17 J.4 harness cases**, broker build and protocol/launcher self-tests passed. Existing version/schema tests passed all **9** cases. No native service installation, SCM service execution or pipe observation was run.

## Native prerequisites and limits

An approved VM operator must independently provision the dedicated nonadministrator logon account, restricted service SID, SCM DACL, protected NTFS directory ancestry and reviewed runtime files. The J.4 LocalService test registration is not this identity. The expected manifest must be created from the protected provisioning record, with its digest delivered independently. The inspector will not turn the service's own observations into expected identity. See the LM observation runner in the M report.

The manifest fields are `ServiceName`, `BinaryCommand`, `Account`, `UserSid`, `ServiceSid`, `PipeName`, `IntegrityRid`, `InstallAdministrators`, `Installation`, `ServiceDescriptorSha256` and `EndpointDescriptorSha256`. Installation entries contain `Path`, `FileId`, `Sha256` and `DescriptorSha256`. List the drive root and ordered ancestor directories, then `ServiceFoundation.exe`, `ServiceFoundation.dll`, `ServiceFoundation.runtimeconfig.json` and `ServiceFoundation.deps.json`. Directory SHA-256 is null. File IDs use the uppercase 16-digit volume serial followed by a colon and the 128-bit ID interpreted as the Windows GUID layout in lowercase N format. Descriptor hashes are SHA-256 of UTF-8 SDDL projected to Owner and Access. Pin the pipe name as `SovereignAI-identity-dev-` followed by 32 lowercase hex digits.

Native checks do not attest already mapped image sections, runtime dependency loading, drive-letter mapping under adversarial control, previously inherited or duplicated handles, or administrator/backup bypass. A path and current file hash cannot prove what was mapped earlier. A trusted boot/install sequence, verified handle exclusion and independent native effective-access tests remain required. No automatic native trust or production service provisioning was added.
