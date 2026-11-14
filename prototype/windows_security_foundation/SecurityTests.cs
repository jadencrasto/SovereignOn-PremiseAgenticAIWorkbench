using System.Collections.Immutable;
using System.Security.Cryptography;
using System.Text.Json;
using System.Security.Principal;

internal static class SecurityTests
{
    const string Admin = "S-1-5-32-544", User = "S-1-5-21-1-2-3-1001",
        ServiceSid = "S-1-5-80-1-2-3-4-5", Other = "S-1-5-21-1-2-3-1002";
    static readonly string Hash = new('A', 64);
    static readonly AccessEvidence Acl = new(Admin, true, Hash, [new(Admin, 0x10000000, false)]);
    static readonly TrustManifest Manifest = MakeManifest();
    static readonly ExpectedIdentity Expected = Load(Manifest) ?? throw new InvalidOperationException("Fixture manifest");
    static readonly ServiceEvidence Good = MakeEvidence();

    static TrustManifest MakeManifest()
    {
        string[] paths = [@"C:\", @"C:\Protected", @"C:\Protected\ServiceFoundation.exe", @"C:\Protected\ServiceFoundation.dll",
            @"C:\Protected\ServiceFoundation.runtimeconfig.json", @"C:\Protected\ServiceFoundation.deps.json"];
        var pins = paths.Select((p, i) => new ObjectPin(p, $"0000000000000001:{i + 1:x32}", i < 2 ? null : Hash, Hash)).ToImmutableArray();
        return new(ExpectedIdentity.ServiceName, $"\"{paths[2]}\" service", @"LAB\RestrictedBroker", User, ServiceSid,
            "SovereignAI-identity-dev-0123456789abcdef0123456789abcdef", 0x2000, [Admin], pins, Hash, Hash);
    }
    static ExpectedIdentity? Load(TrustManifest manifest)
    {
        byte[] json = JsonSerializer.SerializeToUtf8Bytes(manifest);
        return ExpectedIdentity.Read(json, Convert.ToHexString(SHA256.HashData(json)));
    }
    static ServiceEvidence MakeEvidence() => new(
        new(Manifest.BinaryCommand, Manifest.Account, 16, 3, 3, 42, Hash, true),
        new(User, [ServiceSid], [ServiceSid], 0x2000, false), Expected.Executable, 42, true,
        Manifest.Installation.Select(p => new ObjectEvidence(p.Path, p.FileId, p.Sha256, false,
            p.Sha256 is null, true, 1, Acl)).ToArray());

    static ServiceEvidence ReplaceFile(Func<ObjectEvidence, ObjectEvidence> replace)
    {
        var inventory = (ObjectEvidence[])Good.Installation.Clone();
        inventory[2] = replace(inventory[2]);
        return Good with { Installation = inventory };
    }
    static void Check(bool value)
    {
        if (!value) throw new InvalidOperationException("assertion");
    }
    static void Identity(Action<string, Func<Task>> run)
    {
        void Case(string name, ServiceEvidence? evidence, Finding verdict) =>
            run(name, () => { Check(IdentityPolicy.Verify(Expected, evidence) == verdict); return Task.CompletedTask; });
        Case("expected service identity evidence", Good, Finding.VerifiedEvidence);
        Case("missing configuration snapshot", null, Finding.MissingEvidence);
        Case("wrong process PID", Good with { ProcessPid = 43 }, Finding.UnexpectedProcess);
        Case("process exited", Good with { Alive = false }, Finding.UnexpectedProcess);
        Case("unexpected token user", Good with { Token = Good.Token with { User = Other } }, Finding.MismatchedIdentity);
        Case("service SID is missing or disabled", Good with { Token = Good.Token with { EnabledGroups = [] } }, Finding.MismatchedIdentity);
        Case("restricted token SID absent", Good with { Token = Good.Token with { RestrictedGroups = [] } }, Finding.MismatchedIdentity);
        Case("invalid token integrity", Good with { Token = Good.Token with { IntegrityRid = 0x4000 } }, Finding.MismatchedIdentity);
        Case("administrative broker token", Good with { Token = Good.Token with { EnabledGroups = [ServiceSid, Admin] } }, Finding.MismatchedIdentity);
        Case("unsafe privilege even if disabled", Good with { Token = Good.Token with { UnsafePrivileges = true } }, Finding.MismatchedIdentity);
        Case("wrong service account", Good with { Configuration = Good.Configuration with { Account = "Other" } }, Finding.InvalidConfiguration);
        Case("changed service command", Good with { Configuration = Good.Configuration with { BinaryCommand = "other" } }, Finding.InvalidConfiguration);
        Case("unrestricted service SID type", Good with { Configuration = Good.Configuration with { SidType = 1 } }, Finding.InvalidConfiguration);
        Case("writable SCM configuration", Good with { Configuration = Good.Configuration with { ConfigurationProtected = false } }, Finding.InvalidConfiguration);
        Case("wrong executable hash", ReplaceFile(f => f with { Sha256 = new('B', 64) }), Finding.UntrustedInstallation);
        Case("changed executable file identity", ReplaceFile(f => f with { FileId = "different" }), Finding.UntrustedInstallation);
        Case("reparse point", ReplaceFile(f => f with { Reparse = true }), Finding.UntrustedInstallation);
        Case("path substitution", ReplaceFile(f => f with { FinalPathMatches = false }), Finding.UntrustedInstallation);
        Case("hard link", ReplaceFile(f => f with { Links = 2 }), Finding.UntrustedInstallation);
        Case("unsafe inherited write grant", ReplaceFile(f => f with { Access = Acl with { Allows = [new(Other, 2, true)] } }), Finding.UntrustedInstallation);
        Case("untrusted owner", ReplaceFile(f => f with { Access = Acl with { Owner = Other } }), Finding.UntrustedInstallation);
        Case("missing OS evidence", ReplaceFile(f => f with { Access = Acl with { Complete = false } }), Finding.UntrustedInstallation);
        run("tampered manifest, duplicates and missing trust anchor", () =>
        {
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(Manifest);
            Check(ExpectedIdentity.Read(bytes, new('0', 64)) is null &&
                IdentityPolicy.Verify(null, Good) == Finding.MissingEvidence);
            byte[] duplicate = "{\"ServiceName\":\"one\",\"ServiceName\":\"two\"}"u8.ToArray();
            Check(ExpectedIdentity.Read(duplicate, Convert.ToHexString(SHA256.HashData(duplicate))) is null);
            Check(Load(Manifest with { UserSid = "S-1-5-18" }) is null);
            return Task.CompletedTask;
        });
        run("Windows native structure layouts", () =>
        {
            Check(!OperatingSystem.IsWindowsVersionAtLeast(10) || WindowsInspector.AbiLayoutMatches());
            return Task.CompletedTask;
        });
    }

    sealed class Mock : IServiceInspector, IEndpointInspector
    {
        internal Func<int, ServiceEvidence> Observe = _ => Good;
        internal Func<int, ServiceConfiguration> Config = _ => Good.Configuration;
        internal Func<int, PipeEvidence> Pipe = _ => new(42, Acl);
        internal Func<int, int> ProcessKey = _ => 1;
        internal bool Alive = true, PipeMatches = true, FailConnect, TimeoutConnect, WaitForCancellation;
        internal readonly TaskCompletionSource ConnectEntered = new(TaskCreationOptions.RunContinuationsAsynchronously);
        internal int ConfigCalls, Leases, Released, Connected, Disconnected;
        public ServiceConfiguration ReadConfiguration(ExpectedIdentity expected) => Config(Interlocked.Increment(ref ConfigCalls));
        public IServiceLease Inspect(ExpectedIdentity expected, CancellationToken cancellation)
        {
            // Failed acquisition owns no lease.
            var evidence = Observe(Volatile.Read(ref Leases) + 1);
            int slot = Interlocked.Increment(ref Leases);
            return new Service(this, ProcessKey(slot), evidence);
        }
        public async ValueTask<IPipeLease> Connect(ExpectedIdentity expected, CancellationToken cancellation)
        {
            ConnectEntered.TrySetResult();
            if (FailConnect) throw new IOException();
            if (TimeoutConnect) throw new TimeoutException();
            if (WaitForCancellation) await Task.Delay(Timeout.InfiniteTimeSpan, cancellation);
            Interlocked.Increment(ref Connected);
            return new PipeLease(this);
        }
        sealed class Service(Mock owner, int key, ServiceEvidence evidence) : IServiceLease
        {
            public ServiceEvidence Evidence => evidence;
            public bool Alive => owner.Alive;
            public bool SameProcess(IServiceLease other) => other is Service service && service.Key == key;
            int Key => key;
            public bool MatchesPipeServer(uint pid) => owner.PipeMatches && pid == evidence.ProcessPid;
            public void Dispose() => Interlocked.Increment(ref owner.Released);
        }
        sealed class PipeLease(Mock owner) : IPipeLease
        {
            int samples;
            public PipeEvidence Inspect() => owner.Pipe(++samples);
            public void Dispose() => Interlocked.Increment(ref owner.Disconnected);
        }
    }

    static void Endpoint(Action<string, Func<Task>> run)
    {
        void Case(string name, Mock mock, Finding wanted, EvidenceScope scope = EvidenceScope.Synthetic) => run(name, async () =>
        {
            var result = await new EndpointVerifier(mock, mock, scope).Verify(Expected, DevelopmentOperation.Ping, default);
            Check(result.Finding == wanted && result.DevelopmentOperationAllowed ==
                (wanted == Finding.VerifiedEvidence && scope == EvidenceScope.Synthetic));
            Check(mock.Leases == mock.Released && mock.Connected == mock.Disconnected);
            if (wanted is Finding.InvalidConfiguration or Finding.UntrustedInstallation or Finding.MismatchedIdentity)
                Check(mock.Connected == 0);
        });
        Case("verified synthetic binding", new(), Finding.VerifiedEvidence);
        Case("native observation never authorizes", new(), Finding.VerifiedEvidence, EvidenceScope.NativeObservation);
        Case("fake or same-account squatting PID", new() { Pipe = _ => new(77, Acl) }, Finding.UnexpectedEndpoint);
        Case("same numeric PID different process handle", new() { PipeMatches = false }, Finding.UnexpectedEndpoint);
        Case("reused PID on recheck", new() { ProcessKey = i => i }, Finding.ChangedBinding);
        Case("service process exit", new() { Alive = false }, Finding.ChangedBinding);
        Case("pipe replacement", new() { Pipe = i => new(i == 1 ? 42U : 43U, Acl) }, Finding.ChangedBinding);
        Case("token acquisition denied", new() { Observe = _ => throw new UnauthorizedAccessException() }, Finding.InspectionFailure);
        Case("process acquisition failed", new() { Observe = _ => throw new InspectionException(Finding.UnexpectedProcess) }, Finding.UnexpectedProcess);
        Case("wrong executable before connect", new() { Observe = _ => Good with { ImagePath = "unknown" } }, Finding.UntrustedInstallation);
        Case("modified service configuration", new() { Config = _ => Good.Configuration with { BinaryCommand = "modified" } }, Finding.InvalidConfiguration);
        Case("unexpected service token", new() { Observe = _ => Good with { Token = Good.Token with { User = Other } } }, Finding.MismatchedIdentity);
        Case("unsafe pipe create-instance right", new() { Pipe = _ => new(42, Acl with { Allows = [new(Other, 4, false)] }) }, Finding.UnsafeEndpoint);
        Case("pipe descriptor pin mismatch", new() { Pipe = _ => new(42, Acl with { DescriptorSha256 = new('B', 64) }) }, Finding.UnsafeEndpoint);
        Case("connection interruption", new() { FailConnect = true }, Finding.EndpointUnavailable);
        Case("missing endpoint deadline", new() { TimeoutConnect = true }, Finding.EndpointUnavailable);
        Case("failed re-verification", new() { Observe = i => i == 1 ? Good : throw new InvalidOperationException() }, Finding.InspectionFailure);
        Case("service configuration changed after recheck", new() { Config = i => i == 1 ? Good.Configuration : Good.Configuration with { Pid = 43 } }, Finding.ChangedBinding);
        run("missing anchor and unsupported operations are refused before inspection", async () =>
        {
            var mock = new Mock();
            var verifier = new EndpointVerifier(mock, mock, EvidenceScope.Synthetic);
            Check(!(await verifier.Verify(null, DevelopmentOperation.Ping, default)).DevelopmentOperationAllowed &&
                !(await verifier.Verify(Expected, (DevelopmentOperation)99, default)).DevelopmentOperationAllowed &&
                mock.ConfigCalls == 0 && mock.Connected == 0);
        });
        run("cancelled connection releases process lease", async () =>
        {
            using var cancel = new CancellationTokenSource();
            var mock = new Mock { WaitForCancellation = true };
            var pending = new EndpointVerifier(mock, mock, EvidenceScope.Synthetic).Verify(Expected, DevelopmentOperation.Ping, cancel.Token);
            await mock.ConnectEntered.Task.WaitAsync(TimeSpan.FromSeconds(1));
            await cancel.CancelAsync();
            var result = await pending;
            Check(result == new BindingResult(Finding.Cancelled, false) && mock.Leases == 1 && mock.Released == 1);
        });
        run("concurrent connections have independent leases", async () =>
        {
            var mock = new Mock();
            var verifier = new EndpointVerifier(mock, mock, EvidenceScope.Synthetic);
            var results = await Task.WhenAll(Enumerable.Range(0, 12).Select(_ =>
                Task.Run(() => verifier.Verify(Expected, DevelopmentOperation.Status, default))));
            Check(results.All(r => r.DevelopmentOperationAllowed) && mock.Connected == 12 && mock.Disconnected == 12 &&
                mock.Leases == 24 && mock.Released == 24);
        });
    }

    sealed class Witness : IFreshnessWitness { internal long? Value = 0; public long? ReadGeneration() => Value; }
    static void Storage(Action<string, Func<Task>> run)
    {
        var verified = new RequestContext(Guid.NewGuid(), true, new(Finding.VerifiedEvidence, true), true);
        var safe = new ResourceProtection(true, false, false);
        StorageRequest Request() => new(Guid.NewGuid(), new(ResourceClass.Document, new('a', 32)), StorageOperation.Create, 0);
        void Case(string name, Action test) => run(name, () => { test(); return Task.CompletedTask; });
        void Decision(string name, Func<StorageRequest, StorageRequest> alter, RequestContext ctx, ResourceProtection protection,
            bool grant, long? witness, StorageDecision wanted) => Case(name, () =>
        {
            var store = new SyntheticStorage(new Witness { Value = witness });
            var request = alter(Request());
            if (grant) store.SetTestGrant(ctx, request);
            Check(store.Begin(request, ctx, protection, default) == wanted && store.Snapshot() == (0L, 0, 0, false));
        });
        Decision("missing caller identity", r => r, verified with { CallerVerified = false }, safe, true, 0, StorageDecision.IdentityUnavailable);
        Decision("missing intended broker authorization", r => r, verified with { Broker = new(Finding.VerifiedEvidence, false) }, safe, true, 0, StorageDecision.IdentityUnavailable);
        Decision("unverified recovery", r => r, verified with { RecoveryVerified = false }, safe, true, 0, StorageDecision.IdentityUnavailable);
        Decision("invalid request context", r => r, verified with { SessionId = Guid.Empty }, safe, true, 0, StorageDecision.IdentityUnavailable);
        Decision("forged operation grant", r => r, verified, safe, false, 0, StorageDecision.Unauthorized);
        Decision("invalid identifier", r => r with { Resource = r.Resource with { Id = "zero" } }, verified, safe, true, 0, StorageDecision.InvalidRequest);
        Decision("directory traversal identifier", r => r with { Resource = r.Resource with { Id = @"..\secret" } }, verified, safe, true, 0, StorageDecision.InvalidRequest);
        Decision("reparse substitution", r => r, verified, safe with { Reparse = true }, true, 0, StorageDecision.UnsafeResource);
        Decision("inherited writer", r => r, verified, safe with { InheritedWriter = true }, true, 0, StorageDecision.UnsafeResource);
        Decision("missing pin", r => r, verified, safe with { Pinned = false }, true, 0, StorageDecision.UnsafeResource);
        Decision("witness missing", r => r, verified, safe, true, null, StorageDecision.WitnessUnavailable);
        Decision("stale witness", r => r, verified, safe, true, 1, StorageDecision.Stale);
        Decision("invalid class and operation pair", r => r with { Resource = r.Resource with { Class = ResourceClass.Audit } }, verified, safe, true, 0, StorageDecision.InvalidRequest);
        Case("native access probe expectation matrix", () =>
        {
            if (!OperatingSystem.IsWindowsVersionAtLeast(10)) return;
            Check(!StorageAccessProbe.ExpectedGrant("document.fixture", 2, false) &&
                StorageAccessProbe.ExpectedGrant("document.fixture", 2, true) &&
                !StorageAccessProbe.ExpectedGrant("document.fixture", 0x40000, true) &&
                StorageAccessProbe.ExpectedGrant("audit.fixture", 4, true) &&
                !StorageAccessProbe.ExpectedGrant("audit.fixture", 2, true) &&
                !StorageAccessProbe.ExpectedGrant("witness.fixture", 4, true));
        });
        Case("synthetic authorized mutation, replay and production denial", () =>
        {
            var store = new SyntheticStorage(new Witness());
            var request = Request();
            store.SetTestGrant(verified, request, compoundIndex: true);
            Check(StoragePolicy.Production(request) == StorageDecision.Disabled &&
                store.Begin(request, verified, safe, default) == StorageDecision.Simulated &&
                store.Finish(request, verified, safe, default, companionProtection: safe) == StorageDecision.Simulated &&
                store.Snapshot() == (1L, 2, 0, false) &&
                store.Finish(request, verified, safe, default) == StorageDecision.Unauthorized);
        });
        Case("compound mutation requires separate companion protection", () =>
        {
            var store = new SyntheticStorage(new Witness());
            var request = Request();
            store.SetTestGrant(verified, request, compoundIndex: true);
            Check(store.Begin(request, verified, safe, default) == StorageDecision.Simulated &&
                store.Finish(request, verified, safe, default) == StorageDecision.UnsafeResource &&
                store.Snapshot() == (0L, 0, 0, false));
        });
        Case("single resource grant cannot mutate a second store", () =>
        {
            var witness = new Witness();
            var store = new SyntheticStorage(witness);
            var request = Request();
            store.SetTestGrant(verified, request);
            Check(store.Begin(request, verified, safe, default) == StorageDecision.Simulated &&
                store.Finish(request, verified, safe, default) == StorageDecision.Simulated &&
                store.Snapshot() == (1L, 1, 0, false));
            witness.Value = 1;
            var other = Request() with { Resource = request.Resource with { Id = new('b', 32) }, Generation = 1 };
            store.SetTestGrant(verified, other);
            Check(store.Begin(other, verified, safe, default) == StorageDecision.Simulated &&
                store.Finish(other, verified, safe, default) == StorageDecision.Simulated &&
                store.Snapshot() == (2L, 2, 0, false));
        });
        Case("attempted bypass or forged context cannot consume grant", () =>
        {
            var store = new SyntheticStorage(new Witness());
            var request = Request();
            store.SetTestGrant(verified, request);
            Check(store.Finish(request, verified, safe, default) == StorageDecision.Unauthorized &&
                store.Begin(request with { Operation = StorageOperation.Delete }, verified, safe, default) == StorageDecision.Unauthorized &&
                store.Begin(request, verified with { }, safe, default) == StorageDecision.Unauthorized &&
                store.Snapshot() == (0L, 0, 0, false));
        });
        run("concurrent synthetic writer requests", async () =>
        {
            var store = new SyntheticStorage(new Witness());
            var requests = Enumerable.Range(0, 12).Select(_ => Request()).ToArray();
            foreach (var request in requests) store.SetTestGrant(verified, request);
            var results = await Task.WhenAll(requests.Select(request => Task.Run(() => store.Begin(request, verified, safe, default))));
            Check(results.Count(x => x == StorageDecision.Simulated) == 1 && results.Count(x => x == StorageDecision.Busy) == 11);
        });
        foreach (string scenario in new[] { "revoked", "interrupted", "transaction", "partial", "rollback", "stale", "witness", "resource" })
            Case($"write completion {scenario}", () =>
            {
                var witness = new Witness();
                var store = new SyntheticStorage(witness);
                var request = Request();
                store.SetTestGrant(verified, request, compoundIndex: true);
                Check(store.Begin(request, verified, safe, default) == StorageDecision.Simulated);
                using var cancel = new CancellationTokenSource();
                if (scenario == "revoked") store.Revoke();
                if (scenario == "interrupted") cancel.Cancel();
                if (scenario == "stale") witness.Value = 1;
                if (scenario == "witness") witness.Value = null;
                var fault = scenario switch
                {
                    "transaction" => SyntheticStorage.Fault.Transaction, "partial" => SyntheticStorage.Fault.Partial,
                    "rollback" => SyntheticStorage.Fault.Rollback, _ => SyntheticStorage.Fault.None
                };
                var wanted = scenario switch
                {
                    "revoked" => StorageDecision.Revoked, "interrupted" => StorageDecision.Interrupted,
                    "rollback" => StorageDecision.RollbackFailed, "stale" => StorageDecision.Stale,
                    "witness" => StorageDecision.WitnessUnavailable, "resource" => StorageDecision.UnsafeResource,
                    _ => StorageDecision.TransactionFailed
                };
                bool quarantined = scenario == "rollback";
                Check(store.Finish(request, verified, scenario == "resource" ? safe with { Reparse = true } : safe, cancel.Token, fault, safe) == wanted &&
                    store.Snapshot() == (0L, quarantined ? 1 : 0, 0, quarantined));
                if (quarantined) Check(store.Begin(Request(), verified, safe, default) == StorageDecision.Quarantined);
            });
    }

    static void Integration(Action<string, Func<Task>> run)
    {
        var fixture = ProtectedFixtureConfig.Parse([ProtectedFixtureConfig.Mode, ProtectedFixtureConfig.Approval,
            Manifest.PipeName, User, Other]) ?? throw new InvalidOperationException();
        var manifest = Manifest with { ObserverSid = Other, BinaryCommand = fixture.Command(Expected.Executable) };
        var pinned = Load(manifest) ?? throw new InvalidOperationException();
        var current = Good.Configuration with { BinaryCommand = manifest.BinaryCommand };
        var rules = new AccessEvidence(User, true, Hash, [new(User, ProtectedFixtureConfig.OwnerRights, false),
            new(ServiceSid, ProtectedFixtureConfig.OwnerRights, false), new(Other, ProtectedFixtureConfig.ObserverRights, false)]);
        var protection = new ResourceProtection(true, false, false);
        CallerToken caller = new(Other, TokenImpersonationLevel.Identification, 12, 22);

        Mock Fixture() => new() { Config = _ => current, Observe = _ => Good with { Configuration = current }, Pipe = _ => new(42, rules) };
        static StorageRequest Request() => new(Guid.NewGuid(), new(ResourceClass.Document, new('a', 32)), StorageOperation.Create, 0);
        static Task<BindingResult> Bind(Mock mock, ExpectedIdentity? identity, EvidenceScope scope = EvidenceScope.Synthetic) =>
            new EndpointVerifier(mock, mock, scope).Verify(identity, DevelopmentOperation.Ping, default);
        void Case(string name, Action test) => run(name, () => { test(); return Task.CompletedTask; });

        Case("protected manifest rejects missing keys and wrong fixture configuration", () =>
        {
            var json = JsonSerializer.SerializeToNode(manifest)!.AsObject();
            Check(json.Remove("IntegrityRid"));
            byte[] bytes = JsonSerializer.SerializeToUtf8Bytes(json);
            Check(ExpectedIdentity.Read(bytes, Convert.ToHexString(SHA256.HashData(bytes))) is null &&
                Load(manifest with { ObserverSid = User }) is null && Load(manifest with { BinaryCommand = Manifest.BinaryCommand }) is null &&
                Load(manifest with { ObserverSid = null }) is null);
        });

        Case("protected VM access requires the restricted token and rejects privileged stand-ins", () =>
        {
            if (!OperatingSystem.IsWindowsVersionAtLeast(10)) throw new PlatformNotSupportedException();
            var broker = Good.Token;
            var application = broker with { User = Other, EnabledGroups = [], RestrictedGroups = [] };
            Check(StorageAccessProbe.RoleMatchesManifest(pinned, "broker", User) &&
                StorageAccessProbe.RoleMatchesManifest(pinned, "application", Other) &&
                StorageAccessProbe.RoleMatchesManifest(pinned, "independent", "S-1-5-21-1-2-3-1003") &&
                !StorageAccessProbe.RoleMatchesManifest(pinned, "independent", User) &&
                !StorageAccessProbe.RoleMatchesManifest(pinned, "independent", Other) &&
                !StorageAccessProbe.RoleMatchesManifest(pinned, "broker", Other) &&
                StorageAccessProbe.MatchesRole(broker, User, ServiceSid, "broker") &&
                StorageAccessProbe.MatchesRole(application, Other, ServiceSid, "independent") &&
                !StorageAccessProbe.MatchesRole(broker with { RestrictedGroups = [] }, User, ServiceSid, "broker") &&
                !StorageAccessProbe.MatchesRole(broker with { UnsafePrivileges = true }, User, ServiceSid, "broker") &&
                !StorageAccessProbe.MatchesRole(broker, User, ServiceSid, "application") &&
                !StorageAccessProbe.MatchesRole(application, User, ServiceSid, "broker"));
        });

        foreach (var scenario in new[] { "missing manifest", "unsafe descriptor", "wrong server", "service restart", "reused PID",
            "endpoint replacement", "service exit", "changed token", "changed SCM command", "native observation", "missing caller",
            "stale recovery", "missing witness", "success", "revocation" })
            run(scenario, async () =>
            {
                var mock = Fixture();
                switch (scenario)
                {
                    case "unsafe descriptor": mock.Pipe = _ => new(42, rules with { Allows = [.. rules.Allows, new(Admin, 1, false)] }); break;
                    case "wrong server": mock.Pipe = _ => new(43, rules); break;
                    case "service restart": mock.Observe = i => i == 1 ? Good with { Configuration = current } :
                        Good with { Configuration = current with { Pid = 43 }, ProcessPid = 43 }; break;
                    case "reused PID": mock.ProcessKey = i => i; break;
                    case "endpoint replacement": mock.Pipe = i => new(i == 1 ? 42U : 43U, rules); break;
                    case "service exit": mock.Pipe = i => { if (i == 2) mock.Alive = false; return new(42, rules); }; break;
                    case "changed token": mock.Observe = i => Good with { Configuration = current,
                        Token = i == 1 ? Good.Token : Good.Token with { Revision = 12 } }; break;
                    case "changed SCM command": mock.Config = _ => current with { BinaryCommand = Manifest.BinaryCommand }; break;
                }
                var result = await Bind(mock, scenario == "missing manifest" ? null : pinned,
                    scenario == "native observation" ? EvidenceScope.NativeObservation : EvidenceScope.Synthetic);
                var finding = scenario switch
                {
                    "missing manifest" => Finding.MissingEvidence, "unsafe descriptor" => Finding.UnsafeEndpoint,
                    "wrong server" => Finding.UnexpectedEndpoint, "changed SCM command" => Finding.InvalidConfiguration,
                    "service restart" or "reused PID" or "endpoint replacement" or "service exit" or "changed token" => Finding.ChangedBinding,
                    _ => Finding.VerifiedEvidence
                };
                Check(result.Finding == finding && mock.Leases == mock.Released && mock.Connected == mock.Disconnected);
                if (scenario is "missing manifest" or "changed SCM command") Check(mock.Connected == 0);
                var callerFinding = CallerVerification.Verify(Other, a => a(), () => scenario == "missing caller" ? null : caller, () => true);
                var context = new RequestContext(Guid.NewGuid(), callerFinding == CallerFinding.Matched, result, scenario != "stale recovery");
                var store = new SyntheticStorage(new Witness { Value = scenario == "missing witness" ? null : 0 });
                var request = Request();
                var issue = store.IssueCapability(request, context, protection);
                var wanted = scenario switch
                {
                    "missing witness" => StorageDecision.WitnessUnavailable,
                    "success" or "revocation" => StorageDecision.Simulated,
                    _ => StorageDecision.IdentityUnavailable
                };
                Check(issue == wanted && StoragePolicy.Production(request) == StorageDecision.Disabled);
                if (issue != StorageDecision.Simulated)
                {
                    Check(store.Begin(request, context, protection, default) != StorageDecision.Simulated);
                    return;
                }
                Check(store.Begin(request, context, protection, default) == StorageDecision.Simulated);
                if (scenario == "revocation") store.Revoke();
                Check(store.Finish(request, context, protection, default) == (scenario == "revocation" ? StorageDecision.Revoked : StorageDecision.Simulated) &&
                    store.IssueCapability(request, context, protection) != StorageDecision.Simulated && store.Snapshot().Writers == 0);
            });

        foreach (var scenario in new[] { "match", "mismatch", "missing", "changed", "anonymous", "delegated", "scope failure", "cleanup failure" })
            Case($"caller {scenario}", () =>
            {
                int attempts = 0, cleanup = 0;
                var result = CallerVerification.Verify(Other, action =>
                {
                    if (scenario == "scope failure") throw new InvalidOperationException();
                    action();
                }, () => (++attempts, scenario) switch
                {
                    (_, "mismatch") => caller with { Sid = User }, (_, "missing") => null,
                    (2, "changed") => caller with { Revision = 45 }, (_, "anonymous") => caller with { Level = TokenImpersonationLevel.Anonymous },
                    (_, "delegated") => caller with { Level = TokenImpersonationLevel.Impersonation }, _ => caller
                }, () => ++cleanup != 2 || scenario != "cleanup failure");
                var wanted = scenario switch
                {
                    "match" => CallerFinding.Matched, "mismatch" => CallerFinding.Mismatch, "missing" => CallerFinding.Missing,
                    "changed" => CallerFinding.Changed, "anonymous" or "delegated" => CallerFinding.InsufficientLevel,
                    "scope failure" => CallerFinding.Failed, _ => CallerFinding.CleanupFailed
                };
                Check(result == wanted && cleanup >= 2);
            });

        run("cancellation releases retained service lease", async () =>
        {
            var mock = Fixture(); mock.WaitForCancellation = true;
            using var cancel = new CancellationTokenSource();
            var pending = new EndpointVerifier(mock, mock, EvidenceScope.NativeObservation).Verify(pinned, DevelopmentOperation.Ping, cancel.Token);
            await mock.ConnectEntered.Task;
            cancel.Cancel();
            Check(await pending == new BindingResult(Finding.Cancelled, false) && mock.Leases == 1 && mock.Released == 1 && mock.Connected == 0);
        });

        run("scoped synthetic binding expires after process exit and on return", async () =>
        {
            var mock = Fixture();
            var verifier = new EndpointVerifier(mock, mock, EvidenceScope.Synthetic);
            var request = Request();
            var store = new SyntheticStorage(new Witness());
            RequestContext? context = null;
            Check(await verifier.WithinSyntheticBinding(pinned, default, binding =>
            {
                context = new(Guid.NewGuid(), true, binding, true);
                Check(store.IssueCapability(request, context, protection) == StorageDecision.Simulated &&
                    store.Begin(request, context, protection, default) == StorageDecision.Simulated);
                mock.Alive = false;
                Check(store.Finish(request, context, protection, default) == StorageDecision.IdentityUnavailable &&
                    store.IssueCapability(Request(), context, protection) == StorageDecision.IdentityUnavailable);
                mock.Alive = true;
                Check(!binding.IsCurrent);
            }) == new BindingResult(Finding.VerifiedEvidence, false));
            Check(context is not null && !context.Broker.IsCurrent && mock.Leases == mock.Released && mock.Disconnected == 1);
        });

        run("valid synthetic scope grants one operation and cannot escape", async () =>
        {
            var mock = Fixture();
            var store = new SyntheticStorage(new Witness());
            var request = Request();
            BindingResult? retained = null;
            var result = await new EndpointVerifier(mock, mock, EvidenceScope.Synthetic).WithinSyntheticBinding(pinned, default, binding =>
            {
                retained = binding;
                var callerMatch = CallerVerification.Verify(Other, action => action(), () => caller, () => true);
                var context = new RequestContext(Guid.NewGuid(), callerMatch == CallerFinding.Matched, binding, true);
                Check(store.IssueCapability(request, context, protection) == StorageDecision.Simulated &&
                    store.Begin(request, context, protection, default) == StorageDecision.Simulated &&
                    store.Finish(request, context, protection, default) == StorageDecision.Simulated);
            });
            Check(result == new BindingResult(Finding.VerifiedEvidence, false) && retained is { IsCurrent: false } &&
                store.Snapshot() == (1L, 1, 0, false) && StoragePolicy.Production(request) == StorageDecision.Disabled);
        });

        Case("interruption consumes one-use grant and releases writer", () =>
        {
            var store = new SyntheticStorage(new Witness());
            var context = new RequestContext(Guid.NewGuid(), true, new(Finding.VerifiedEvidence, true), true);
            var request = Request();
            Check(store.IssueCapability(request, context, protection) == StorageDecision.Simulated &&
                store.Begin(request, context, protection, default) == StorageDecision.Simulated &&
                store.Interrupt(request, context with { }) == StorageDecision.Unauthorized &&
                store.Interrupt(request, context) == StorageDecision.Interrupted &&
                store.IssueCapability(request, context, protection) == StorageDecision.Unauthorized &&
                store.Snapshot() == (0L, 0, 0, false));
        });

        foreach (var scenario in new[] { "commit", "partial", "quarantine", "witness changes during write" })
            Case($"synthetic filesystem {scenario}", () =>
            {
                if (!OperatingSystem.IsWindows()) throw new PlatformNotSupportedException();
                using var journal = new SyntheticStateJournal();
                var witness = new ChangingWitness(scenario == "witness changes during write" ? 4 : int.MaxValue);
                var store = new SyntheticStorage(witness, journal);
                var context = new RequestContext(Guid.NewGuid(), true, new(Finding.VerifiedEvidence, true), true);
                var request = Request();
                var fault = scenario switch { "partial" => SyntheticStorage.Fault.Partial, "quarantine" => SyntheticStorage.Fault.Rollback, _ => SyntheticStorage.Fault.None };
                var wanted = scenario switch { "partial" => StorageDecision.TransactionFailed, "quarantine" => StorageDecision.RollbackFailed,
                    "witness changes during write" => StorageDecision.Stale, _ => StorageDecision.Simulated };
                Check(store.IssueCapability(request, context, protection) == StorageDecision.Simulated &&
                    store.Begin(request, context, protection, default) == StorageDecision.Simulated &&
                    store.Finish(request, context, protection, default, fault) == wanted &&
                    journal.Entries == (scenario is "commit" or "quarantine" ? 1 : 0) &&
                    store.Snapshot() == (scenario == "commit" ? 1L : 0L, scenario == "commit" ? 1 : 0, 0, scenario == "quarantine"));
            });
    }

    sealed class ChangingWitness(int changeAt) : IFreshnessWitness
    {
        int reads;
        public long? ReadGeneration() => ++reads >= changeAt ? 1 : 0;
    }

    internal static async Task<int> Run(string suite)
    {
        var cases = new List<(string Name, Func<Task> Test)>();
        void Register(string name, Func<Task> test) => cases.Add((name, test));
        switch (suite) { case "identity": Identity(Register); break; case "endpoint": Endpoint(Register); break; case "storage": Storage(Register); break; case "integration": Integration(Register); break; }
        int failed = 0;
        foreach (var item in cases)
        {
            try { await item.Test().WaitAsync(TimeSpan.FromSeconds(5)); Console.WriteLine($"PASS {item.Name}"); }
            catch (Exception ex) { failed++; Console.WriteLine($"FAIL {item.Name} ({ex.GetType().Name})"); }
        }
        Console.WriteLine($"{suite}: {cases.Count - failed} passed, {failed} failed");
        return failed == 0 && cases.Count != 0 ? 0 : 1;
    }
}
