internal static class ProtectedProbeGateTests
{
    internal static int Run()
    {
        var now = DateTimeOffset.Parse("2026-01-01T00:00:00Z");
        var id = Guid.Parse("11111111-1111-1111-1111-111111111111");
        var nonce = Guid.Parse("22222222-2222-2222-2222-222222222222");
        var hash = new string('a', 64);
        var approval = new ProtectedProbeGate.Approval("Protected", id, "VM", "snapshot-1",
            "inspect-synthetic-storage-rights", @"C:\SovereignVmVerification\synthetic-storage-k", hash, nonce, now.AddSeconds(30));
        var context = new ProtectedProbeGate.Context(id, "VM", hash, true, true, true);
        int passed = 0, failed = 0;
        void Case(string name, Func<bool> test)
        {
            try { if (!test()) throw new InvalidOperationException(); Console.WriteLine("PASS " + name); passed++; }
            catch { Console.WriteLine("FAIL " + name); failed++; }
        }
        bool Reject(ProtectedProbeGate.Approval a, ProtectedProbeGate.Context c) => !new ProtectedProbeGate().Consume(a, c, now);
        Case("valid synthetic approval", () => new ProtectedProbeGate().Consume(approval, context, now));
        Case("wrong VM", () => Reject(approval, context with { VmId = Guid.NewGuid() }));
        Case("wrong computer", () => Reject(approval, context with { Computer = "other" }));
        Case("manifest mismatch", () => Reject(approval, context with { ManifestDigest = new string('b', 64) }));
        Case("invalid digest", () => Reject(approval with { ManifestDigest = new string('z', 64) }, context));
        Case("missing package verification", () => Reject(approval, context with { PackageVerified = false }));
        Case("missing independent approval", () => Reject(approval, context with { IndependentApproval = false }));
        Case("missing snapshot", () => Reject(approval, context with { SnapshotConfirmed = false }));
        Case("wrong profile", () => Reject(approval with { Profile = "Lifecycle" }, context));
        Case("wrong operation", () => Reject(approval with { Operation = "write" }, context));
        Case("substituted target", () => Reject(approval with { Target = approval.Target + @"\.." }, context));
        Case("expired", () => Reject(approval with { Expires = now }, context));
        Case("too long", () => Reject(approval with { Expires = now.AddMinutes(2) }, context));
        Case("replay", () => { var g = new ProtectedProbeGate(); return g.Consume(approval, context, now) && !g.Consume(approval, context, now); });
        Case("concurrent replay", () => { var g = new ProtectedProbeGate(); int success = 0; Parallel.For(0, 20, _ => { if (g.Consume(approval, context, now)) Interlocked.Increment(ref success); }); return success == 1; });
        Console.WriteLine($"protected probe gate: {passed} passed, {failed} failed");
        return failed == 0 ? 0 : 1;
    }
}
