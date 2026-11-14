using System.Security.Cryptography;

// Test-only validation logic; it is not wired into SCM or any native operation.
// Inputs are injected synthetic observations, not attestation or approval.
internal sealed class ProtectedProbeGate
{
    internal sealed record Approval(string Profile, Guid VmId, string Computer, string Snapshot,
        string Operation, string Target, string ManifestDigest, Guid Nonce, DateTimeOffset Expires);
    internal sealed record Context(Guid VmId, string Computer, string ManifestDigest, bool PackageVerified,
        bool IndependentApproval, bool SnapshotConfirmed);
    readonly HashSet<Guid> consumed = [];
    readonly object gate = new();
    internal bool Consume(Approval approval, Context context, DateTimeOffset now)
    {
        if (approval.Profile != "Protected" || approval.Operation != "inspect-synthetic-storage-rights" ||
            approval.Target != @"C:\SovereignVmVerification\synthetic-storage-k" ||
            approval.VmId == Guid.Empty || approval.VmId != context.VmId ||
            string.IsNullOrWhiteSpace(approval.Computer) || approval.Computer != context.Computer ||
            string.IsNullOrWhiteSpace(approval.Snapshot) || approval.Snapshot.Length > 80 ||
            approval.Snapshot.Any(c => !(char.IsAsciiLetterOrDigit(c) || c is '-' or '_' or '.')) ||
            !context.SnapshotConfirmed || !context.PackageVerified || !context.IndependentApproval ||
            approval.Nonce == Guid.Empty || approval.Expires <= now || approval.Expires > now.AddMinutes(1) ||
            !HashEqual(approval.ManifestDigest, context.ManifestDigest)) return false;
        lock (gate) return consumed.Add(approval.Nonce);
    }
    static bool HashEqual(string? a, string? b)
    {
        if (a is not { Length: 64 } || b is not { Length: 64 }) return false;
        try { return CryptographicOperations.FixedTimeEquals(Convert.FromHexString(a), Convert.FromHexString(b)); }
        catch (FormatException) { return false; }
    }
}
