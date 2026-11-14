internal static class ServiceHostedStorageProbeTests
{
    internal static int Run()
    {
        int passed = 0, failed = 0;
        void Check(string name, Func<bool> test)
        {
            try { if (!test()) throw new InvalidOperationException(); passed++; Console.WriteLine("PASS " + name); }
            catch { failed++; Console.WriteLine("FAIL " + name); }
        }
        Check("fixed operation disabled", () => ServiceHostedStorageProbe.Dispatch(ServiceHostedStorageProbe.Operation) == ServiceHostedStorageProbe.Result.Disabled);
        Check("unknown operation disabled", () => ServiceHostedStorageProbe.Dispatch("write") == ServiceHostedStorageProbe.Result.Disabled);
        Check("no arbitrary target", () => ServiceHostedStorageProbe.Target == @"C:\SovereignVmVerification\synthetic-storage-k");
        Check("shared probe broker write", () => StorageAccessProbe.ExpectedGrant("sqlite.fixture", 2, true));
        Check("shared probe witness denied", () => !StorageAccessProbe.ExpectedGrant("witness.fixture", 2, true));
        Check("shared probe ordinary user denied", () => !StorageAccessProbe.ExpectedGrant("sqlite.fixture", 2, false));
        Check("shared probe audit append", () => StorageAccessProbe.ExpectedGrant("audit.fixture", 4, true) && !StorageAccessProbe.ExpectedGrant("audit.fixture", 2, true));
        string serviceSid = "S-1-5-80-1-2-3-4-5", user = "S-1-5-21-1-2-3-1001";
        var token = new TokenEvidence(user, [serviceSid], [serviceSid], 8192, false);
        Check("shared probe matching role", () => StorageAccessProbe.MatchesRole(token, user, serviceSid, "broker"));
        Check("shared probe wrong identity", () => !StorageAccessProbe.MatchesRole(token with { User = "other" }, user, serviceSid, "broker"));
        Check("shared probe missing restricted SID", () => !StorageAccessProbe.MatchesRole(token with { RestrictedGroups = [] }, user, serviceSid, "broker"));
        Check("shared probe forbidden privilege", () => !StorageAccessProbe.MatchesRole(token with { UnsafePrivileges = true }, user, serviceSid, "broker"));
        Check("bounded categorical evidence", () => Enum.GetValues<ServiceHostedStorageProbe.Result>().All(x => ServiceHostedStorageProbe.Evidence(x).Length < 100 && ServiceHostedStorageProbe.Evidence(x).Contains("authority=false")));
        Console.WriteLine($"service hosted probe: {passed} passed, {failed} failed");
        return failed == 0 ? 0 : 1;
    }
}
