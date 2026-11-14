using System.Collections.Immutable;
using System.Security.Cryptography;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;

internal enum Finding
{
    VerifiedEvidence, MissingEvidence, MismatchedIdentity, InvalidConfiguration, UntrustedInstallation,
    UnexpectedProcess, InspectionFailure, NativeUnavailable, EndpointUnavailable, UnexpectedEndpoint,
    UnsafeEndpoint, ChangedBinding, Cancelled
}

internal enum EvidenceScope { NativeObservation, Synthetic }
internal sealed record ObjectPin(string Path, string FileId, string? Sha256, string DescriptorSha256);
internal sealed record TrustManifest(string ServiceName, string BinaryCommand, string Account, string UserSid,
    string ServiceSid, string PipeName, int IntegrityRid, ImmutableArray<string> InstallAdministrators,
    ImmutableArray<ObjectPin> Installation, string ServiceDescriptorSha256, string EndpointDescriptorSha256,
    string? ObserverSid = null);
internal sealed record ServiceConfiguration(string BinaryCommand, string Account, uint Type, uint Start, uint SidType, uint Pid,
    string DescriptorSha256, bool ConfigurationProtected);
internal sealed record TokenEvidence(string User, string[] EnabledGroups, string[] RestrictedGroups, int IntegrityRid,
    bool UnsafePrivileges, long TokenId = 0, long Revision = 0);
internal sealed record AccessRule(string Sid, uint Mask, bool Inherited);
internal sealed record AccessEvidence(string Owner, bool Complete, string DescriptorSha256, AccessRule[] Allows);
internal sealed record ObjectEvidence(string Path, string FileId, string? Sha256, bool Reparse, bool Directory,
    bool FinalPathMatches, uint Links, AccessEvidence Access);
internal sealed record ServiceEvidence(ServiceConfiguration Configuration, TokenEvidence Token, string ImagePath,
    uint ProcessPid, bool Alive, ObjectEvidence[] Installation);

// A reviewed digest is supplied independently. A digest embedded in the manifest is not a trust anchor.
internal sealed class ExpectedIdentity
{
    internal const string ServiceName = "SovereignDevBrokerFoundation";
    internal TrustManifest Manifest { get; }
    internal string Executable => Manifest.Installation[^4].Path;
    internal ProtectedFixtureConfig? Fixture => Manifest.ObserverSid is { } observer ?
        ProtectedFixtureConfig.Parse([ProtectedFixtureConfig.Mode, ProtectedFixtureConfig.Approval,
            Manifest.PipeName, Manifest.UserSid, observer]) : null;
    ExpectedIdentity(TrustManifest manifest) => Manifest = manifest;

    internal static ExpectedIdentity? Read(ReadOnlySpan<byte> json, string reviewedSha256)
    {
        try
        {
            if (json.Length is 0 or > 65536 || !IsHash(reviewedSha256) ||
                !CryptographicOperations.FixedTimeEquals(SHA256.HashData(json), Convert.FromHexString(reviewedSha256))) return null;
            using var doc = JsonDocument.Parse(json.ToArray());
            if (!UniqueKeys(doc.RootElement)) return null;
            var m = doc.Deserialize<TrustManifest>(new JsonSerializerOptions {
                UnmappedMemberHandling = JsonUnmappedMemberHandling.Disallow,
                RespectRequiredConstructorParameters = true
            });
            return m is not null && Valid(m) ? new(m) : null;
        }
        catch (Exception ex) when (ex is JsonException or ArgumentException or NullReferenceException) { return null; }
    }

    static bool UniqueKeys(JsonElement item) => item.ValueKind switch
    {
        JsonValueKind.Object => item.EnumerateObject().Select(p => p.Name).Distinct(StringComparer.Ordinal).Count()
            == item.EnumerateObject().Count() && item.EnumerateObject().All(p => UniqueKeys(p.Value)),
        JsonValueKind.Array => item.EnumerateArray().All(UniqueKeys),
        _ => true
    };

    static bool Valid(TrustManifest m)
    {
        if (m.ServiceName != ServiceName || !Regex.IsMatch(m.PipeName, @"\ASovereignAI-identity-dev-[a-f0-9]{32}\z") ||
            !ValidSid(m.UserSid) || !Regex.IsMatch(m.ServiceSid, @"\AS-1-5-80-(?:[0-9]+-){4}[0-9]+\z") ||
            !ValidSid(m.ServiceSid) || !Regex.IsMatch(m.UserSid, @"\AS-1-5-21-(?:[0-9]+-){3}[0-9]+\z") ||
            string.IsNullOrWhiteSpace(m.Account) || m.IntegrityRid is < 0x1000 or > 0x3000 ||
            m.InstallAdministrators.IsDefaultOrEmpty || m.InstallAdministrators.Length > 8 || !m.InstallAdministrators.All(ValidSid) ||
            m.InstallAdministrators.Any(s => s == m.UserSid || s == m.ServiceSid || s is "S-1-1-0" or "S-1-5-11" or "S-1-5-32-545") ||
            m.Installation.IsDefaultOrEmpty || m.Installation.Length is < 5 or > 32 ||
            !IsHash(m.ServiceDescriptorSha256) || !IsHash(m.EndpointDescriptorSha256)) return false;
        var objects = m.Installation;
        int firstFile = objects.Length - 4;
        for (int i = 0; i < objects.Length; i++)
        {
            var p = objects[i];
            if (!AbsoluteLocalPath(p.Path) || !Regex.IsMatch(p.FileId, @"\A[0-9A-F]{16}:[0-9a-f]{32}\z") ||
                !IsHash(p.DescriptorSha256) || (i < firstFile ? p.Sha256 is not null : !IsHash(p.Sha256))) return false;
            if (i == 0 && p.Path.Length != 3) return false;
            if (i > 0)
            {
                string parent = p.Path[..p.Path.LastIndexOf('\\')];
                if (parent.Length == 2) parent += "\\";
                if (!Same(parent, objects[Math.Min(i - 1, firstFile - 1)].Path)) return false;
            }
        }
        string[] required = ["ServiceFoundation.exe", "ServiceFoundation.dll", "ServiceFoundation.runtimeconfig.json", "ServiceFoundation.deps.json"];
        for (int i = 0; i < required.Length; i++)
            if (!Same(objects[firstFile + i].Path.Split('\\')[^1], required[i])) return false;
        if (m.ObserverSid is not { } observer) return m.BinaryCommand == $"\"{objects[firstFile].Path}\" service";
        var fixture = ProtectedFixtureConfig.Parse([ProtectedFixtureConfig.Mode, ProtectedFixtureConfig.Approval,
            m.PipeName, m.UserSid, observer]);
        return fixture is not null && !m.InstallAdministrators.Contains(observer, StringComparer.Ordinal) &&
            m.BinaryCommand == fixture.Command(objects[firstFile].Path);
    }

    internal static bool Same(string? a, string? b) => string.Equals(a, b, StringComparison.OrdinalIgnoreCase);
    internal static bool IsHash(string? value) => value is not null && Regex.IsMatch(value, @"\A[0-9a-fA-F]{64}\z");
    internal static bool ValidSid(string value) => Regex.IsMatch(value, @"\AS-1-[0-9]+(?:-[0-9]+){1,15}\z")
        && value.Split('-').Skip(2).All(v => uint.TryParse(v, out uint n) && n.ToString(System.Globalization.CultureInfo.InvariantCulture) == v);
    static bool AbsoluteLocalPath(string path)
    {
        if (!Regex.IsMatch(path, @"\A[A-Z]:\\") || path.Length > 240) return false;
        return path.Length == 3 || path[3..].Split('\\').All(part =>
            Regex.IsMatch(part, @"\A[A-Za-z0-9_][A-Za-z0-9_. -]*\z") && part[^1] is not ('.' or ' ') &&
            !Regex.IsMatch(part.Split('.')[0], @"\A(CON|PRN|AUX|NUL|COM[0-9]|LPT[0-9])\z", RegexOptions.IgnoreCase));
    }
}

internal static class IdentityPolicy
{
    // Includes delete, WRITE_DAC, WRITE_OWNER, generic write/all, and file/dir mutation bits.
    const uint Mutation = 0x500D0156;
    internal static bool Protected(AccessEvidence access, ImmutableArray<string> administrators, string descriptorHash) =>
        access.Complete && administrators.Contains(access.Owner, StringComparer.Ordinal) &&
        ExpectedIdentity.Same(access.DescriptorSha256, descriptorHash) &&
        access.Allows.All(rule => (rule.Mask & Mutation) == 0 || administrators.Contains(rule.Sid, StringComparer.Ordinal));

    internal static Finding Configuration(ExpectedIdentity expected, ServiceConfiguration config)
    {
        var m = expected.Manifest;
        return config.Type == 0x10 && config.Start == 3 && config.SidType == 3 &&
            config.BinaryCommand == m.BinaryCommand && ExpectedIdentity.Same(config.Account, m.Account) &&
            config.ConfigurationProtected && ExpectedIdentity.Same(config.DescriptorSha256, m.ServiceDescriptorSha256)
            ? Finding.VerifiedEvidence : Finding.InvalidConfiguration;
    }

    internal static Finding Verify(ExpectedIdentity? expected, ServiceEvidence? observation)
    {
        if (expected is null || observation is null) return Finding.MissingEvidence;
        var m = expected.Manifest;
        var config = Configuration(expected, observation.Configuration);
        if (config != Finding.VerifiedEvidence) return config;
        if (!observation.Alive || observation.ProcessPid == 0 || observation.ProcessPid != observation.Configuration.Pid)
            return Finding.UnexpectedProcess;
        if (observation.Token.User != m.UserSid || observation.Token.IntegrityRid != m.IntegrityRid || observation.Token.UnsafePrivileges ||
            !observation.Token.EnabledGroups.Contains(m.ServiceSid, StringComparer.Ordinal) ||
            !observation.Token.RestrictedGroups.Contains(m.ServiceSid, StringComparer.Ordinal) ||
            observation.Token.EnabledGroups.Any(s => s == "S-1-5-32-544" || m.InstallAdministrators.Contains(s, StringComparer.Ordinal)))
            return Finding.MismatchedIdentity;
        if (!ExpectedIdentity.Same(observation.ImagePath, expected.Executable) ||
            observation.Installation.Length != m.Installation.Length) return Finding.UntrustedInstallation;
        for (int i = 0; i < m.Installation.Length; i++)
        {
            var pin = m.Installation[i];
            var item = observation.Installation[i];
            if (!ExpectedIdentity.Same(item.Path, pin.Path) || !item.FinalPathMatches || item.Reparse ||
                item.Directory != (pin.Sha256 is null) || (!item.Directory && item.Links != 1) ||
                item.FileId != pin.FileId || !ExpectedIdentity.Same(item.Sha256, pin.Sha256) ||
                !Protected(item.Access, m.InstallAdministrators, pin.DescriptorSha256))
                return Finding.UntrustedInstallation;
        }
        return Finding.VerifiedEvidence;
    }
}
