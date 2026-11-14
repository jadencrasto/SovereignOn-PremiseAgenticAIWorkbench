using System.Runtime.InteropServices;

// Development-only own-process SCM adapter.
internal static class NativeScm
{
    const string ServiceName = ProtectedFixtureConfig.ServiceName;
    const uint OwnProcess = 0x10;
    const uint AcceptStop = 1, AcceptShutdown = 4, ErrorFailed = 1066;
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate void ServiceMainDelegate(uint argc, IntPtr argv);
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] delegate uint HandlerDelegate(uint control, uint eventType, IntPtr eventData, IntPtr context);
    [StructLayout(LayoutKind.Sequential)] struct ServiceTableEntry { public IntPtr Name; public IntPtr Main; }
    [StructLayout(LayoutKind.Sequential)] internal struct ServiceStatus { public uint Type, CurrentState, ControlsAccepted, Win32ExitCode, ServiceSpecificExitCode, CheckPoint, WaitHint; }
    [DllImport("advapi32.dll", EntryPoint="StartServiceCtrlDispatcherW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)] static extern bool StartDispatcher([In, MarshalAs(UnmanagedType.LPArray, SizeConst=2)] ServiceTableEntry[] table);
    [DllImport("advapi32.dll", EntryPoint="RegisterServiceCtrlHandlerExW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern IntPtr RegisterHandler(string name, HandlerDelegate handler, IntPtr context);
    [DllImport("advapi32.dll", EntryPoint="SetServiceStatus", ExactSpelling=true, SetLastError=true)]
    [return: MarshalAs(UnmanagedType.Bool)] static extern bool SetStatus(IntPtr handle, ref ServiceStatus status);

    static readonly ServiceMainDelegate mainCallback = ServiceMain;
    static readonly HandlerDelegate handlerCallback = Handler;
    static ScmController? controller;
    static IntPtr statusHandle;
    static int callbackFailure;
    static ProtectedFixtureConfig? protectedFixture;

    internal static ServiceStatus CreateStatus(uint state, ServiceError error)
    {
        bool pending = state is ScmController.StartPending or ScmController.StopPending;
        return new ServiceStatus { Type=OwnProcess, CurrentState=state,
            ControlsAccepted=state == ScmController.Running ? AcceptStop | AcceptShutdown : 0,
            Win32ExitCode=error == ServiceError.None ? 0 : ErrorFailed, ServiceSpecificExitCode=(uint)error,
            CheckPoint=pending ? 1U : 0U, WaitHint=pending ? 5000U : 0U };
    }
    static bool Publish(uint state, ServiceError error)
    {
        var status = CreateStatus(state, error);
        if (error != ServiceError.None) Interlocked.Exchange(ref callbackFailure, 1);
        bool accepted = SetStatus(statusHandle, ref status);
        if (!accepted) Interlocked.Exchange(ref callbackFailure, 1);
        return accepted;
    }
    static uint Handler(uint control, uint eventType, IntPtr eventData, IntPtr context)
    {
        try { return Volatile.Read(ref controller)?.Control(control) ?? 1064; }
        catch { Interlocked.Exchange(ref callbackFailure, 1); Volatile.Read(ref controller)?.CallbackFailed(); return 1064; }
    }
    internal enum Mode { Invalid, Normal, FailInitialization, HoldInitialization, ExitProcess }
    internal static Mode ParseStartup(string? name, uint count, string? argument, bool fixture = false)
    {
        if (!string.Equals(name, ServiceName, StringComparison.OrdinalIgnoreCase)) return Mode.Invalid;
        if (count == 1) return Mode.Normal;
        if (count != 2 || fixture) return Mode.Invalid;
        return argument switch {
            "--j4-fail-initialization" => Mode.FailInitialization,
            "--j4-hold-initialization" => Mode.HoldInitialization,
            "--j4-exit-process" => Mode.ExitProcess,
            _ => Mode.Invalid
        };
    }
    internal sealed class DenyOnlyWorker(Mode mode, Func<CancellationToken, Task>? exitDelay = null,
        Action<int>? exitProcess = null) : IScmWorker
    {
        public Task Initialize(CancellationToken token) => mode switch {
            Mode.FailInitialization => Task.FromException(new InvalidOperationException()),
            Mode.HoldInitialization => Task.Delay(Timeout.InfiniteTimeSpan, token),
            _ => Task.CompletedTask
        };
        public async Task Run(CancellationToken token)
        {
            if (mode == Mode.ExitProcess)
            {
                await (exitDelay?.Invoke(token) ?? Task.Delay(TimeSpan.FromSeconds(3), token));
                token.ThrowIfCancellationRequested();
                (exitProcess ?? Environment.Exit)(23);
            }
            await Task.Delay(Timeout.InfiniteTimeSpan, token);
        }
        public Task Cleanup(CancellationToken token) => Task.CompletedTask;
        public ValueTask DisposeAsync() => ValueTask.CompletedTask;
    }
    static void ServiceMain(uint argc, IntPtr argv)
    {
        try {
            string? name = argc is 1 or 2 && argv != IntPtr.Zero ? Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv)) : null;
            string? argument = argc == 2 && argv != IntPtr.Zero ? Marshal.PtrToStringUni(Marshal.ReadIntPtr(argv, IntPtr.Size)) : null;
            var fixture = protectedFixture;
            var mode = ParseStartup(name, argc, argument, fixture is not null);
            var lifecycle = new ScmController(
                () => { statusHandle=RegisterHandler(ServiceName, handlerCallback, IntPtr.Zero); return statusHandle != IntPtr.Zero; },
                Publish, () => fixture is not null && OperatingSystem.IsWindows()
                    ? ProtectedFixture.Create(fixture) : new DenyOnlyWorker(mode), TimeSpan.FromSeconds(5), TimeSpan.FromSeconds(60));
            Volatile.Write(ref controller, lifecycle);
            var result = lifecycle.Run(mode != Mode.Invalid).GetAwaiter().GetResult();
            if (result.ExitCode != 0)
            {
                Interlocked.Exchange(ref callbackFailure, 1);
            }
        } catch {
            Interlocked.Exchange(ref callbackFailure, 1);
        }
    }
    public static int Dispatch(ProtectedFixtureConfig? fixture = null)
    {
        protectedFixture = fixture;
        IntPtr name = Marshal.StringToHGlobalUni(ServiceName);
        try {
            var table = new[] { new ServiceTableEntry { Name=name, Main=Marshal.GetFunctionPointerForDelegate(mainCallback) }, new ServiceTableEntry() };
            if (!StartDispatcher(table)) { Console.Error.WriteLine("SERVICE category=dispatcher_failed"); return 1; }
            return callbackFailure == 0 ? 0 : 1;
        } catch { Console.Error.WriteLine("SERVICE category=dispatcher_failed"); return 1; }
        finally { Marshal.FreeHGlobal(name); GC.KeepAlive(mainCallback); GC.KeepAlive(handlerCallback); }
    }
    public static int SelfTest()
    {
        if (ScmTests.Run() != 0) return 1;
        if (Marshal.SizeOf<ServiceStatus>() != 28 || Marshal.SizeOf<ServiceTableEntry>() != IntPtr.Size * 2 ||
            Marshal.OffsetOf<ServiceStatus>(nameof(ServiceStatus.Win32ExitCode)).ToInt64() != 12 ||
            Marshal.OffsetOf<ServiceStatus>(nameof(ServiceStatus.ServiceSpecificExitCode)).ToInt64() != 16 ||
            Marshal.OffsetOf<ServiceTableEntry>(nameof(ServiceTableEntry.Main)).ToInt64() != IntPtr.Size)
        {
            Console.WriteLine("FAIL SCM ABI layout");
            return 1;
        }
        Console.WriteLine("PASS SCM ABI layout");
        Console.WriteLine("PASS: synthetic SCM lifecycle selftest"); return 0;
    }
}
