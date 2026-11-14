using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

// By default this probes access rights without mutation. A separately selected
// protected-VM exercise writes only a new generated sidecar and removes it by handle.
[SupportedOSPlatform("windows")]
internal static class StorageAccessProbe
{
    const uint Write = 2, Append = 4, DeleteChild = 0x40, Delete = 0x10000, WriteDac = 0x40000, WriteOwner = 0x80000;
    static readonly string[] Fixtures = ["document.fixture", "sqlite.fixture", "vector.fixture", "artifact.fixture",
        "audit.fixture", "recovery.fixture", "witness.fixture"];

    internal static bool ExpectedGrant(string fixture, uint right, bool broker) => broker &&
        (fixture is "audit.fixture" ? right == Append :
            fixture is not "witness.fixture" && right is Write or Append or Delete);

    internal static bool MatchesRole(TokenEvidence actual, string expectedSid, string serviceSid, string role) =>
        role is "broker" or "application" or "independent" && actual.User == expectedSid && !actual.UnsafePrivileges &&
        !actual.EnabledGroups.Contains("S-1-5-32-544") &&
        actual.EnabledGroups.Contains(serviceSid) == (role == "broker") &&
        actual.RestrictedGroups.Contains(serviceSid) == (role == "broker");

    internal static bool RoleMatchesManifest(ExpectedIdentity expected, string role, string sid) =>
        expected.Fixture is { } fixture && ExpectedIdentity.ValidSid(sid) && role switch
        {
            "broker" => sid == fixture.BrokerSid,
            "application" => sid == fixture.ObserverSid,
            "independent" => sid != fixture.BrokerSid && sid != fixture.ObserverSid &&
                sid != expected.Manifest.ServiceSid && !expected.Manifest.InstallAdministrators.Contains(sid),
            _ => false
        };

    internal static int Run(string root, string role, string expectedSid, string? serviceSid = null, bool exercise = false)
    {
        var pins = new List<SafeFileHandle>();
        try
        {
            bool protectedFixture = serviceSid is not null;
            if (!(role is "application" or "broker" || protectedFixture && role == "independent") ||
                !ExpectedIdentity.ValidSid(expectedSid) || (exercise && (!protectedFixture || role != "broker"))) return 2;
            using var identity = WindowsIdentity.GetCurrent();
            if (identity.User?.Value != expectedSid ||
                new WindowsPrincipal(identity).IsInRole(WindowsBuiltInRole.Administrator)) return 2;
            if (protectedFixture)
            {
                if (!ExpectedIdentity.ValidSid(serviceSid!) || !serviceSid!.StartsWith("S-1-5-80-", StringComparison.Ordinal)) return 2;
                using var process = new SafeProcessHandle(GetCurrentProcess(), ownsHandle: false);
                var actual = WindowsInspector.InspectToken(process);
                if (!MatchesRole(actual, expectedSid, serviceSid, role)) return 2;
            }
            if (!Path.IsPathFullyQualified(root)) return 2;
            root = Path.GetFullPath(root);
            if (Path.GetFileName(root) != (protectedFixture ? "synthetic-storage-k" : "synthetic-storage-j4") ||
                !root.StartsWith(Path.GetPathRoot(root)!, StringComparison.Ordinal) ||
                Path.GetPathRoot(root) is not { Length: 3 } drive ||
                new DriveInfo(drive).DriveType != DriveType.Fixed) return 2;
            var ancestors = new Stack<string>();
            for (var item = new DirectoryInfo(root); item is not null; item = item.Parent)
            {
                if (!item.Exists || item.Attributes.HasFlag(FileAttributes.ReparsePoint)) return 2;
                ancestors.Push(item.FullName);
            }
            if (protectedFixture)
                foreach (var path in ancestors) pins.Add(SyntheticStateJournal.OpenDirectory(path, false));
            var marker = new FileInfo(Path.Combine(root, ".synthetic-only"));
            if (!marker.Exists || marker.Attributes.HasFlag(FileAttributes.ReparsePoint) || marker.Length != 0) return 2;
            // The flat fixture has an administrator-owned parent. Neither test
            // identity may bypass file ACLs through directory delete-child.
            foreach (string directory in new[] { root, Path.GetDirectoryName(root)! })
                foreach (uint right in new[] { Write, Append, DeleteChild, Delete, WriteDac, WriteOwner })
                {
                    using var handle = OpenExisting(directory, right, 7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
                    if (!handle.IsInvalid || Marshal.GetLastWin32Error() != 5) return 1;
                }
            string[] fixtures = protectedFixture ? [.. Fixtures, @"sqlite-sidecars.fixture\sqlite-wal.fixture", @"sqlite-sidecars.fixture\sqlite-shm.fixture"] : Fixtures;
            if (protectedFixture)
            {
                string sidecars = Path.Combine(root, "sqlite-sidecars.fixture");
                pins.Add(SyntheticStateJournal.OpenDirectory(sidecars, false));
                foreach (uint right in new[] { Write, Append, DeleteChild, WriteDac, WriteOwner })
                {
                    using var handle = OpenExisting(sidecars, right, 7, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
                    int error = handle.IsInvalid ? Marshal.GetLastWin32Error() : 0;
                    bool grant = role == "broker" && right == Write; // FILE_ADD_FILE on a directory.
                    if (error != (grant ? 0 : 5)) return 1;
                }
            }
            foreach (var fixture in fixtures)
            {
                // Fixed paths under an operator-staged root with no concurrent administrator.
                // OPEN_REPARSE_POINT refuses to follow a substituted final component.
                string path = Path.Combine(root, fixture);
                var item = new FileInfo(path);
                if (!item.Exists || item.Attributes.HasFlag(FileAttributes.ReparsePoint) || item.Length > 4096) return 2;
                if (protectedFixture)
                {
                    // A read-only sharing trial detects incompatible pre-existing
                    // data-write/delete handles at this point. Release it before
                    // probing each right so the test never manufactures a denial.
                    using var metadata = OpenExisting(path, 0x81, 1, IntPtr.Zero, 3, 0x00200000, IntPtr.Zero);
                    if (metadata.IsInvalid || !SyntheticStateJournal.Pinned(metadata, path, false)) return 2;
                }
                foreach (uint right in new[] { Write, Append, Delete, WriteDac, WriteOwner })
                {
                    using var handle = OpenExisting(path, right, 7, IntPtr.Zero, 3, 0x00200000, IntPtr.Zero);
                    int error = handle.IsInvalid ? Marshal.GetLastWin32Error() : 0;
                    if (error != 0 && error != 5) return 1; // Access denied only, not sharing or path failure.
                    if ((error == 0) != ExpectedGrant(fixture, right, role == "broker")) return 1;
                }
            }
            return !exercise || ExerciseSidecar(Path.Combine(root, "sqlite-sidecars.fixture"), pins[^1]) ? 0 : 1;
        }
        catch { return 1; }
        finally { for (int i = pins.Count - 1; i >= 0; i--) pins[i].Dispose(); }
    }

    static bool ExerciseSidecar(string directory, SafeFileHandle pinnedDirectory)
    {
        if (!SyntheticStateJournal.Pinned(pinnedDirectory, directory, true)) return false;
        string path = Path.Combine(directory, $"sqlite-{Guid.NewGuid():N}-wal.fixture");
        using var file = OpenExisting(path, 0xC0010000, 0, IntPtr.Zero, 1, 0x00200000, IntPtr.Zero);
        if (file.IsInvalid || !SyntheticStateJournal.Pinned(file, path, false)) return false;
        bool verified = false, removed = false;
        try
        {
            byte[] bytes = "sovereign-disposable-sidecar"u8.ToArray();
            RandomAccess.Write(file, bytes, 0);
            RandomAccess.FlushToDisk(file);
            verified = SyntheticStateJournal.ReadBack(file, bytes);
        }
        finally { removed = SyntheticStateJournal.DeleteAndConfirm(path, file); }
        return verified && removed;
    }

    [DllImport("kernel32.dll", EntryPoint="CreateFileW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern SafeFileHandle OpenExisting(string path, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", ExactSpelling=true)] static extern IntPtr GetCurrentProcess();
}
