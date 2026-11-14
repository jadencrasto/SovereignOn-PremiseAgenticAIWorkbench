internal static class ServiceTokenProbeTests
{
    internal static async Task<int> Run()
    {
        var now = DateTimeOffset.Parse("2026-01-01T00:00:00Z");
        var request = new ServiceTokenProbe.Request("inspect-synthetic-storage-rights", true, true,
            "synthetic-user", "synthetic-service", 8192, now.AddSeconds(30));
        var token = new ServiceTokenProbe.Token("synthetic-user", "synthetic-service", true, true,
            true, false, false, 8192, false);
        int passed = 0, failed = 0;
        async Task Case(string name, Func<Task<bool>> test)
        {
            try { if (!await test()) throw new InvalidOperationException(); Console.WriteLine("PASS " + name); passed++; }
            catch { Console.WriteLine("FAIL " + name); failed++; }
        }
        Task<bool> Denied(ServiceTokenProbe.Request r, ServiceTokenProbe.Token t) => Task.FromResult(!ServiceTokenProbe.Matches(r, t, now));
        await Case("matching synthetic token", () => Task.FromResult(ServiceTokenProbe.Matches(request, token, now)));
        await Case("missing approval", () => Denied(request with { Approved = false }, token));
        await Case("missing VM evidence", () => Denied(request with { VmVerified = false }, token));
        await Case("wrong user", () => Denied(request, token with { User = "other" }));
        await Case("wrong service SID", () => Denied(request, token with { ServiceSid = "other" }));
        await Case("missing restricted SID", () => Denied(request, token with { ServiceRestricted = false }));
        await Case("missing enabled SID", () => Denied(request, token with { ServiceEnabled = false }));
        await Case("unrestricted token", () => Denied(request, token with { Restricted = false }));
        await Case("administrator", () => Denied(request, token with { Administrator = true }));
        await Case("excess privileges", () => Denied(request, token with { UnsafePrivileges = true }));
        await Case("impersonating thread", () => Denied(request, token with { ThreadImpersonating = true }));
        await Case("integrity mismatch", () => Denied(request, token with { IntegrityRid = 12288 }));
        await Case("expired request", () => Denied(request with { Expires = now }, token));
        await Case("excessive validity", () => Denied(request with { Expires = now.AddMinutes(2) }, token));
        await Case("unknown operation", () => Denied(request with { Operation = "execute" }, token));
        await Case("native mode unavailable", () => Task.FromResult(ServiceTokenProbe.NativeExecution() == ServiceTokenProbe.Outcome.Unsupported));
        await Case("bounded sanitized output", () => Task.FromResult(Enum.GetValues<ServiceTokenProbe.Outcome>().All(x =>
            ServiceTokenProbe.Evidence(x).Length < 100 && !ServiceTokenProbe.Evidence(x).Contains(token.User))));
        await Case("simulated timeout", async () => await ServiceTokenProbe.Simulate(request, token, now,
            TimeSpan.FromSeconds(1), TimeSpan.FromMilliseconds(5)) == ServiceTokenProbe.Outcome.Timeout);
        await Case("simulated success nonauthorizing", async () => await ServiceTokenProbe.Simulate(request, token, now,
            TimeSpan.Zero, TimeSpan.FromSeconds(1)) == ServiceTokenProbe.Outcome.SimulatedMatch);
        using var cancelled = new CancellationTokenSource(); cancelled.Cancel();
        await Case("cancelled", async () => await ServiceTokenProbe.Simulate(request, token, now,
            TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(1), cancelled.Token) == ServiceTokenProbe.Outcome.Timeout);
        Console.WriteLine($"service token probe: {passed} passed, {failed} failed");
        return failed == 0 ? 0 : 1;
    }
}
