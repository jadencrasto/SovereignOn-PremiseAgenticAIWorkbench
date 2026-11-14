internal static class Program
{
    public static async Task<int> Main(string[] args)
    {
        if (OperatingSystem.IsWindows())
        {
            if (args is ["recovery-selftest"]) return await RecoveryTests.Run();
            if (args is ["recovery-fixture-child", var fixtureId, var operation, var setting] &&
                Guid.TryParseExact(fixtureId, "N", out var id)) return RecoveryTests.Child(id, operation, setting);
        }
        if (args is ["selftest", var suite] && suite is "identity" or "endpoint" or "storage" or "integration")
            return await SecurityTests.Run(suite);
        if (args is ["probe-protected-pipe-denial", "--approved-vm-observation", var deniedManifest, var deniedDigest, var callerSid] &&
            OperatingSystem.IsWindowsVersionAtLeast(10))
        {
            string observed = "Unavailable";
            try
            {
                if (ReadExpected(deniedManifest, deniedDigest) is { Fixture: { } config } expected &&
                    StorageAccessProbe.RoleMatchesManifest(expected, "independent", callerSid) && IsNonElevatedCaller(callerSid))
                    observed = WindowsEndpoint.ObserveAccessDenial(config);
            }
            catch { observed = "Unavailable"; }
            Console.WriteLine($"PIPE_ACCESS observation={observed} authority=false");
            return observed == "Denied" ? 0 : 1;
        }
        if (args is [var storageMode, var approval, var protectedRoot, var protectedRole, var protectedSid, var storageManifest, var storageDigest] &&
            ((storageMode == "probe-protected-storage" && approval == "--approved-vm-observation") ||
             (storageMode == "exercise-protected-storage" && approval == "--approved-vm-synthetic-write")) &&
            OperatingSystem.IsWindowsVersionAtLeast(10))
        {
            int result = 2;
            try
            {
                if (ReadExpected(storageManifest, storageDigest) is { Fixture: not null } expected &&
                    StorageAccessProbe.RoleMatchesManifest(expected, protectedRole, protectedSid))
                    result = StorageAccessProbe.Run(protectedRoot, protectedRole, protectedSid, expected.Manifest.ServiceSid,
                        exercise: storageMode == "exercise-protected-storage");
            }
            catch { result = 1; }
            Console.WriteLine($"STORAGE observation={(result == 0 ? "ExpectedAccess" : "Rejected")} authority=false");
            return result;
        }
        if (args is ["probe-storage", "--approved-vm-observation", var root, var role, var sid] &&
            OperatingSystem.IsWindowsVersionAtLeast(10))
        {
            int result = StorageAccessProbe.Run(root, role, sid);
            Console.WriteLine($"STORAGE observation={(result == 0 ? "ExpectedAccess" : "Rejected")} authority=false");
            return result;
        }
        if (args is not [var command, "--approved-vm-observation", var manifestPath, var digest] ||
            command is not ("observe-identity" or "observe-endpoint" or "observe-protected-endpoint"))
        {
            Console.WriteLine("REFUSED category=approval_or_arguments");
            return 2;
        }
        if (!OperatingSystem.IsWindowsVersionAtLeast(10))
        {
            Console.WriteLine("OBSERVATION finding=NativeUnavailable authority=false");
            return 1;
        }
        Finding finding;
        try
        {
            var expected = ReadExpected(manifestPath, digest);
            using var timeout = new CancellationTokenSource(TimeSpan.FromSeconds(8));
            var inspector = new WindowsInspector();
            if (expected is null) finding = Finding.MissingEvidence;
            else if (command == "observe-protected-endpoint" && expected.Fixture is not { }) finding = Finding.InvalidConfiguration;
            else if (expected.Fixture is { } fixture && !IsNonElevatedCaller(fixture.ObserverSid)) finding = Finding.MismatchedIdentity;
            else if (command == "observe-identity")
            {
                using var service = inspector.Inspect(expected, timeout.Token);
                finding = IdentityPolicy.Verify(expected, service.Evidence);
            }
            else
            {
                var verifier = new EndpointVerifier(inspector, new WindowsEndpoint(), EvidenceScope.NativeObservation);
                finding = (await verifier.Verify(expected, DevelopmentOperation.Ping, timeout.Token)).Finding;
            }
        }
        catch (InspectionException ex) { finding = ex.Finding; }
        catch (OperationCanceledException) { finding = Finding.Cancelled; }
        catch (Exception) { finding = Finding.InspectionFailure; }
        Console.WriteLine($"OBSERVATION finding={finding} authority=false");
        return finding == Finding.VerifiedEvidence ? 0 : 1;
    }

    static ExpectedIdentity? ReadExpected(string path, string digest)
    {
        if (!ExpectedIdentity.IsHash(digest)) return null;
        using var manifest = File.Open(path, FileMode.Open, FileAccess.Read, FileShare.Read);
        var length = manifest.Length;
        if (length is <= 0 or > 65536) return null;
        var bytes = new byte[length];
        manifest.ReadExactly(bytes);
        return ExpectedIdentity.Read(bytes, digest);
    }

    [System.Runtime.Versioning.SupportedOSPlatform("windows")]
    static bool IsNonElevatedCaller(string expectedSid)
    {
        using var identity = System.Security.Principal.WindowsIdentity.GetCurrent();
        return identity.User?.Value == expectedSid &&
            !new System.Security.Principal.WindowsPrincipal(identity).IsInRole(System.Security.Principal.WindowsBuiltInRole.Administrator);
    }
}
