using System.Runtime.InteropServices;

internal static class ProtectedFixtureTests
{
    const string Broker = "S-1-5-21-1-2-3-1001", Observer = "S-1-5-21-1-2-3-1002";
    static readonly string[] Args = [ProtectedFixtureConfig.Mode, ProtectedFixtureConfig.Approval,
        "SovereignAI-identity-dev-" + new string('a', 32), Broker, Observer];

    sealed class Owner : IDisposable
    {
        internal int Disposals;
        public void Dispose() => Disposals++;
    }

    static void Check(bool value)
    {
        if (!value) throw new InvalidOperationException();
    }

    internal static async Task<int> Run()
    {
        int passed = 0, failed = 0;
        async Task Test(string name, Func<Task> test)
        {
            try { await test().WaitAsync(TimeSpan.FromSeconds(5)); passed++; Console.WriteLine($"PASS {name}"); }
            catch { failed++; Console.WriteLine($"FAIL {name}"); }
        }

        await Test("protected configuration and exact least-privilege descriptor", () =>
        {
            var config = ProtectedFixtureConfig.Parse(Args);
            Check(config is not null && config.Descriptor("S-1-5-80-1-2-3-4-5") ==
                $"O:{Broker}D:P(A;;0x0012019F;;;{Broker})(A;;0x0012019F;;;S-1-5-80-1-2-3-4-5)(A;;0x00120081;;;{Observer})");
            Check((ProtectedFixtureConfig.ObserverRights & 0x500D0004) == 0);
            Check(Marshal.SizeOf<ProtectedFixture.SecurityAttributes>() == (IntPtr.Size == 8 ? 24 : 12));
            return Task.CompletedTask;
        });
        await Test("fixture rejects same account, missing approval and nonlocal names", () =>
        {
            Check(ProtectedFixtureConfig.Parse([.. Args[..4], Broker]) is null &&
                ProtectedFixtureConfig.Parse([.. Args[..2], @"\\remote\pipe\x", Broker, Observer]) is null &&
                ProtectedFixtureConfig.Parse([Args[0], "--other", .. Args[2..]]) is null &&
                ProtectedFixtureConfig.Parse([.. Args[..3], "S-1-5-19", Observer]) is null &&
                NativeScm.ParseStartup(ProtectedFixtureConfig.ServiceName, 2, "--j4-exit-process", fixture: true) == NativeScm.Mode.Invalid);
            return Task.CompletedTask;
        });
        foreach (var mode in new[] { "identity rejected", "occupied endpoint", "duplicate instance", "invalid startup",
            "STOP while waiting", "SHUTDOWN after connect", "connection interruption", "connection deadline", "unexpected worker exit" })
            await Test(mode, async () =>
            {
                var owner = new Owner();
                var entered = new TaskCompletionSource(TaskCreationOptions.RunContinuationsAsynchronously);
                var states = new List<uint>();
                int opens = 0, accepts = 0;
                var worker = new ProtectedFixtureWorker(() =>
                {
                    opens++;
                    if (mode is "identity rejected" or "occupied endpoint" or "duplicate instance") throw new InvalidOperationException();
                    return owner;
                }, async (lease, token) =>
                {
                    Check(ReferenceEquals(lease, owner));
                    accepts++;
                    entered.TrySetResult();
                    if (mode == "connection interruption") throw new IOException();
                    if (mode == "unexpected worker exit") throw new InvalidOperationException();
                    if (mode != "SHUTDOWN after connect") await Task.Delay(Timeout.InfiniteTimeSpan, token);
                }, mode == "connection deadline" ? TimeSpan.Zero : TimeSpan.FromSeconds(2));
                var controller = new ScmController(() => true, (state, _) => { states.Add(state); return true; },
                    () => worker, TimeSpan.FromSeconds(2), TimeSpan.FromSeconds(3));
                var execution = controller.Run(mode != "invalid startup");
                if (mode is "STOP while waiting" or "SHUTDOWN after connect")
                {
                    await entered.Task;
                    Check(owner.Disposals == 0 && controller.Control(mode == "STOP while waiting" ? ScmController.Stop : ScmController.Shutdown) == 0);
                }
                var result = await execution;
                var wanted = mode switch
                {
                    "identity rejected" or "occupied endpoint" or "duplicate instance" => ServiceError.Initialization,
                    "invalid startup" => ServiceError.InvalidConfiguration,
                    "STOP while waiting" or "SHUTDOWN after connect" => ServiceError.None,
                    _ => ServiceError.Worker
                };
                bool enteredRunning = wanted is ServiceError.None or ServiceError.Worker;
                Check(result.Error == wanted && result.Acknowledged == ScmController.Stopped &&
                    states.SequenceEqual(enteredRunning ? new uint[] { 2, 4, 3, 1 } : [2U, 1U]) &&
                    owner.Disposals == (enteredRunning ? 1 : 0) && accepts == (enteredRunning ? 1 : 0) &&
                    opens == (mode == "invalid startup" ? 0 : 1));
            });
        Console.WriteLine($"protected fixture: {passed} passed, {failed} failed");
        return failed == 0 ? 0 : 1;
    }
}
