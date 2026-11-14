using System.Text.RegularExpressions;

internal enum ResourceClass { Document, SqliteState, VectorIndex, Artifact, Audit, Recovery, FreshnessWitness }
internal enum StorageOperation { Read, Create, Modify, Delete, Append, Recover }
internal enum StorageDecision
{
    Disabled, Simulated, InvalidRequest, IdentityUnavailable, UnsafeResource, Unauthorized,
    Busy, Revoked, Stale, WitnessUnavailable, Quarantined, Interrupted, TransactionFailed, RollbackFailed
}

internal sealed record ResourceKey(ResourceClass Class, string Id)
{
    internal bool Valid => Enum.IsDefined(Class) && Id is not null &&
        Regex.IsMatch(Id, @"\A[a-f0-9]{32}\z") && Id.Any(c => c != '0');
}
internal sealed record StorageRequest(Guid RequestId, ResourceKey Resource, StorageOperation Operation, long Generation);
internal sealed record RequestContext(Guid SessionId, bool CallerVerified, BindingResult Broker, bool RecoveryVerified);
internal sealed record ResourceProtection(bool Pinned, bool Reparse, bool InheritedWriter);
internal interface IFreshnessWitness { long? ReadGeneration(); }
internal interface ISyntheticStateJournal
{
    StorageDecision Record(StorageRequest request, long current, long next, IReadOnlyDictionary<ResourceKey, long> snapshot,
        Func<StorageDecision> recheck, SyntheticStorage.Fault fault);
}

internal static class StoragePolicy
{
    // Dedicated classes expose only typed operations and opaque IDs. No path,
    // query, free-form command or storage driver enters this decision path.
    internal static bool Supported(ResourceClass resource, StorageOperation operation) => resource switch
    {
        ResourceClass.Document or ResourceClass.Artifact => operation is StorageOperation.Read or StorageOperation.Create or StorageOperation.Delete,
        ResourceClass.SqliteState or ResourceClass.VectorIndex => operation is StorageOperation.Read or StorageOperation.Create or StorageOperation.Modify or StorageOperation.Delete,
        ResourceClass.Audit => operation is StorageOperation.Append,
        ResourceClass.Recovery => operation is StorageOperation.Read or StorageOperation.Recover,
        ResourceClass.FreshnessWitness => operation is StorageOperation.Read,
        _ => false
    };

    internal static StorageDecision Evaluate(StorageRequest request, RequestContext context,
        ResourceProtection protection, bool trustedGrant, long generation, long? witness)
    {
        if (request.RequestId == Guid.Empty || request.Resource is not { Valid: true } || request.Generation < 0 ||
            !Supported(request.Resource.Class, request.Operation)) return StorageDecision.InvalidRequest;
        if (context.SessionId == Guid.Empty || !context.CallerVerified || !context.RecoveryVerified ||
            context.Broker.Finding != Finding.VerifiedEvidence || !context.Broker.IsCurrent)
            return StorageDecision.IdentityUnavailable;
        if (!protection.Pinned || protection.Reparse || protection.InheritedWriter) return StorageDecision.UnsafeResource;
        if (!trustedGrant) return StorageDecision.Unauthorized;
        if (witness is null) return StorageDecision.WitnessUnavailable;
        return request.Generation == generation && witness.Value == generation
            ? StorageDecision.Simulated : StorageDecision.Stale;
    }

    internal static StorageDecision Production(StorageRequest request) => StorageDecision.Disabled;
}

// Synthetic only. The lock excludes concurrent writers in this object, and
// neither the state nor the witness models OS ACLs or durable atomicity.
internal sealed class SyntheticStorage(IFreshnessWitness witness, ISyntheticStateJournal? journal = null)
{
    readonly object gate = new();
    readonly Dictionary<Guid, (RequestContext Context, StorageRequest Request, bool CompoundIndex)> grants = new();
    readonly HashSet<Guid> issued = new();
    readonly Dictionary<Guid, StorageRequest> reservations = new();
    readonly Dictionary<ResourceKey, long> revisions = new();
    long generation;
    bool revoked, quarantined;

    internal static SyntheticStorage Restore(IFreshnessWitness witness, ISyntheticStateJournal? journal,
        long generation, Dictionary<ResourceKey, long> revisions, IEnumerable<Guid> consumed)
    {
        var storage = new SyntheticStorage(witness, journal) { generation = generation };
        storage.ApplyCommitted(revisions);
        storage.issued.UnionWith(consumed);
        return storage;
    }

    internal void SetTestGrant(RequestContext context, StorageRequest request, bool compoundIndex = false)
    {
        if (compoundIndex && (request.Resource.Class != ResourceClass.Document ||
            request.Operation is not (StorageOperation.Create or StorageOperation.Delete)))
            throw new ArgumentException("Compound grants apply only to document creation or deletion.");
        lock (gate)
        {
            if (revoked || quarantined || !issued.Add(request.RequestId)) throw new InvalidOperationException("grant_unavailable");
            grants.Add(request.RequestId, (context, request, compoundIndex));
        }
    }

    internal StorageDecision IssueCapability(StorageRequest request, RequestContext context, ResourceProtection protection)
    {
        lock (gate)
        {
            if (quarantined) return StorageDecision.Quarantined;
            if (revoked) return StorageDecision.Revoked;
            var result = StoragePolicy.Evaluate(request, context, protection, true, generation, ReadWitness());
            if (result != StorageDecision.Simulated) return result;
            if (!issued.Add(request.RequestId)) return StorageDecision.Unauthorized;
            grants.Add(request.RequestId, (context, request, false));
            return result;
        }
    }

    internal StorageDecision Begin(StorageRequest request, RequestContext context, ResourceProtection protection,
        CancellationToken cancellation)
    {
        lock (gate)
        {
            if (cancellation.IsCancellationRequested) return StorageDecision.Interrupted;
            var result = Decide(request, context, protection);
            if (result != StorageDecision.Simulated) return result;
            if (reservations.Count != 0) return StorageDecision.Busy;
            reservations.Add(request.RequestId, request);
            return StorageDecision.Simulated;
        }
    }

    StorageDecision Decide(StorageRequest request, RequestContext context, ResourceProtection protection)
    {
        if (quarantined) return StorageDecision.Quarantined;
        if (revoked) return StorageDecision.Revoked;
        bool trustedGrant = grants.TryGetValue(request.RequestId, out var grant) &&
            ReferenceEquals(grant.Context, context) && grant.Request == request;
        return StoragePolicy.Evaluate(request, context, protection, trustedGrant, generation, ReadWitness());
    }

    long? ReadWitness()
    {
        try { return witness.ReadGeneration(); }
        catch { return null; }
    }

    internal StorageDecision Interrupt(StorageRequest request, RequestContext context)
    {
        lock (gate)
        {
            if (!grants.TryGetValue(request.RequestId, out var grant) || grant.Request != request ||
                !ReferenceEquals(grant.Context, context)) return StorageDecision.Unauthorized;
            reservations.Remove(request.RequestId);
            grants.Remove(request.RequestId);
            return StorageDecision.Interrupted;
        }
    }

    // Failure fixtures only. Compound writes affect the synthetic document and
    // vector generations. A failed rollback quarantines the entire instance.
    internal enum Fault { None, Transaction, Partial, Rollback }

    internal StorageDecision Finish(StorageRequest request, RequestContext context, ResourceProtection protection,
        CancellationToken cancellation, Fault fault = Fault.None, ResourceProtection? companionProtection = null)
    {
        lock (gate)
        {
            if (!reservations.TryGetValue(request.RequestId, out var reserved) || reserved != request ||
                !grants.TryGetValue(request.RequestId, out var grant) || !ReferenceEquals(context, grant.Context))
                return StorageDecision.Unauthorized;
            try
            {
                if (cancellation.IsCancellationRequested) return StorageDecision.Interrupted;
                var result = Decide(request, context, protection);
                if (result != StorageDecision.Simulated) return result;
                if (grant.CompoundIndex && companionProtection is not { Pinned: true, Reparse: false, InheritedWriter: false })
                    return StorageDecision.UnsafeResource;
                if (fault == Fault.Transaction || !Enum.IsDefined(fault)) return StorageDecision.TransactionFailed;
                bool exists = revisions.ContainsKey(request.Resource);
                if (request.Operation == StorageOperation.Read)
                    return exists ? StorageDecision.Simulated : StorageDecision.TransactionFailed;
                if ((request.Operation == StorageOperation.Create && exists) ||
                    (request.Operation is StorageOperation.Modify or StorageOperation.Delete && !exists))
                    return StorageDecision.TransactionFailed;
                var snapshot = new Dictionary<ResourceKey, long>(revisions);
                long next = checked(generation + 1);
                void Apply(ResourceKey key)
                {
                    if (request.Operation == StorageOperation.Delete) snapshot.Remove(key);
                    else snapshot[key] = next;
                }
                Apply(request.Resource);
                if (journal is not null)
                {
                    if (grant.CompoundIndex) Apply(request.Resource with { Class = ResourceClass.VectorIndex });
                    try
                    {
                        result = journal.Record(request, generation, next, snapshot, () => cancellation.IsCancellationRequested
                            ? StorageDecision.Interrupted : Decide(request, context, protection), fault);
                    }
                    catch { result = StorageDecision.RollbackFailed; }
                    if (result != StorageDecision.Simulated)
                    {
                        if (result is StorageDecision.RollbackFailed or StorageDecision.Quarantined) quarantined = true;
                        return result;
                    }
                }
                else
                {
                    if (fault == Fault.Partial) return StorageDecision.TransactionFailed;
                    if (fault == Fault.Rollback)
                    {
                        ApplyCommitted(snapshot);
                        quarantined = true;
                        return StorageDecision.RollbackFailed;
                    }
                    if (grant.CompoundIndex) Apply(request.Resource with { Class = ResourceClass.VectorIndex });
                }
                ApplyCommitted(snapshot);
                generation = next;
                return StorageDecision.Simulated;
            }
            finally
            {
                reservations.Remove(request.RequestId);
                grants.Remove(request.RequestId);
            }
        }
    }

    void ApplyCommitted(Dictionary<ResourceKey, long> snapshot)
    {
        revisions.Clear();
        foreach (var item in snapshot) revisions.Add(item.Key, item.Value);
    }

    internal void Revoke() { lock (gate) revoked = true; }
    internal (long Generation, int Revisions, int Writers, bool Quarantined) Snapshot()
    {
        lock (gate) return (generation, revisions.Count, reservations.Count, quarantined);
    }
}
