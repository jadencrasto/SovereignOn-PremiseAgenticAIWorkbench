# Batch 2B.3O.1 — Offline VM staging under RemoteSigned

**Production NO-GO.** The VM execution failure and its exact zone classification require VM-side evidence. The operator observed `RemoteSigned`, an unsigned script and a signature error on the mounted ISO. Those observations establish that PowerShell required a valid signature for that invocation. They do not establish whether an alternate data stream, the optical media's security zone, a Windows shell zone check or another environment-specific condition caused it. Microsoft documents the signature requirement and Windows Shell zone checks. [PowerShell execution policies](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_execution_policies?view=powershell-7)

This batch replaces the unsigned optical-script staging entry point with a native verifier. It never runs package scripts, changes execution policy, changes publisher trust or requests elevation. It verifies a reviewed bootstrap inventory, the inner ISO, the payload inventory, every allowed file and every packaged PowerShell signature before creating the fixed workspace. The application checks for a VirtualBox model, independently supplied VM UUID and name, and an effective `RemoteSigned` or `AllSigned` policy. It requires a separately reviewed publisher certificate SHA-256, a valid Authenticode result, a matching certificate in TrustedPublisher, code-signing usage and a verifiable offline certificate chain. A successful package check asks for `STAGE` before writing. Create-new file handles and rollback are confined to the owned fixed workspace. A failure to confirm rollback is reported for VM review.

## Signing prerequisite

The existing package is unsigned. A reviewed signing identity and independently provisioned trust are required before this bootstrap will stage it. Obtain an organization-approved code-signing certificate or other independently trusted publisher certificate under the VM's certificate policy. Provision its public certificate, chain and any required cached revocation evidence in the approved VM through a separately reviewed procedure. Trust the actual publisher under the applicable Windows policy. Do not copy a private signing key to the VM. Self-generated trust does not establish independent review. [PowerShell signing](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_signing?view=powershell-7.4)

The host builder signs temporary copies only when an existing `Cert:\CurrentUser\My` certificate is explicitly selected. The 40-hex thumbprint selects the host certificate. The VM bootstrap asks for the independently reviewed SHA-256 fingerprint of its public certificate. The signing option invokes the standard Authenticode cmdlet and requires a valid result. No timestamp service is configured. Offline chain or revocation checks can fail until independently reviewed cached evidence is available.

```powershell
.\build-native-vm-iso.ps1 -SignerThumbprint <reviewed-40-hex-certificate-thumbprint>
```

The builder prints the outer ISO hash, inner payload inventory hash and `BOOTSTRAP_MANIFEST_SHA256`. If called without a signer, it produces an explicitly UNSIGNED review package. The bootstrap rejects it before staging. Existing ISOs are retained and later default runs use unique suffixes.

## Review and staging

Independently review the source, signer certificate and the builder's hashes. Transfer the reviewed bootstrap manifest SHA-256 through a separate trusted channel. The outer ISO is attached read-only in the already approved disposable VM. `PAYLOAD.iso` is an ordinary file on that disc, so Windows `Get-FileHash` can check all of its bytes in the guest. The outer mounted disc itself is verified on the host using its printed hash. Verifying raw optical sectors from the guest is a separate operation and is not assumed here.

The following independently reviewed interactive PowerShell block uses built-in cmdlets. It validates the exact fixed bootstrap manifest against the separately supplied digest, checks all four bootstrap runtime files, the complete payload ISO and the payload inventory, then starts the verified native executable. It executes no packaged PowerShell script. Replace `D:` only if the VM shows a different optical drive letter.

```powershell
$pin = Read-Host 'Independently reviewed BOOTSTRAP-SHA256.txt SHA-256'
if ($pin -notmatch '^[0-9a-fA-F]{64}$' -or (Get-FileHash 'D:\BOOTSTRAP-SHA256.txt' -Algorithm SHA256).Hash -ine $pin) { throw 'Bootstrap manifest mismatch' }
$allowed = @('VmBootstrap.exe','VmBootstrap.dll','VmBootstrap.deps.json','VmBootstrap.runtimeconfig.json','PAYLOAD.iso','SHA256SUMS.txt')
$seen = [Collections.Generic.HashSet[string]]::new([StringComparer]::Ordinal)
foreach ($line in (Get-Content 'D:\BOOTSTRAP-SHA256.txt')) {
    if ($line -cnotmatch '^([0-9a-f]{64})  ([A-Za-z0-9_.-]+)$') { throw 'Invalid bootstrap entry' }
    $hash = $Matches[1]; $name = $Matches[2]
    if ($name -cnotin $allowed -or -not $seen.Add($name) -or (Get-FileHash -LiteralPath (Join-Path 'D:\' $name) -Algorithm SHA256).Hash -ine $hash) { throw 'Bootstrap file mismatch' }
}
if ($seen.Count -ne $allowed.Count) { throw 'Bootstrap entry missing' }
foreach ($name in 'DOTNET_STARTUP_HOOKS','DOTNET_ADDITIONAL_DEPS','DOTNET_SHARED_STORE','CORECLR_ENABLE_PROFILING','COR_ENABLE_PROFILING') { if ([Environment]::GetEnvironmentVariable($name, 'Process')) { throw 'Unexpected runtime configuration' } }
& 'D:\VmBootstrap.exe' $pin
```

The native verifier asks for the independently reviewed VM name and UUID and the publisher certificate SHA-256. It stops when the fixed workspace already exists. It does not create a VM marker. Once staging succeeds, follow the existing Lifecycle or Protected profile setup and per-run approval. The two profiles use the same service name and require independently prepared, separate VM states. Retain the approved snapshot and restore it after ambiguous outcomes.

The bootstrap trusts the already installed VM Windows, .NET runtime and PowerShell executable. It cannot attest a compromised operating system, an attacker who controls the VM or changes the optical attachment between checks, the recoverability of the VM snapshot, or an independently administered durable witness. The protected profile additionally requires the reviewed service account, protected installation, SCM configuration, endpoint and storage ACLs, independent manifest and cross-account token read permissions. Windows Home's Local Group Policy Editor is unavailable. Actual support and secure provisioning on this VM remain unverified. [Windows configuration tools](https://support.microsoft.com/en-au/windows/experience/system-configuration-tools-in-windows?facet1=startup)

## Execution-policy diagnostics

In the VM, record `Get-ExecutionPolicy -List`, `$PSVersionTable`, the complete error's `FullyQualifiedErrorId`, and `Get-AuthenticodeSignature D:\stage-native-security-tests.ps1`. If available, `Get-Item D:\stage-native-security-tests.ps1 -Stream *` can report alternate streams, but an optical filesystem may not expose them. These are read-only diagnostics. They do not by themselves prove why Windows classified a file as remote. Do not modify execution policy or origin markings.

Native SCM, endpoint, ACL and protected identity verification are pending. The package alone carries no authorization and does not make the production service or storage ready.

## Host implementation and verification

The new `prototype/windows_vm_bootstrap` project implements the native verifier and handle-based staging transaction. `build-native-vm-iso.ps1` and `tools/offline_verification/build_native_vm_iso.py` build it and produce an outer transfer ISO containing a complete, separately hashable `PAYLOAD.iso`. The prior unsigned optical staging script is excluded from the new payload. Existing launcher, setup, identity and approval logic are included without a policy change. The package README points to this procedure. Prior audit reports remain historical and can contain superseded optical staging instructions.

`dotnet build prototype/windows_vm_bootstrap/VmBootstrap.csproj --no-restore` succeeded with zero warnings and errors. The bootstrap self-test passed 22 cases, including canonical inventory parsing, path traversal and duplicate rejection, changed payload and ISO data, missing trust values, unsigned script denial, VM identity, accepted execution policies, no elevation request or policy override, destination refusal, transaction cleanup and a real Windows temporary-file rollback. Package tests passed 6 cases. The existing Batch O workflow tests passed 17 cases. These tests do not establish native signature verification or SCM execution in the VM.

The framework-dependent bootstrap has four files. A single-file publication was investigated but offline restore reported missing `Microsoft.NET.ILLink.Tasks` and Windows runtime packages. No dependencies were downloaded. The reviewed interactive block checks all four files before launch. The installed Windows, PowerShell and .NET runtime remain explicit trust assumptions.

No independently reviewed code-signing identity or publisher trust was supplied for this batch. A read-only check of the managed host session's CurrentUser personal certificate store found zero certificates with a private key and code-signing usage. It did not inspect other identities, hardware tokens or a separately administered signing environment. The default build is deliberately an unsigned review image, and the verifier's signature check prevents staging it. A genuinely signed image and positive VM staging remain blocked on a separately selected code-signing identity and independently provisioned publisher trust. The unsigned image hash is an integrity identifier for review, not evidence of publisher authorization.
