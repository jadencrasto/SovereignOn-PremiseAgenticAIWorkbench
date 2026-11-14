using System.Diagnostics;
using System.Reflection;
using System.Runtime.Versioning;

[SupportedOSPlatform("windows")]
internal static class RecoveryTests
{
    const string Key = "ffffffffffffffffffffffffffffffff";
    static readonly Guid Transaction = new("eeef20fe-502e-41a6-b7f9-77b71d8c89dc");
    static readonly ResourceProtection Safe = new(true, false, false);
    static readonly string[] Milestones = ["before-intent", "after-intent", "during-write", "after-data-flush", "before-commit", "after-commit"];
    static RequestContext Context(bool allow = true) => new(Guid.NewGuid(), allow, new(Finding.VerifiedEvidence, allow), allow);

    internal static int Child(Guid id, string operation, string setting)
    {
        if (operation is not ("write" or "recover" or "hold") ||
            (operation == "write" && setting is not ("normal" or "duplicate" or "all-stores" or "next-during-write" or "revoke") && !Milestones.Contains(setting)) ||
            (operation == "recover" && setting is not ("0" or "1" or "5" or "denied" or "rollback-fails")) ||
            (operation == "hold" && setting != "lease")) return 2;
        try
        {
            using var files = new DurableFixture(id);
            SyntheticStorage? runningStorage = null;
            using var recovery = new DurableRecovery(files, new FixtureWitness(files), step =>
            {
                if (operation == "write" && (step == setting || (step == "during-write" && setting == "next-during-write"))) Environment.Exit(70);
                if (operation == "write" && step == "after-data-flush" && setting == "revoke") runningStorage!.Revoke();
            });
            if (operation == "hold")
            {
                Console.WriteLine("LeaseHeld");
                Thread.Sleep(TimeSpan.FromSeconds(10));
                return 2;
            }
            long generation = setting switch { "1" or "duplicate" or "next-during-write" => 1, "5" => 5, _ => 0 };
            var ctx = Context(setting != "denied");
            var review = new StorageRequest(Guid.NewGuid(), new(ResourceClass.Recovery, Key), StorageOperation.Recover, generation);
            var result = recovery.Reconcile(review, ctx, Safe, setting == "rollback-fails");
            if (operation == "recover" || result.Decision != StorageDecision.Simulated)
            {
                Console.WriteLine($"{result.Decision} {result.Generation} {result.Entries} {result.Resolution}");
                return 0;
            }
            var storage = runningStorage = recovery.Coordinator();
            foreach (var store in setting == "all-stores" ? DurableRecovery.Stores :
                setting == "next-during-write" ? [ResourceClass.SqliteState] : [ResourceClass.Document])
            {
                var operationType = store == ResourceClass.Audit ? StorageOperation.Append : StorageOperation.Create;
                var write = new StorageRequest(generation == 0 || setting == "duplicate" ? Transaction : Guid.NewGuid(),
                    new(store, Key), operationType, generation);
                var decision = storage.IssueCapability(write, ctx, Safe);
                if (decision == StorageDecision.Simulated) decision = storage.Begin(write, ctx, Safe, default);
                if (decision == StorageDecision.Simulated) decision = storage.Finish(write, ctx, Safe, default);
                if (decision != StorageDecision.Simulated) { Console.WriteLine($"Write {decision} {storage.Snapshot().Generation}"); return 0; }
                generation++;
            }
            Console.WriteLine($"Write Simulated {generation}");
            return 0;
        }
        catch (FixtureBusy) { Console.WriteLine("Busy"); return 0; }
        catch (Exception ex) { Console.WriteLine($"Failed {ex.GetType().Name}"); return 1; }
    }

    static Process Start(Guid id, string operation, string setting)
    {
        string host = Environment.ProcessPath ?? throw new InvalidOperationException();
        var info = new ProcessStartInfo(host) { UseShellExecute = false, RedirectStandardOutput = true, CreateNoWindow = true };
        if (Path.GetFileNameWithoutExtension(host).Equals("dotnet", StringComparison.OrdinalIgnoreCase))
            info.ArgumentList.Add(Assembly.GetExecutingAssembly().Location);
        foreach (var arg in new[] { "recovery-fixture-child", id.ToString("N"), operation, setting }) info.ArgumentList.Add(arg);
        return Process.Start(info) ?? throw new InvalidOperationException();
    }

    static async Task Expect(Guid id, string operation, string setting, string output, int code = 0)
    {
        using var child = Start(id, operation, setting);
        try
        {
            var read = child.StandardOutput.ReadToEndAsync();
            await child.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(8));
            string observed = (await read.WaitAsync(TimeSpan.FromSeconds(2))).Trim();
            if (child.ExitCode != code || observed != output) throw new InvalidOperationException($"expected {output}/{code}, got {observed}/{child.ExitCode}");
        }
        finally { Stop(child); }
    }

    static void Stop(Process child)
    {
        if (child.HasExited) return;
        try { child.Kill(); }
        catch (InvalidOperationException) when (child.HasExited) { return; }
        if (!child.WaitForExit(2000)) throw new TimeoutException("child_cleanup");
    }

    internal static async Task<int> Run()
    {
        int pass = 0, fail = 0;
        async Task Test(string name, Func<DurableFixture, Task> run)
        {
            try
            {
                using var fixture = new DurableFixture(Guid.NewGuid(), create: true);
                DurableRecovery.Initialize(fixture);
                try { await run(fixture); }
                finally { fixture.Cleanup(); }
                Console.WriteLine($"PASS {name}"); pass++;
            }
            catch (Exception ex) { Console.WriteLine($"FAIL {name}: {ex.Message}"); fail++; }
        }

        foreach (var step in Milestones)
            await Test(step, async f =>
            {
                await Expect(f.Id, "write", step, "", 70);
                string first = step switch
                {
                    "before-intent" => "Simulated 0 0 Clean", "after-commit" => "Simulated 1 1 Clean",
                    "before-commit" => "Quarantined 0 0 WitnessDisagreement", _ => "Simulated 0 0 Aborted"
                };
                string second = step == "before-commit" ? "Quarantined 0 0 PreviouslyQuarantined" : first.Replace("Aborted", "Clean");
                string gen = step == "after-commit" ? "1" : "0";
                await Expect(f.Id, "recover", gen, first);
                await Expect(f.Id, "recover", gen, second);
                if (first.EndsWith("Aborted", StringComparison.Ordinal) && f.Members().Any(DurableFixture.IsStore)) throw new IOException("orphan_store");
            });

        await Test("normal commit, all five store types and restart", async f =>
        {
            await Expect(f.Id, "write", "all-stores", "Write Simulated 5");
            await Expect(f.Id, "recover", "5", "Simulated 5 5 Clean");
        });

        await Test("duplicate transaction after restart", async f =>
        {
            await Expect(f.Id, "write", "normal", "Write Simulated 1");
            await Expect(f.Id, "write", "duplicate", "Write Unauthorized 1");
            await Expect(f.Id, "recover", "1", "Simulated 1 1 Clean");
        });

        await Test("aborting a partial later generation preserves committed stores", async f =>
        {
            await Expect(f.Id, "write", "normal", "Write Simulated 1");
            await Expect(f.Id, "write", "next-during-write", "", 70);
            await Expect(f.Id, "recover", "1", "Simulated 1 1 Aborted");
            if (f.Members().Count(DurableFixture.IsStore) != 5) throw new IOException("orphan_store");
            await Expect(f.Id, "recover", "1", "Simulated 1 1 Clean");
        });

        await Test("revocation after data flush blocks publication and survives reconnect as an abort", async f =>
        {
            await Expect(f.Id, "write", "revoke", "Write Revoked 0");
            await Expect(f.Id, "recover", "0", "Simulated 0 0 Clean");
            if (f.Members().Any(DurableFixture.IsStore) || RecoveryFrame.Parse(f.Read("journal"))[^1].Kind != "R") throw new IOException("revocation_not_recorded");
        });

        foreach (var fault in new[] { "torn journal", "corrupt journal", "missing segment", "missing journal", "conflicting generation",
            "missing witness", "witness rollback", "missing store", "conflicting store", "conflicting witness" })
            await Test(fault, async f =>
            {
                byte[] initialWitness = f.Read("head", true)!;
                await Expect(f.Id, "write", "normal", "Write Simulated 1");
                var log = f.Read("journal")!;
                var frames = RecoveryFrame.Parse(log);
                string expectation = "Quarantined 0 0 InvalidEvidence";
                switch (fault)
                {
                    case "torn journal": f.Write("journal", log[..^3], false); break;
                    case "corrupt journal": log[5] ^= 1; f.Write("journal", log, false); break;
                    case "missing segment": f.Write("journal", [.. frames[0].Encode(), .. frames[1].Encode()], false); expectation = "Quarantined 0 0 WitnessDisagreement"; break;
                    case "missing journal": if (!f.Remove("journal")) throw new IOException(); break;
                    case "conflicting generation": f.Write("journal", (frames[1] with { Generation = 3, Previous = frames[^1].Hash }).Encode(), false, append: true); break;
                    case "missing witness": if (!f.Remove("head", true)) throw new IOException(); break;
                    case "witness rollback": f.Write("head", initialWitness, false, true); expectation = "Quarantined 0 0 WitnessDisagreement"; break;
                    case "missing store": if (!f.Remove($"{Transaction:N}-2")) throw new IOException(); break;
                    case "conflicting store": f.Write($"{Transaction:N}-3", [1], false); break;
                    case "conflicting witness":
                        var witnessFrames = RecoveryFrame.Parse(f.Read("head", true));
                        f.Write("head", [.. witnessFrames[0].Encode(), .. (witnessFrames[1] with { Digest = RecoveryFrame.Zero }).Encode()], false, true);
                        expectation = "Quarantined 0 0 WitnessDisagreement";
                        break;
                }
                await Expect(f.Id, "recover", "1", expectation);
                await Expect(f.Id, "recover", "1", "Quarantined 0 0 PreviouslyQuarantined");
            });

        await Test("failed rollback persists quarantine", async f =>
        {
            await Expect(f.Id, "write", "during-write", "", 70);
            await Expect(f.Id, "recover", "rollback-fails", "Quarantined 0 0 RollbackFailed");
            await Expect(f.Id, "recover", "0", "Quarantined 0 0 PreviouslyQuarantined");
        });
        await Test("unauthorized and stale recovery are non-mutating", async f =>
        {
            await Expect(f.Id, "write", "during-write", "", 70);
            byte[] before = f.Read("journal")!;
            await Expect(f.Id, "recover", "denied", "Unauthorized 0 0 Denied");
            await Expect(f.Id, "recover", "1", "Unauthorized 0 0 Denied");
            if (!before.AsSpan().SequenceEqual(f.Read("journal")) || f.Read("quarantine") is not null) throw new IOException();
            await Expect(f.Id, "recover", "0", "Simulated 0 0 Aborted");
        });
        await Test("competing recovery uses OS sharing fence and process exit releases it", async f =>
        {
            using var holder = Start(f.Id, "hold", "lease");
            try
            {
                if (await holder.StandardOutput.ReadLineAsync().WaitAsync(TimeSpan.FromSeconds(5)) != "LeaseHeld") throw new IOException();
                await Expect(f.Id, "recover", "0", "Busy");
            }
            finally { Stop(holder); }
            await Expect(f.Id, "recover", "0", "Simulated 0 0 Clean");
        });
        Console.WriteLine($"Recovery: {pass} passed, {fail} failed");
        return fail == 0 ? 0 : 1;
    }
}
