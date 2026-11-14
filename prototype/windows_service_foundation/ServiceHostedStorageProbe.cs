using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

// Fixed-operation component. No service entry point calls it: the approval,
// durable replay and protected target-identity prerequisites are not implemented.
internal static class ServiceHostedStorageProbe
{
    internal const string Operation = "inspect-synthetic-storage-rights";
    internal const string Target = @"C:\SovereignVmVerification\synthetic-storage-k";
    internal enum Result { Disabled, Denied, Failed, ExpectedAccess }

    // Deliberately unconditionally closed; no flag, parameter or environment override.
    internal static Result Dispatch(string operation) => Result.Disabled;

    // Kept private and unreachable until the native approval path is independently
    // implemented. This invokes the actual shared N probe in the current process;
    // it does not launch or impersonate another process. It is not natively tested.
    [SupportedOSPlatform("windows")]
    static Result InspectCurrentProcess(ExpectedIdentity expected, CancellationToken cancellation)
    {
        try
        {
            cancellation.ThrowIfCancellationRequested();
            if (expected.Fixture is null) return Result.Denied;
            using var threadIdentity = WindowsIdentity.GetCurrent(ifImpersonating: true);
            if (threadIdentity is not null) return Result.Denied;
            using var process = new SafeProcessHandle(GetCurrentProcess(), ownsHandle: false);
            var token = WindowsInspector.InspectToken(process);
            var manifest = expected.Manifest;
            if (!StorageAccessProbe.MatchesRole(token, manifest.UserSid, manifest.ServiceSid, "broker") ||
                token.IntegrityRid != manifest.IntegrityRid) return Result.Denied;
            cancellation.ThrowIfCancellationRequested();
            int result = StorageAccessProbe.Run(Target, "broker", manifest.UserSid, manifest.ServiceSid, exercise: false);
            cancellation.ThrowIfCancellationRequested();
            return result == 0 ? Result.ExpectedAccess : Result.Failed;
        }
        catch { return Result.Failed; }
    }

    internal static string Evidence(Result value) => value switch
    {
        Result.Disabled => "PROBE result=disabled development_only=true authority=false",
        Result.Denied => "PROBE result=denied development_only=true authority=false",
        Result.ExpectedAccess => "PROBE result=expected_access development_only=true authority=false",
        _ => "PROBE result=failed development_only=true authority=false"
    };

    [DllImport("kernel32.dll", ExactSpelling = true)] static extern IntPtr GetCurrentProcess();
}
