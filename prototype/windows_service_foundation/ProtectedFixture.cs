using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

internal sealed class ProtectedFixtureWorker(
    Func<IDisposable> open, Func<IDisposable, CancellationToken, Task> accept, TimeSpan connectDeadline) : IScmWorker
{
    IDisposable? owner;

    public Task Initialize(CancellationToken token)
    {
        token.ThrowIfCancellationRequested();
        owner = open();
        return Task.CompletedTask;
    }

    public async Task Run(CancellationToken token)
    {
        using var connection = CancellationTokenSource.CreateLinkedTokenSource(token);
        connection.CancelAfter(connectDeadline);
        try
        {
            await accept(owner ?? throw new InvalidOperationException(), connection.Token);
            connection.Token.ThrowIfCancellationRequested();
        }
        catch (OperationCanceledException) when (token.IsCancellationRequested) { token.ThrowIfCancellationRequested(); }
        // One observation only. Retain the original instance after peer disconnect.
        // No application payload, read, write, flush, disconnect or second accept.
        await Task.Delay(Timeout.InfiniteTimeSpan, token);
    }

    public Task Cleanup(CancellationToken token) => Task.CompletedTask;
    public ValueTask DisposeAsync()
    {
        Interlocked.Exchange(ref owner, null)?.Dispose();
        return ValueTask.CompletedTask;
    }
}

[SupportedOSPlatform("windows")]
internal static class ProtectedFixture
{
    internal static IScmWorker Create(ProtectedFixtureConfig config) => new ProtectedFixtureWorker(
        () => Open(config), (owner, token) => ((NamedPipeServerStream)owner).WaitForConnectionAsync(token),
        TimeSpan.FromSeconds(30));

    static NamedPipeServerStream Open(ProtectedFixtureConfig config)
    {
        using var identity = WindowsIdentity.GetCurrent(TokenAccessLevels.Query | TokenAccessLevels.Duplicate);
        var serviceSid = (SecurityIdentifier)new NTAccount("NT SERVICE", ProtectedFixtureConfig.ServiceName)
            .Translate(typeof(SecurityIdentifier));
        if (identity.User?.Value != config.BrokerSid || identity.Groups is not { } groups ||
            groups.Contains(new SecurityIdentifier(WellKnownSidType.BuiltinAdministratorsSid, null)) ||
            !IsTokenRestricted(identity.AccessToken) || !CheckTokenMembership(identity.AccessToken, serviceSid, out bool member) || !member)
            throw new InvalidOperationException("fixture_identity");

        if (!ConvertDescriptor(config.Descriptor(serviceSid.Value), 1, out IntPtr descriptor, IntPtr.Zero))
            throw new InvalidOperationException("fixture_descriptor");
        try
        {
            var security = new SecurityAttributes { Length = Marshal.SizeOf<SecurityAttributes>(), Descriptor = descriptor };
            var handle = CreateNamedPipe(@"\\.\pipe\" + config.PipeName, 0x40080002, 8, 1, 0, 0, 0, ref security);
            try
            {
                if (handle.IsInvalid) throw new InvalidOperationException("fixture_endpoint");
                return new NamedPipeServerStream(PipeDirection.Out, isAsync: true, isConnected: false, handle);
            }
            catch { handle.Dispose(); throw; }
        }
        finally { LocalFree(descriptor); }
    }

    // CheckTokenMembership requires an impersonation token when passed a handle.
    static bool CheckTokenMembership(SafeAccessTokenHandle primary, SecurityIdentifier sid, out bool member)
    {
        member = false;
        if (!DuplicateToken(primary, 1, out var impersonation)) return false;
        using (impersonation)
        {
            var bytes = new byte[sid.BinaryLength];
            sid.GetBinaryForm(bytes, 0);
            return NativeMembership(impersonation, bytes, out member);
        }
    }

    [StructLayout(LayoutKind.Sequential)]
    internal struct SecurityAttributes { public int Length; public IntPtr Descriptor; public int Inherit; }

    [DllImport("kernel32.dll", EntryPoint = "CreateNamedPipeW", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    static extern SafePipeHandle CreateNamedPipe(string name, uint openMode, uint pipeMode, uint instances,
        uint outSize, uint inSize, uint timeout, ref SecurityAttributes security);

    [DllImport("advapi32.dll", EntryPoint = "ConvertStringSecurityDescriptorToSecurityDescriptorW", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool ConvertDescriptor(string text, uint revision, out IntPtr descriptor, IntPtr length);

    [DllImport("kernel32.dll", ExactSpelling = true)] static extern IntPtr LocalFree(IntPtr memory);
    [DllImport("advapi32.dll", ExactSpelling = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool IsTokenRestricted(SafeAccessTokenHandle token);
    [DllImport("advapi32.dll", ExactSpelling = true, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool DuplicateToken(SafeAccessTokenHandle token, int level, out SafeAccessTokenHandle duplicate);
    [DllImport("advapi32.dll", EntryPoint = "CheckTokenMembership", ExactSpelling = true, SetLastError = true)] [return: MarshalAs(UnmanagedType.Bool)]
    static extern bool NativeMembership(SafeAccessTokenHandle token, byte[] sid, [MarshalAs(UnmanagedType.Bool)] out bool member);
}
