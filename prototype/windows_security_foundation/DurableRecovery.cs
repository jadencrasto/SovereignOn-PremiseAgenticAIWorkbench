using System.Globalization;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Text;

internal sealed record RecoveryFrame(string Kind, long Generation, Guid Transaction, string Digest, string Previous,
    string WitnessLink = "")
{
    internal static readonly string Zero = new('0', 64);
    string Body => FormattableString.Invariant($"{Kind}|{Generation}|{Transaction:N}|{Digest}|{Previous}|{WitnessLink}");
    internal string Hash => HashBytes(Encoding.ASCII.GetBytes(Body));
    internal byte[] Encode() => Encoding.ASCII.GetBytes($"{Body}|{Hash}\n");
    internal static string HashBytes(byte[] bytes) => Convert.ToHexString(SHA256.HashData(bytes));

    internal static List<RecoveryFrame> Parse(byte[]? bytes)
    {
        if (bytes is not { Length: > 0 } || bytes[^1] != '\n' || bytes.Any(b => b > 127)) throw new IOException("torn_record");
        var frames = new List<RecoveryFrame>();
        var previous = Zero;
        foreach (string line in Encoding.ASCII.GetString(bytes).Split('\n')[..^1])
        {
            if (line.Length > 320) throw new IOException("record_limit");
            var parts = line.Split('|');
            if (frames.Count == 96 || parts.Length != 7 || !long.TryParse(parts[1], NumberStyles.None, CultureInfo.InvariantCulture, out long generation) ||
                generation > 64 || !Guid.TryParseExact(parts[2], "N", out var transaction) || transaction == Guid.Empty ||
                parts[0] is not ("A" or "I" or "C" or "R" or "W") ||
                !ExpectedIdentity.IsHash(parts[3]) || parts[4] != previous ||
                (parts[0] == "C" ? !ExpectedIdentity.IsHash(parts[5]) : parts[5].Length != 0)) throw new IOException("invalid_record");
            var record = new RecoveryFrame(parts[0], generation, transaction, parts[3], previous, parts[5]);
            if (!record.Encode().AsSpan().SequenceEqual(Encoding.ASCII.GetBytes(line + "\n"))) throw new IOException("record_checksum");
            frames.Add(record);
            previous = record.Hash;
        }
        return frames;
    }
}

internal sealed record WitnessHead(Guid Deployment, long Generation, long Sequence, Guid Transaction, string Commitment,
    string Link, string PreviousLink);

// A production provider would need independent administration, authentication and
// an atomic compare-and-advance outside the recovery domain. Only a fixture exists.
internal interface IDurableFreshnessWitness
{
    WitnessHead ReadVerified(Guid deployment);
    bool TryAdvance(WitnessHead expected, Guid transaction, string commitment);
}

[SupportedOSPlatform("windows")]
internal sealed class FixtureWitness(DurableFixture files) : IDurableFreshnessWitness
{
    internal void Initialize(RecoveryFrame anchor) => files.Write("head", (anchor with { Kind = "W" }).Encode(), create: true, witness: true);

    public WitnessHead ReadVerified(Guid deployment)
    {
        var frames = RecoveryFrame.Parse(files.Read("head", true));
        if (frames[0] != (DurableRecovery.Anchor(deployment) with { Kind = "W" }) ||
            frames.Where((f, i) => f.Kind != "W" || f.Generation != i).Any() ||
            frames.Select(f => f.Transaction).Distinct().Count() != frames.Count) throw new IOException("witness_disagreement");
        var last = frames[^1];
        return new(deployment, last.Generation, frames.Count - 1, last.Transaction, last.Digest, last.Hash, last.Previous);
    }

    public bool TryAdvance(WitnessHead expected, Guid transaction, string commitment)
    {
        if (ReadVerified(expected.Deployment) != expected) return false;
        var next = new RecoveryFrame("W", expected.Generation + 1, transaction, commitment, expected.Link);
        files.Write("head", next.Encode(), create: false, witness: true, append: true);
        return ReadVerified(expected.Deployment) == new WitnessHead(expected.Deployment, next.Generation,
            expected.Sequence + 1, transaction, commitment, next.Hash, expected.Link);
    }
}

[SupportedOSPlatform("windows")]
internal sealed class DurableRecovery(DurableFixture files, IDurableFreshnessWitness witness, Action<string>? milestone = null)
    : ISyntheticStateJournal, IFreshnessWitness, IDisposable
{
    readonly Microsoft.Win32.SafeHandles.SafeFileHandle lease = files.Acquire();
    readonly object gate = new();
    internal static readonly ResourceClass[] Stores = [ResourceClass.Document, ResourceClass.SqliteState, ResourceClass.VectorIndex,
        ResourceClass.Artifact, ResourceClass.Audit];
    internal sealed record Result(StorageDecision Decision, long Generation, int Entries, string Resolution);
    RecoveryFrame? head;
    WitnessHead? witnessed;
    readonly HashSet<Guid> seen = [];
    Dictionary<ResourceKey, long> snapshot = [];
    RecoveryFrame? tail;
    int framesUsed;
    bool inspected, issuedCoordinator;

    internal static RecoveryFrame Anchor(Guid id) => new("A", 0, id, RecoveryFrame.HashBytes(id.ToByteArray()), RecoveryFrame.Zero);

    internal static void Initialize(DurableFixture files)
    {
        files.Write("lease", [], create: true);
        var anchor = Anchor(files.Id);
        files.Write("journal", anchor.Encode(), create: true);
        new FixtureWitness(files).Initialize(anchor);
    }

    internal Result Reconcile(StorageRequest request, RequestContext context, ResourceProtection protection, bool failRollback = false)
    {
        lock (gate) return ReconcileCore(request, context, protection, failRollback);
    }

    Result ReconcileCore(StorageRequest request, RequestContext context, ResourceProtection protection, bool failRollback)
    {
        // Apply the existing context checks before reading the journal. Issue
        // and reserve the capability against verified evidence before rollback.
        if (request.Resource.Class != ResourceClass.Recovery || request.Operation != StorageOperation.Recover)
            return new(StorageDecision.Unauthorized, 0, 0, "Denied");
        if (StoragePolicy.Evaluate(request, context, protection, true, request.Generation, request.Generation) != StorageDecision.Simulated)
            return new(StorageDecision.Unauthorized, 0, 0, "Denied");
        if (lease.IsClosed || inspected) return new(StorageDecision.Unauthorized, 0, 0, "LeaseUsed");
        inspected = true;
        try
        {
            if (files.Read("quarantine") is not null) return Quarantine("PreviouslyQuarantined");
            var log = RecoveryFrame.Parse(files.Read("journal"));
            if (log[0] != Anchor(files.Id)) throw new IOException("anchor_missing");
            var committed = log[0];
            RecoveryFrame? pending = null;
            var known = new HashSet<string>(["journal", "lease", "witness"], StringComparer.Ordinal);
            seen.Add(files.Id);
            foreach (var frame in log.Skip(1))
            {
                if (frame.Generation != committed.Generation + 1) throw new IOException("generation_conflict");
                if (frame.Kind == "I" && pending is null && seen.Add(frame.Transaction)) pending = frame;
                else if (pending is not null && frame.Transaction == pending.Transaction && frame.Digest == pending.Digest)
                {
                    if (frame.Kind == "C")
                    {
                        snapshot = ReadStores(frame);
                        committed = frame;
                        known.UnionWith(Names(frame.Transaction));
                    }
                    else if (frame.Kind != "R") throw new IOException("invalid_transition");
                    pending = null;
                }
                else throw new IOException("invalid_transition");
            }
            if (pending is not null) known.UnionWith(Names(pending.Transaction));
            if (files.Members().Any(name => !known.Contains(name))) throw new IOException("untracked_file");
            var stamp = witness.ReadVerified(files.Id);
            if (stamp.Generation != committed.Generation || stamp.Sequence != committed.Generation ||
                stamp.Transaction != committed.Transaction || stamp.Commitment != committed.Digest || stamp.Deployment != files.Id ||
                stamp.Link != (committed.Generation == 0 ? (committed with { Kind = "W" }).Hash : committed.WitnessLink))
                return Quarantine("WitnessDisagreement");
            var recovery = SyntheticStorage.Restore(new FixedGeneration(stamp.Generation), null, committed.Generation, snapshot, seen);
            var authorized = recovery.IssueCapability(request, context, protection);
            if (authorized != StorageDecision.Simulated || recovery.Begin(request, context, protection, default) != StorageDecision.Simulated)
                return new(StorageDecision.Unauthorized, 0, 0, "Denied");
            try
            {
                tail = log[^1];
                framesUsed = log.Count;
                if (pending is not null)
                {
                    if (failRollback || Names(pending.Transaction).Any(name => !files.Remove(name)) || witness.ReadVerified(files.Id) != stamp)
                        return Quarantine("RollbackFailed");
                    Append(pending with { Kind = "R", Previous = tail.Hash });
                }
                head = committed;
                witnessed = stamp;
                return new(StorageDecision.Simulated, committed.Generation, snapshot.Count, pending is null ? "Clean" : "Aborted");
            }
            finally { recovery.Interrupt(request, context); }
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { return Quarantine("InvalidEvidence"); }
    }

    internal SyntheticStorage Coordinator()
    {
        lock (gate)
        {
            if (issuedCoordinator || head is null || ReadGeneration() != head.Generation) throw new InvalidOperationException("recovery_required");
            issuedCoordinator = true;
            return SyntheticStorage.Restore(this, this, head.Generation, snapshot, seen);
        }
    }

    public long? ReadGeneration()
    {
        lock (gate)
        {
            try { return !lease.IsClosed && head is not null && files.Read("quarantine") is null && witness.ReadVerified(files.Id) == witnessed ? head.Generation : null; }
            catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { return null; }
        }
    }

    StorageDecision ISyntheticStateJournal.Record(StorageRequest request, long current, long next, IReadOnlyDictionary<ResourceKey, long> proposed,
        Func<StorageDecision> recheck, SyntheticStorage.Fault fault)
    {
        lock (gate) return RecordCore(request, current, next, proposed, recheck, fault);
    }

    StorageDecision RecordCore(StorageRequest request, long current, long next, IReadOnlyDictionary<ResourceKey, long> proposed,
        Func<StorageDecision> recheck, SyntheticStorage.Fault fault)
    {
        if (ReadGeneration() != current || tail is null || witnessed is null || next != current + 1 || next > 64 ||
            framesUsed > 94 || seen.Contains(request.RequestId) || proposed.Count > 64 ||
            proposed.Any(pair => !pair.Key.Valid || !Stores.Contains(pair.Key.Class) || pair.Value <= 0 || pair.Value > next))
            return StorageDecision.Stale;
        var bytes = Stores.Select(store => Serialize(proposed, store)).ToArray();
        var intent = new RecoveryFrame("I", next, request.RequestId, Digest(bytes), tail.Hash);
        milestone?.Invoke("before-intent");
        try
        {
            Append(intent);
            seen.Add(intent.Transaction);
            milestone?.Invoke("after-intent");
            var paths = Names(intent.Transaction);
            for (int i = 0; i < paths.Length; i++)
            {
                if (i == 0 && milestone is not null)
                {
                    // Model interruption mid-store using a persisted prefix, then append the remainder.
                    int middle = bytes[i].Length / 2;
                    files.Write(paths[i], bytes[i][..middle], create: true);
                    milestone("during-write");
                    files.Write(paths[i], bytes[i][middle..], create: false, append: true);
                }
                else files.Write(paths[i], bytes[i], create: true);
                if (i == 0 && fault is SyntheticStorage.Fault.Partial or SyntheticStorage.Fault.Rollback)
                {
                    if (fault == SyntheticStorage.Fault.Rollback || !files.Remove(paths[i]) || witness.ReadVerified(files.Id) != witnessed)
                        return Quarantine("RollbackFailed").Decision;
                    Append(intent with { Kind = "R", Previous = tail!.Hash });
                    return StorageDecision.TransactionFailed;
                }
            }
            _ = ReadStores(intent);
            milestone?.Invoke("after-data-flush");
            var fence = recheck();
            if (fence != StorageDecision.Simulated)
            {
                if (paths.Any(path => !files.Remove(path)) || witness.ReadVerified(files.Id) != witnessed)
                    return Quarantine("RollbackFailed").Decision;
                Append(intent with { Kind = "R", Previous = tail!.Hash });
                return fence;
            }
            if (!witness.TryAdvance(witnessed, intent.Transaction, intent.Digest)) return Quarantine("WitnessAdvanceFailed").Decision;
            milestone?.Invoke("before-commit");
            var stamp = witness.ReadVerified(files.Id);
            if (stamp.Deployment != files.Id || stamp.Generation != next || stamp.Sequence != next ||
                stamp.Transaction != intent.Transaction || stamp.Commitment != intent.Digest || !ExpectedIdentity.IsHash(stamp.Link) ||
                stamp.PreviousLink != witnessed.Link)
                return Quarantine("WitnessDisagreement").Decision;
            var commit = intent with { Kind = "C", Previous = tail!.Hash, WitnessLink = stamp.Link };
            Append(commit);
            milestone?.Invoke("after-commit");
            snapshot = ReadStores(commit);
            if (witness.ReadVerified(files.Id) != stamp)
                return Quarantine("WitnessDisagreement").Decision;
            head = commit;
            witnessed = stamp;
            return StorageDecision.Simulated;
        }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { return Quarantine("CommitFailed").Decision; }
    }

    Result Quarantine(string reason)
    {
        head = null;
        try { if (files.Read("quarantine") is null) files.Write("quarantine", "quarantined\n"u8.ToArray(), create: true); }
        catch (Exception ex) when (ex is IOException or UnauthorizedAccessException) { return new(StorageDecision.RollbackFailed, 0, 0, "QuarantineUnconfirmed"); }
        return new(StorageDecision.Quarantined, 0, 0, reason);
    }

    void Append(RecoveryFrame frame)
    {
        if (framesUsed >= 96) throw new IOException("journal_limit");
        files.Write("journal", frame.Encode(), create: false, append: true);
        tail = frame;
        framesUsed++;
    }

    static string[] Names(Guid transaction) => Enumerable.Range(0, Stores.Length).Select(i => $"{transaction:N}-{i}").ToArray();
    static string Digest(IEnumerable<byte[]> bytes) => RecoveryFrame.HashBytes(Encoding.ASCII.GetBytes(string.Concat(bytes.Select(RecoveryFrame.HashBytes))));

    static byte[] Serialize(IReadOnlyDictionary<ResourceKey, long> proposed, ResourceClass store) =>
        Encoding.ASCII.GetBytes(string.Concat(proposed.Where(p => p.Key.Class == store).OrderBy(p => p.Key.Id, StringComparer.Ordinal)
            .Select(p => FormattableString.Invariant($"{p.Key.Id}:{p.Value}\n"))));

    Dictionary<ResourceKey, long> ReadStores(RecoveryFrame frame)
    {
        var bytes = Names(frame.Transaction).Select(n => files.Read(n) ?? throw new IOException("store_missing")).ToArray();
        if (Digest(bytes) != frame.Digest) throw new IOException("store_disagreement");
        var output = new Dictionary<ResourceKey, long>();
        for (int i = 0; i < bytes.Length; i++)
        {
            foreach (var line in Encoding.ASCII.GetString(bytes[i]).Split('\n', StringSplitOptions.RemoveEmptyEntries))
            {
                var parts = line.Split(':');
                var key = new ResourceKey(Stores[i], parts[0]);
                if (parts.Length != 2 || !key.Valid || !long.TryParse(parts[1], out long revision) || revision <= 0 ||
                    revision > frame.Generation || output.Count >= 64 || !output.TryAdd(key, revision)) throw new IOException("store_format");
            }
            if (!Serialize(output, Stores[i]).AsSpan().SequenceEqual(bytes[i])) throw new IOException("store_format");
        }
        return output;
    }

    sealed class FixedGeneration(long generation) : IFreshnessWitness { public long? ReadGeneration() => generation; }
    public void Dispose() { lock (gate) lease.Dispose(); }
}
