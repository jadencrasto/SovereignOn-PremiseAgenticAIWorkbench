using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Text;
using Microsoft.Win32.SafeHandles;

// Constructed only by the offline integration tests. The caller cannot select a root,
// a file name, or any payload. No production service references this assembly.
[SupportedOSPlatform("windows")]
internal sealed class SyntheticStateJournal : ISyntheticStateJournal, IDisposable
{
    readonly List<(string Path, SafeFileHandle Handle)> ancestors = [];
    readonly List<(string Path, SafeFileHandle Handle)> entries = new(64);
    readonly string root;
    SafeFileHandle? owner;
    long generation;

    internal SyntheticStateJournal()
    {
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string temp = Path.TrimEndingDirectorySeparator(Path.GetTempPath());
        if (local.Length == 0 || !Path.IsPathFullyQualified(local) || Path.GetPathRoot(temp) is not { Length: 3 } drive ||
            !ExpectedIdentity.Same(temp, Path.Combine(local, "Temp")) || new DriveInfo(drive).DriveType != DriveType.Fixed)
            throw new IOException("fixture_root_unavailable");
        root = Path.Combine(temp, "sovereign-n-" + Guid.NewGuid().ToString("N"));
        try
        {
            var chain = new Stack<string>();
            for (DirectoryInfo? directory = new(temp); directory is not null; directory = directory.Parent)
                chain.Push(directory.FullName);
            foreach (var path in chain) ancestors.Add((path, OpenDirectory(path, false)));
            if (!CreateDirectory(root, IntPtr.Zero)) throw new IOException("fixture_create");
            owner = OpenDirectory(root, true);
        }
        catch
        {
            // A directory that could not be pinned is never reopened for deletion.
            ReleaseAncestors();
            throw;
        }
    }

    public StorageDecision Record(StorageRequest request, long current, long next, IReadOnlyDictionary<ResourceKey, long> snapshot,
        Func<StorageDecision> recheck, SyntheticStorage.Fault fault)
    {
        if (owner is null || owner.IsClosed || current != generation || next != generation + 1 || next > 64 ||
            snapshot.Count > 64 || snapshot.Any(item => !item.Key.Valid || item.Value is <= 0 || item.Value > next))
            return StorageDecision.TransactionFailed;
        if (!ancestors.All(item => Pinned(item.Handle, item.Path, true)) || !Pinned(owner, root, true))
            return StorageDecision.UnsafeResource;
        var bytes = Encoding.ASCII.GetBytes(string.Join('\n', snapshot.OrderBy(item => item.Key.Class)
            .ThenBy(item => item.Key.Id, StringComparer.Ordinal)
            .Select(item => FormattableString.Invariant($"{(int)item.Key.Class} {item.Key.Id} {item.Value}"))));
        string path = Path.Combine(root, FormattableString.Invariant($"generation-{next:D2}.fixture"));
        var candidate = CreateFile(path, 0xC0010000, 0, IntPtr.Zero, 1, 0x00200000, IntPtr.Zero);
        bool retained = false;
        var result = StorageDecision.TransactionFailed;
        try
        {
            if (candidate.IsInvalid) return StorageDecision.TransactionFailed;
            if (!Pinned(candidate, path, directory: false)) return StorageDecision.RollbackFailed;
            try
            {
                int length = fault is SyntheticStorage.Fault.Partial or SyntheticStorage.Fault.Rollback ? bytes.Length / 2 : bytes.Length;
                RandomAccess.Write(candidate, bytes.AsSpan(0, length), 0);
                RandomAccess.FlushToDisk(candidate);
                if (fault == SyntheticStorage.Fault.Rollback)
                {
                    // Leave the failed transaction quarantined. The test owner can still
                    // remove its private file by handle at fixture teardown.
                    entries.Add((path, candidate));
                    retained = true;
                    return StorageDecision.RollbackFailed;
                }
                if (fault != SyntheticStorage.Fault.Partial && ReadBack(candidate, bytes))
                {
                    result = recheck();
                    if (result == StorageDecision.Simulated)
                    {
                        entries.Add((path, candidate));
                        retained = true;
                        generation = next;
                        return result;
                    }
                }
            }
            catch { result = StorageDecision.TransactionFailed; }
            return DeleteAndConfirm(path, candidate) ? result : StorageDecision.RollbackFailed;
        }
        finally { if (!retained) candidate.Dispose(); }
    }

    internal int Entries => entries.Count;

    internal static bool ReadBack(SafeFileHandle file, byte[] expected)
    {
        if (RandomAccess.GetLength(file) != expected.Length) return false;
        var actual = new byte[expected.Length];
        for (int offset = 0; offset < actual.Length;)
        {
            int read = RandomAccess.Read(file, actual.AsSpan(offset), offset);
            if (read == 0) return false;
            offset += read;
        }
        return actual.AsSpan().SequenceEqual(expected);
    }

    internal static bool Pinned(SafeFileHandle handle, string path, bool directory)
    {
        var buffer = new StringBuilder(1024);
        uint count = GetFinalPathNameByHandle(handle, buffer, (uint)buffer.Capacity, 0);
        return count > 0 && count < buffer.Capacity && ExpectedIdentity.Same(buffer.ToString(), @"\\?\" + path) &&
            GetFileInformationByHandle(handle, out var info) && (info.Attributes & 0x400) == 0 &&
            ((info.Attributes & 0x10) != 0) == directory && (directory || info.Links == 1);
    }

    internal static SafeFileHandle OpenDirectory(string path, bool deletable)
    {
        var handle = CreateFile(path, deletable ? 0x10080U : 0x80U, 3, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
        if (!handle.IsInvalid && Pinned(handle, path, directory: true)) return handle;
        handle.Dispose();
        throw new IOException("fixture_ancestor_unavailable");
    }

    internal static bool DeleteAndConfirm(string path, SafeFileHandle handle)
    {
        byte delete = 1;
        bool accepted = SetFileInformationByHandle(handle, 4, ref delete, 1);
        handle.Dispose();
        return accepted && GetFileAttributes(path) == uint.MaxValue && Marshal.GetLastWin32Error() == 2;
    }

    void ReleaseAncestors()
    {
        for (int i = ancestors.Count - 1; i >= 0; i--) ancestors[i].Handle.Dispose();
        ancestors.Clear();
    }

    public void Dispose()
    {
        if (owner is not { } directory) return;
        owner = null;
        bool clean = true;
        try
        {
            foreach (var (path, handle) in entries) clean &= DeleteAndConfirm(path, handle);
            clean &= DeleteAndConfirm(root, directory);
        }
        finally
        {
            foreach (var (_, handle) in entries) handle.Dispose();
            entries.Clear();
            directory.Dispose();
            ReleaseAncestors();
        }
        if (!clean) throw new IOException("fixture_cleanup_incomplete");
    }

    [StructLayout(LayoutKind.Sequential)] struct FileInformation
    {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME Created, Accessed, Written;
        public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }

    [DllImport("kernel32.dll", EntryPoint = "CreateDirectoryW", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] internal static extern bool CreateDirectory(string path, IntPtr security);
    [DllImport("kernel32.dll", EntryPoint = "CreateFileW", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    internal static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    static extern uint GetFinalPathNameByHandle(SafeFileHandle handle, StringBuilder path, uint length, uint flags);
    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] static extern bool GetFileInformationByHandle(SafeFileHandle handle, out FileInformation info);
    [DllImport("kernel32.dll", ExactSpelling = true, SetLastError = true)]
    [return: MarshalAs(UnmanagedType.Bool)] static extern bool SetFileInformationByHandle(SafeFileHandle handle, int kind, ref byte value, uint size);
    [DllImport("kernel32.dll", EntryPoint = "GetFileAttributesW", CharSet = CharSet.Unicode, ExactSpelling = true, SetLastError = true)]
    static extern uint GetFileAttributes(string path);
}
