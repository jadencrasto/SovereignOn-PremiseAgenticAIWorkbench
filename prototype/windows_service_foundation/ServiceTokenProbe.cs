// Offline-only policy foundation. No SCM mode or native probe is wired to this code.
// A successful synthetic check never authorizes access or reports native evidence.
internal static class ServiceTokenProbe
{
    internal enum Outcome { Denied, Unsupported, SimulatedMatch, Timeout }
    internal sealed record Token(string User, string ServiceSid, bool ServiceEnabled,
        bool ServiceRestricted, bool Restricted, bool Administrator, bool UnsafePrivileges,
        int IntegrityRid, bool ThreadImpersonating);
    internal sealed record Request(string Operation, bool Approved, bool VmVerified,
        string ExpectedUser, string ExpectedServiceSid, int ExpectedIntegrityRid,
        DateTimeOffset Expires);
    internal static bool Matches(Request request, Token token, DateTimeOffset now) =>
        request.Operation == "inspect-synthetic-storage-rights" && request.Approved && request.VmVerified &&
        request.Expires > now && request.Expires <= now.AddMinutes(1) &&
        !string.IsNullOrWhiteSpace(request.ExpectedUser) &&
        !string.IsNullOrWhiteSpace(request.ExpectedServiceSid) &&
        token.User == request.ExpectedUser && token.ServiceSid == request.ExpectedServiceSid &&
        token.ServiceEnabled && token.ServiceRestricted && token.Restricted && !token.Administrator &&
        !token.UnsafePrivileges && !token.ThreadImpersonating && token.IntegrityRid > 0 &&
        token.IntegrityRid == request.ExpectedIntegrityRid;

    // This entry point cannot be activated by configuration, command line, or flags.
    internal static Outcome NativeExecution() => Outcome.Unsupported;

    // Pure synthetic operation; no supplied callback, native token, files, or process launch.
    internal static async Task<Outcome> Simulate(Request request, Token token, DateTimeOffset now,
        TimeSpan delay, TimeSpan timeout, CancellationToken cancellation = default)
    {
        if (!Matches(request, token, now) || timeout <= TimeSpan.Zero || timeout > TimeSpan.FromSeconds(1) ||
            delay < TimeSpan.Zero || delay > TimeSpan.FromSeconds(1)) return Outcome.Denied;
        using var deadline = CancellationTokenSource.CreateLinkedTokenSource(cancellation);
        deadline.CancelAfter(timeout);
        try { await Task.Delay(delay, deadline.Token); return Outcome.SimulatedMatch; }
        catch (OperationCanceledException) { return Outcome.Timeout; }
    }
    internal static string Evidence(Outcome outcome) => outcome switch
    {
        Outcome.SimulatedMatch => "development_only=true authority=false evidence=synthetic_match",
        Outcome.Timeout => "development_only=true authority=false evidence=timeout",
        Outcome.Unsupported => "development_only=true authority=false evidence=native_unsupported",
        _ => "development_only=true authority=false evidence=denied"
    };
}
