using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

internal enum DevelopmentOperation { Ping, Status, Capabilities }
internal sealed record PipeEvidence(uint Pid, AccessEvidence Access);
internal interface IPipeLease : IDisposable
{
    PipeEvidence Inspect();
}
internal interface IEndpointInspector
{
    ValueTask<IPipeLease> Connect(ExpectedIdentity expected, CancellationToken cancellation);
}
internal sealed record BindingResult(Finding Finding, bool DevelopmentOperationAllowed, Func<bool>? SyntheticLeaseCheck = null)
{
    internal bool IsCurrent
    {
        get
        {
            try { return DevelopmentOperationAllowed && (SyntheticLeaseCheck?.Invoke() ?? true); }
            catch { return false; }
        }
    }
}

// Owns every lease from validation through the recheck. Native observations
// cannot unlock dispatch. The connected transport has no request-write method.
internal sealed class EndpointVerifier(IServiceInspector services, IEndpointInspector endpoints, EvidenceScope scope)
{
    internal Task<BindingResult> Verify(ExpectedIdentity? expected, DevelopmentOperation operation, CancellationToken cancellation) =>
        Verify(expected, operation, cancellation, null);

    internal Task<BindingResult> WithinSyntheticBinding(ExpectedIdentity? expected, CancellationToken cancellation,
        Action<BindingResult> observe) => Verify(expected, DevelopmentOperation.Ping, cancellation, observe);

    async Task<BindingResult> Verify(ExpectedIdentity? expected, DevelopmentOperation operation, CancellationToken cancellation,
        Action<BindingResult>? syntheticObservation)
    {
        if (expected is null) return new(Finding.MissingEvidence, false);
        if (!Enum.IsDefined(operation)) return new(Finding.UnexpectedEndpoint, false);
        if (syntheticObservation is not null && scope != EvidenceScope.Synthetic) return new(Finding.NativeUnavailable, false);
        try
        {
            cancellation.ThrowIfCancellationRequested();
            var initialConfig = services.ReadConfiguration(expected);
            var verdict = IdentityPolicy.Configuration(expected, initialConfig);
            if (verdict != Finding.VerifiedEvidence) return new(verdict, false);
            if (initialConfig.Pid == 0) return new(Finding.UnexpectedProcess, false);
            using var expectedProcess = services.Inspect(expected, cancellation);
            verdict = IdentityPolicy.Verify(expected, expectedProcess.Evidence);
            if (verdict != Finding.VerifiedEvidence) return new(verdict, false);
            if (!expectedProcess.Alive || expectedProcess.Evidence.Configuration != initialConfig)
                return new(Finding.ChangedBinding, false);

            using var pipe = await endpoints.Connect(expected, cancellation);
            var first = pipe.Inspect();
            verdict = CheckPipe(expected, expectedProcess, first);
            if (verdict != Finding.VerifiedEvidence) return new(verdict, false);

            using var recheck = services.Inspect(expected, cancellation);
            verdict = IdentityPolicy.Verify(expected, recheck.Evidence);
            if (verdict != Finding.VerifiedEvidence) return new(verdict, false);
            if (!expectedProcess.SameProcess(recheck) || recheck.Evidence.Configuration != initialConfig)
                return new(Finding.ChangedBinding, false);
            var initialToken = expectedProcess.Evidence.Token;
            var currentToken = recheck.Evidence.Token;
            if (initialToken.TokenId != currentToken.TokenId || initialToken.Revision != currentToken.Revision ||
                !initialToken.EnabledGroups.ToHashSet(StringComparer.Ordinal).SetEquals(currentToken.EnabledGroups) ||
                !initialToken.RestrictedGroups.ToHashSet(StringComparer.Ordinal).SetEquals(currentToken.RestrictedGroups))
                return new(Finding.ChangedBinding, false);
            var last = pipe.Inspect();
            if (first.Pid != last.Pid || first.Access.DescriptorSha256 != last.Access.DescriptorSha256 ||
                services.ReadConfiguration(expected) != initialConfig)
                return new(Finding.ChangedBinding, false);
            verdict = CheckPipe(expected, recheck, last);
            if (verdict != Finding.VerifiedEvidence) return new(verdict, false);
            cancellation.ThrowIfCancellationRequested();
            if (!expectedProcess.Alive || !recheck.Alive) return new(Finding.ChangedBinding, false);
            if (syntheticObservation is not null)
            {
                int active = 1;
                bool StillBound()
                {
                    if (Volatile.Read(ref active) == 0) return false;
                    bool current = false;
                    try
                    {
                        current = !cancellation.IsCancellationRequested && expectedProcess.Alive && recheck.Alive &&
                            expectedProcess.SameProcess(recheck) && services.ReadConfiguration(expected) == initialConfig &&
                            CheckPipe(expected, recheck, pipe.Inspect()) == Finding.VerifiedEvidence;
                        return current;
                    }
                    finally { if (!current) Volatile.Write(ref active, 0); }
                }
                try { syntheticObservation(new(Finding.VerifiedEvidence, true, StillBound)); }
                finally { Volatile.Write(ref active, 0); }
                return new(Finding.VerifiedEvidence, false);
            }
            return new(Finding.VerifiedEvidence, scope == EvidenceScope.Synthetic);
        }
        catch (InspectionException ex) { return new(ex.Finding, false); }
        catch (OperationCanceledException) { return new(Finding.Cancelled, false); }
        catch (TimeoutException) { return new(Finding.EndpointUnavailable, false); }
        catch (IOException) { return new(Finding.EndpointUnavailable, false); }
        catch (PlatformNotSupportedException) { return new(Finding.NativeUnavailable, false); }
        catch (Exception) { return new(Finding.InspectionFailure, false); }
    }

    static Finding CheckPipe(ExpectedIdentity expected, IServiceLease process, PipeEvidence pipe)
    {
        if (!process.Alive) return Finding.ChangedBinding;
        if (!process.MatchesPipeServer(pipe.Pid)) return Finding.UnexpectedEndpoint;
        var m = expected.Manifest;
        bool OwnerAllowed(string sid) => sid == m.ServiceSid || sid == m.UserSid ||
            m.InstallAdministrators.Contains(sid, StringComparer.Ordinal);
        // FILE_CREATE_PIPE_INSTANCE aliases FILE_APPEND_DATA. Generic write
        // includes that bit, so ordinary clients need explicitly scoped rights.
        const uint instanceOrControl = 0x500D0004;
        if (!pipe.Access.Complete || !OwnerAllowed(pipe.Access.Owner) ||
            !ExpectedIdentity.Same(pipe.Access.DescriptorSha256, m.EndpointDescriptorSha256) ||
            pipe.Access.Allows.Any(rule => (rule.Mask & instanceOrControl) != 0 && !OwnerAllowed(rule.Sid)))
            return Finding.UnsafeEndpoint;
        if (expected.Fixture is { } fixture)
        {
            AccessRule[] expectedRules = [new(m.UserSid, ProtectedFixtureConfig.OwnerRights, false),
                new(m.ServiceSid, ProtectedFixtureConfig.OwnerRights, false),
                new(fixture.ObserverSid, ProtectedFixtureConfig.ObserverRights, false)];
            if (pipe.Access.Owner != m.UserSid || pipe.Access.Allows.Length != expectedRules.Length ||
                !expectedRules.All(pipe.Access.Allows.Contains)) return Finding.UnsafeEndpoint;
        }
        return Finding.VerifiedEvidence;
    }
}

[SupportedOSPlatform("windows")]
internal sealed class WindowsEndpoint : IEndpointInspector
{
    internal static string ObserveAccessDenial(ProtectedFixtureConfig config)
    {
        // One read-only local open, no messages. A busy or absent pipe cannot
        // pass a denied-client scenario. The caller's process has an outer deadline.
        using var pipe = SyntheticStateJournal.CreateFile(@"\\.\pipe\" + config.PipeName, ProtectedFixtureConfig.ObserverRights,
            0, IntPtr.Zero, 3, 0x00110000, IntPtr.Zero); // SECURITY_SQOS_PRESENT | SECURITY_IDENTIFICATION
        if (!pipe.IsInvalid) return "UnexpectedAccess";
        return Marshal.GetLastPInvokeError() == 5 ? "Denied" : "Unavailable";
    }

    public async ValueTask<IPipeLease> Connect(ExpectedIdentity expected, CancellationToken cancellation)
    {
        var stream = new NamedPipeClientStream(".", expected.Manifest.PipeName,
            PipeAccessRights.ReadData | PipeAccessRights.ReadAttributes | PipeAccessRights.ReadPermissions | PipeAccessRights.Synchronize,
            PipeOptions.Asynchronous, TokenImpersonationLevel.Identification, HandleInheritability.None);
        try
        {
            await stream.ConnectAsync(2000, cancellation);
            return new Lease(stream);
        }
        catch { stream.Dispose(); throw; }
    }

    sealed class Lease(NamedPipeClientStream stream) : IPipeLease
    {
        public PipeEvidence Inspect()
        {
            if (!stream.IsConnected || !GetNamedPipeServerProcessId(stream.SafePipeHandle, out uint pid))
                throw new InspectionException(Finding.EndpointUnavailable);
            return new(pid, WindowsInspector.InspectAccess(stream.SafePipeHandle, 1));
        }
        public void Dispose() => stream.Dispose();
    }
    [DllImport("kernel32.dll", ExactSpelling=true, SetLastError=true)]
    static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe, out uint pid);
}
