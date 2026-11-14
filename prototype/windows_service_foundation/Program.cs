// Standalone, development-only SCM fixture.
internal static class Program
{
    enum State { Stopped, Starting, Running, Stopping, Failed }
    sealed record Evidence(bool ConfigValid, bool IdentityVerified, bool EndpointAuthenticated, bool StorageIsolated);
    sealed class Host
    {
        public State State { get; private set; } = State.Stopped;
        public bool Start(Evidence e)
        {
            if (State != State.Stopped) { State = State.Failed; return false; }
            State = State.Starting;
            if (!e.ConfigValid || !e.IdentityVerified || !e.EndpointAuthenticated || !e.StorageIsolated) { State = State.Failed; return false; }
            // Even synthetic complete evidence cannot activate this foundation.
            State = State.Failed; return false;
        }
        public void Stop() { State = State.Stopping; State = State.Stopped; }
        public bool Authorize(string operation) => false;
    }
    static int SelfTest()
    {
        var h = new Host();
        foreach (var e in new[] { new Evidence(false,false,false,false), new Evidence(true,false,false,false), new Evidence(true,true,true,true) }) {
            if (h.Start(e) || h.State != State.Failed || h.Authorize("ingest") || h.Authorize("ping")) return 1;
            h.Stop(); if (h.State != State.Stopped) return 1;
        }
        if (h.Start(new Evidence(true,false,false,false)) || h.Start(new Evidence(true,true,true,true))) return 1;
        h.Stop();
        Console.WriteLine("PASS: fail-closed service foundation selftest"); return 0;
    }
    static int Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "selftest") return SelfTest();
        if (args.Length == 1 && args[0] == "scm-selftest") return NativeScm.SelfTest();
        if (args is ["protected-fixture-selftest"]) return ProtectedFixtureTests.Run().GetAwaiter().GetResult();
        if (args is ["service-token-probe-selftest"]) return ServiceTokenProbeTests.Run().GetAwaiter().GetResult();
        if (args is ["protected-probe-gate-selftest"]) return ProtectedProbeGateTests.Run();
        if (args is ["service-hosted-probe-selftest"]) return ServiceHostedStorageProbeTests.Run();
        if (args.Length == 1 && args[0] == "service" && OperatingSystem.IsWindows()) return NativeScm.Dispatch();
        if (OperatingSystem.IsWindows() && ProtectedFixtureConfig.Parse(args) is { } fixture) return NativeScm.Dispatch(fixture);
        Console.Error.WriteLine("SERVICE_FOUNDATION category=unsupported development_only=true");
        return 2;
    }
}
