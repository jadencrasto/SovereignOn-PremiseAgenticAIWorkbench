using System.Collections.Concurrent;

internal static class ScmTests
{
    const uint Start = ScmController.StartPending, Running = ScmController.Running,
        Stopping = ScmController.StopPending, Stopped = ScmController.Stopped;
    static readonly TimeSpan Limit = TimeSpan.FromSeconds(3);
    static TaskCompletionSource Gate() => new(TaskCreationOptions.RunContinuationsAsynchronously);
    static void Check(bool condition, string description)
    {
        if (!condition) throw new InvalidOperationException(description);
    }

    sealed class Worker : IScmWorker
    {
        public ConcurrentQueue<string> Events { get; } = new();
        public Func<CancellationToken, Task> Init = _ => Task.CompletedTask;
        public Func<CancellationToken, Task> Work = t => Task.Delay(Timeout.InfiniteTimeSpan, t);
        public Func<CancellationToken, Task> Clean = _ => Task.CompletedTask;
        public Func<ValueTask> Dispose = () => ValueTask.CompletedTask;
        public Task Initialize(CancellationToken token) { Events.Enqueue("initialize"); return Init(token); }
        public Task Run(CancellationToken token) { Events.Enqueue("work"); return Work(token); }
        public Task Cleanup(CancellationToken token) { Events.Enqueue("cleanup"); return Clean(token); }
        public ValueTask DisposeAsync() { Events.Enqueue("dispose"); return Dispose(); }
    }

    sealed class Fixture
    {
        public readonly Worker Worker = new();
        public readonly List<(uint State, ServiceError Error, bool Ack)> Reports = new();
        public readonly ScmController Controller;
        public Func<bool> Register = () => true;
        public Func<uint, bool> Publish = _ => true;
        public Action Signal = () => {};
        public Action OnRunning = () => {};
        public int Created;

        public Fixture(bool shortDeadline = false, bool shortLifetime = false)
        {
            Controller = new(() => Register(), (state, error) =>
            {
                bool ack = false;
                try
                {
                    ack = Publish(state);
                    if (state == Running && ack) OnRunning();
                    return ack;
                }
                finally { Reports.Add((state, error, ack)); }
            }, () => { Created++; return Worker; },
                shortDeadline ? TimeSpan.FromMilliseconds(200) : Limit,
                shortLifetime ? TimeSpan.FromMilliseconds(50) : Limit,
                signalHook: () => Signal());
        }

        public async Task<ScmResult> Verify(ServiceError error, ServiceError finalCode, uint acknowledged,
            string[] events, uint[] states, bool pending = false, bool valid = true, Task<ScmResult>? run = null)
        {
            var result = await (run ?? Controller.Run(valid)).WaitAsync(Limit + Limit);
            Check(result.Error == error && result.ExitCode == (error == ServiceError.None ? 0 : 1), "result and process code");
            Check(result.Acknowledged == acknowledged && result.FinalReportedError == finalCode, "acknowledgement and final code");
            Check((result.PendingShutdown != null) == pending, "pending shutdown");
            Check(Reports.Select(x => x.State).SequenceEqual(states), "publication sequence");
            Check(result.FinalAttempted == states.Contains(Stopped), "single terminal attempt");
            Check(Reports.Where(x => x.Ack).Select(x => x.State).LastOrDefault() == acknowledged, "last accepted status");
            Check(Worker.Events.SequenceEqual(events), "worker operation and disposal order");
            Check(Created == (events.Length == 0 ? 0 : 1), "worker acquisition count");
            foreach (var report in Reports)
            {
                var status = NativeScm.CreateStatus(report.State, report.Error);
                Check(status.Type == 0x10 && status.CurrentState == report.State, "service type and state");
                Check(status.ControlsAccepted == (report.State == Running ? 5U : 0U), "accepted controls");
                var expected = report.State == Stopped ? finalCode : ServiceError.None;
                Check(status.Win32ExitCode == (expected == ServiceError.None ? 0U : 1066U)
                    && status.ServiceSpecificExitCode == (uint)expected, "Win32 and service-specific codes");
                Check((status.CheckPoint, status.WaitHint) == (report.State is Start or Stopping ? (1U, 5000U) : (0U, 0U)), "pending hints");
            }
            return result;
        }
    }

    public static int Run()
    {
        int passed = 0, failed = 0;
        void Case(string name, Func<Task> test)
        {
            try { test().WaitAsync(TimeSpan.FromSeconds(8)).GetAwaiter().GetResult(); Console.WriteLine($"PASS {name}"); passed++; }
            catch (Exception ex) { Console.WriteLine($"FAIL {name} ({ex.Message})"); failed++; }
        }
        string[] initialized = ["initialize", "cleanup", "dispose"];
        string[] worked = ["initialize", "work", "cleanup", "dispose"];
        uint[] normal = [Start, Running, Stopping, Stopped];
        static Task FailedTask() => Task.FromException(new InvalidOperationException());
        void StatusFailure(string name, uint reject, ServiceError code, bool throws = false) => Case(name, async () =>
        {
            var f = new Fixture();
            f.Publish = s => s != reject || (throws ? throw new InvalidOperationException() : false);
            f.OnRunning = () => f.Controller.Control(ScmController.Stop);
            uint[] states = reject switch { Start => [Start, Stopped], Running => [Start, Running, Stopped], _ => normal };
            await f.Verify(code, reject == Stopped ? ServiceError.None : code,
                reject == Stopped ? Stopping : Stopped, reject == Start ? [] : initialized, states);
        });
        Case("normal stop and disposal", async () =>
        {
            var f = new Fixture();
            f.Worker.Work = t => { Check(f.Controller.Control(1) == 0, "stop accepted"); return Task.Delay(Timeout.Infinite, t); };
            await f.Verify(ServiceError.None, ServiceError.None, Stopped, worked, normal);
            Check(f.Controller.Control(1) == 1061 && (await f.Controller.Run(true)).ExitCode == 1, "post-terminal controls");
            Check(f.Reports.Count == 4 && f.Worker.Events.Count == 4, "no duplicate publication or disposal");
        });
        foreach (bool throws in new[] { false, true })
            Case(throws ? "registration exception" : "invalid registration handle", async () =>
            {
                var f = new Fixture { Register = () => throws ? throw new InvalidOperationException() : false };
                await f.Verify(ServiceError.Registration, ServiceError.None, 0, [], []);
            });
        StatusFailure("START_PENDING rejected", Start, ServiceError.StartStatus);
        StatusFailure("RUNNING rejected", Running, ServiceError.RunningStatus);
        StatusFailure("STOP_PENDING rejected", Stopping, ServiceError.StopStatus);
        StatusFailure("STOPPED rejected", Stopped, ServiceError.FinalStatus);
        StatusFailure("native status publisher throws", Running, ServiceError.RunningStatus, true);
        Case("initial and final publications rejected", async () =>
        {
            var f = new Fixture { Publish = _ => false };
            await f.Verify(ServiceError.StartStatus, ServiceError.StartStatus, 0, [], [Start, Stopped]);
        });
        Case("invalid configuration", async () =>
        {
            var f = new Fixture();
            await f.Verify(ServiceError.InvalidConfiguration, ServiceError.InvalidConfiguration, Stopped, [], [Start, Stopped], valid: false);
        });
        Case("initialization failure disposes", async () =>
        {
            var f = new Fixture(); f.Worker.Init = _ => FailedTask();
            await f.Verify(ServiceError.Initialization, ServiceError.Initialization, Stopped, initialized, [Start, Stopped]);
        });
        Case("STOP during active initialization", async () =>
        {
            var f = new Fixture(); var entered = Gate();
            f.Worker.Init = t => { entered.SetResult(); return Task.Delay(Timeout.Infinite, t); };
            var run = f.Controller.Run(true);
            await entered.Task.WaitAsync(Limit);
            Check(f.Controller.Control(1) == 0, "initialization stop accepted");
            await f.Verify(ServiceError.None, ServiceError.None, Stopped, initialized, [Start, Stopped], run: run);
        });
        Case("STOP immediately after RUNNING", async () =>
        {
            var f = new Fixture();
            f.OnRunning = () => Check(f.Controller.Control(1) == 0, "stop accepted");
            await f.Verify(ServiceError.None, ServiceError.None, Stopped, initialized, normal);
        });
        foreach (uint control in new[] { 1U, 5U })
            Case(control == 1 ? "repeated STOP" : "SHUTDOWN during work", async () =>
            {
                var f = new Fixture(); var entered = Gate(); var release = Gate(); int signals = 0;
                f.Signal = () => Interlocked.Increment(ref signals);
                f.Worker.Work = t => { entered.SetResult(); return Task.Delay(Timeout.Infinite, t); };
                f.Worker.Clean = _ => release.Task;
                var run = f.Controller.Run(true);
                await entered.Task.WaitAsync(Limit);
                Check(f.Controller.Control(control) == 0, "control accepted");
                if (control == 1) Check(f.Controller.Control(1) == 0, "duplicate accepted");
                release.SetResult();
                await f.Verify(ServiceError.None, ServiceError.None, Stopped, worked, normal, run: run);
                Check(signals == 1, "only one signal");
            });
        Case("concurrent STOP and SHUTDOWN", async () =>
        {
            var f = new Fixture(); var entered = Gate(); var release = Gate(); int signals = 0;
            f.Signal = () => Interlocked.Increment(ref signals);
            f.Worker.Work = t => { entered.SetResult(); return Task.Delay(Timeout.Infinite, t); };
            f.Worker.Clean = _ => release.Task;
            var run = f.Controller.Run(true);
            await entered.Task.WaitAsync(Limit);
            try
            {
                var controls = await Task.WhenAll(Task.Run(() => f.Controller.Control(1)), Task.Run(() => f.Controller.Control(5)));
                Check(controls.All(code => code == 0) && signals == 1, "concurrent controls coalesced");
            }
            finally { release.SetResult(); }
            await f.Verify(ServiceError.None, ServiceError.None, Stopped, worked, normal, run: run);
        });
        foreach (bool stop in new[] { false, true })
            Case(stop ? "worker faults during STOP" : "unexpected worker fault", async () =>
            {
                var f = new Fixture(); var entered = Gate(); var fault = Gate();
                f.Worker.Work = _ => { entered.SetResult(); return fault.Task; };
                var run = f.Controller.Run(true);
                await entered.Task.WaitAsync(Limit);
                if (stop) f.Controller.Control(1);
                fault.SetException(new InvalidOperationException());
                await f.Verify(ServiceError.Worker, ServiceError.Worker, Stopped, worked, normal, run: run);
            });
        Case("cancellation callback fails and disposal still runs", async () =>
        {
            var f = new Fixture(); var released = Gate();
            f.Worker.Work = async t =>
            {
                using var registration = t.Register(() => { released.SetResult(); throw new InvalidOperationException(); });
                f.Controller.Control(1);
                await released.Task;
            };
            await f.Verify(ServiceError.Cancellation, ServiceError.Cancellation, Stopped, worked, normal);
        });
        Case("control during cleanup", async () =>
        {
            var f = new Fixture(); var entered = Gate(); var release = Gate();
            f.OnRunning = () => f.Controller.Control(1);
            f.Worker.Clean = _ => { entered.SetResult(); return release.Task; };
            var run = f.Controller.Run(true);
            await entered.Task.WaitAsync(Limit);
            Check(f.Controller.Control(5) == 0, "duplicate during cleanup");
            release.SetResult();
            await f.Verify(ServiceError.None, ServiceError.None, Stopped, initialized, normal, run: run);
        });
        Case("cleanup failure", async () =>
        {
            var f = new Fixture(); f.OnRunning = () => f.Controller.Control(1); f.Worker.Clean = _ => FailedTask();
            await f.Verify(ServiceError.Cleanup, ServiceError.Cleanup, Stopped, initialized, normal);
        });
        Case("disposal failure", async () =>
        {
            var f = new Fixture(); f.OnRunning = () => f.Controller.Control(1); f.Worker.Dispose = () => new(FailedTask());
            await f.Verify(ServiceError.Disposal, ServiceError.Disposal, Stopped, initialized, normal);
        });
        Case("callback exception", async () =>
        {
            var f = new Fixture { Signal = () => throw new InvalidOperationException() };
            f.OnRunning = () => Check(f.Controller.Control(1) == 1064, "callback code");
            await f.Verify(ServiceError.Callback, ServiceError.Callback, Stopped, initialized, normal);
        });
        Case("initialization deadline", async () =>
        {
            var f = new Fixture(shortDeadline: true); f.Worker.Init = t => Task.Delay(Timeout.Infinite, t);
            await f.Verify(ServiceError.InitializationDeadline, ServiceError.InitializationDeadline, Stopped, initialized, [Start, Stopped]);
        });
        Case("worker lifetime deadline", async () =>
        {
            var f = new Fixture(shortLifetime: true);
            await f.Verify(ServiceError.LifetimeDeadline, ServiceError.LifetimeDeadline, Stopped, worked, normal);
        });
        foreach (string phase in new[] { "initialize", "work", "cleanup", "dispose" })
            Case($"uncooperative {phase} preserves ownership", async () =>
            {
                var f = new Fixture(shortDeadline: true); var release = Gate();
                uint[] states = [Start, Running, Stopping];
                string[] before = initialized, after = initialized;
                switch (phase)
                {
                    case "initialize":
                        f.Worker.Init = _ => release.Task;
                        states = [Start]; before = ["initialize"]; break;
                    case "work":
                        f.Worker.Work = _ => { f.Controller.Control(1); return release.Task; };
                        before = ["initialize", "work"]; after = worked; break;
                    case "cleanup":
                        f.OnRunning = () => f.Controller.Control(1);
                        f.Worker.Clean = _ => release.Task;
                        before = ["initialize", "cleanup"]; break;
                    default:
                        f.OnRunning = () => f.Controller.Control(1);
                        f.Worker.Dispose = () => new(release.Task); break;
                }
                ScmResult result;
                try { result = await f.Verify(ServiceError.ShutdownDeadline, ServiceError.None, states[^1], before, states, pending: true); }
                finally { release.TrySetResult(); }
                await result.PendingShutdown!.WaitAsync(Limit);
                Check(f.Worker.Events.SequenceEqual(after) && f.Reports.Count == states.Length, "deferred disposal, no late status");
            });
        Case("VM startup selectors are exact", () =>
        {
            const string name = "SovereignDevBrokerFoundation";
            Check(NativeScm.ParseStartup(name, 1, null) == NativeScm.Mode.Normal
                && NativeScm.ParseStartup(name, 2, "--j4-fail-initialization") == NativeScm.Mode.FailInitialization
                && NativeScm.ParseStartup(name, 2, "--j4-hold-initialization") == NativeScm.Mode.HoldInitialization
                && NativeScm.ParseStartup(name, 2, "--j4-exit-process") == NativeScm.Mode.ExitProcess
                && NativeScm.ParseStartup(name, 2, "--invalid") == NativeScm.Mode.Invalid
                && NativeScm.ParseStartup("other", 1, null) == NativeScm.Mode.Invalid
                && NativeScm.ParseStartup(name, 3, "--j4-exit-process") == NativeScm.Mode.Invalid, "startup parser");
            return Task.CompletedTask;
        });
        Case("VM initialization fault reaches real worker", async () =>
        {
            var f = new Fixture();
            await using var worker = new NativeScm.DenyOnlyWorker(NativeScm.Mode.FailInitialization);
            f.Worker.Init = worker.Initialize;
            await f.Verify(ServiceError.Initialization, ServiceError.Initialization, Stopped, initialized, [Start, Stopped]);
        });
        Case("VM held initialization reaches real worker", async () =>
        {
            var f = new Fixture(shortDeadline: true);
            await using var worker = new NativeScm.DenyOnlyWorker(NativeScm.Mode.HoldInitialization);
            f.Worker.Init = worker.Initialize;
            await f.Verify(ServiceError.InitializationDeadline, ServiceError.InitializationDeadline, Stopped, initialized, [Start, Stopped]);
        });
        Case("VM abrupt exit requests only exit code 23", async () =>
        {
            var f = new Fixture();
            int exitCode = -1;
            await using var worker = new NativeScm.DenyOnlyWorker(NativeScm.Mode.ExitProcess,
                _ => Task.CompletedTask, code => { exitCode = code; f.Controller.Control(1); });
            f.Worker.Work = worker.Run;
            await f.Verify(ServiceError.None, ServiceError.None, Stopped, worked, normal);
            Check(exitCode == 23, "fault exit code");
        });
        Console.WriteLine($"SCM tests: {passed} passed, {failed} failed");
        return failed == 0 ? 0 : 1;
    }
}
