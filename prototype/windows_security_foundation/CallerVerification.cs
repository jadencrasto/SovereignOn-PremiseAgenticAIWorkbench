using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

internal enum CallerFinding { Matched, Missing, Mismatch, InsufficientLevel, Changed, Failed, CleanupFailed }
internal sealed record CallerToken(string Sid, TokenImpersonationLevel Level, long Logon, long Revision);

// This component returns observations only. It is not a request dispatcher.
internal static class CallerVerification
{
    internal static CallerFinding Verify(string? expectedSid, Action<Action> asClient,
        Func<CallerToken?> capture, Func<bool> threadIsClear)
    {
        if (expectedSid is null || !ExpectedIdentity.ValidSid(expectedSid)) return CallerFinding.Missing;
        // Captured by the local wrapper, never by request data.
        if (!Clear(threadIsClear)) return CallerFinding.CleanupFailed;
        CallerToken? initial = null;
        for (int sample = 0; sample < 2; sample++)
        {
            CallerToken? observed = null;
            bool failed = false;
            try { asClient(() => observed = capture()); }
            catch { failed = true; }
            if (!Clear(threadIsClear)) return CallerFinding.CleanupFailed;
            if (failed) return CallerFinding.Failed;
            if (observed is null || !ExpectedIdentity.ValidSid(observed.Sid)) return CallerFinding.Missing;
            if (observed.Level != TokenImpersonationLevel.Identification) return CallerFinding.InsufficientLevel;
            if (initial is not null && observed != initial) return CallerFinding.Changed;
            if (observed.Sid != expectedSid) return CallerFinding.Mismatch;
            initial = observed;
        }
        return CallerFinding.Matched;
    }

    static bool Clear(Func<bool> threadIsClear)
    {
        try { return threadIsClear(); }
        catch { return false; }
    }
}

[SupportedOSPlatform("windows")]
internal static class WindowsCallerVerification
{
    // Reserved for a future serial, request-capable fixture after one bounded
    // read. The K pipe is outbound-only and never calls this method.
    internal static CallerFinding VerifyAfterRead(NamedPipeServerStream pipe, ExpectedIdentity? expected)
    {
        if (expected?.Fixture is not { } fixture) return CallerFinding.Missing;
        if (!ThreadIsClear()) return CallerFinding.CleanupFailed;
        var finding = CallerVerification.Verify(fixture.ObserverSid, action => pipe.RunAsClient(() => action()), Capture, ThreadIsClear);
        if (finding == CallerFinding.CleanupFailed && !ThreadIsClear() && (!RevertToSelf() || !ThreadIsClear()))
            Environment.FailFast("fixture_impersonation_cleanup_failed");
        return finding;
    }

    static bool ThreadIsClear()
    {
        if (!OpenThreadToken(GetCurrentThread(), 8, true, out var token)) return Marshal.GetLastWin32Error() == 1008;
        token.Dispose();
        return false;
    }

    static CallerToken? Capture()
    {
        if (!OpenThreadToken(GetCurrentThread(), 8, true, out var token)) return null;
        using (token)
        {
            var statistics = WindowsInspector.ReadToken(token, 10, WindowsInspector.ReadTokenStatistics);
            if (statistics.TokenType != 2 || statistics.Level is < 0 or > 3) throw new InvalidOperationException();
            string sid = WindowsInspector.ReadToken(token, 1, WindowsInspector.ReadTokenSid);
            return new(sid, (TokenImpersonationLevel)(statistics.Level + 1), statistics.Logon, statistics.Modified);
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct TokenStatistics
    {
        public long TokenId, Logon, Expiration;
        public int TokenType, Level, DynamicCharged, DynamicAvailable, Groups, Privileges;
        public long Modified;
    }

    [DllImport("kernel32.dll", ExactSpelling = true)] static extern IntPtr GetCurrentThread();
    [DllImport("advapi32.dll", ExactSpelling = true, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool OpenThreadToken(IntPtr thread, uint access, [MarshalAs(UnmanagedType.Bool)] bool openAsSelf,
        out SafeAccessTokenHandle token);
    [DllImport("advapi32.dll", ExactSpelling = true, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool RevertToSelf();
}
