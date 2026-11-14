using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using Microsoft.Win32.SafeHandles;

[SupportedOSPlatform("windows")]
internal sealed class DurableFixture : IDisposable
{
    readonly List<SafeFileHandle> directories = [];
    readonly string root;
    internal Guid Id { get; }
    const int Limit = 65536;

    internal DurableFixture(Guid id, bool create = false)
    {
        Id = id;
        string local = Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData);
        string temp = Path.TrimEndingDirectorySeparator(Path.GetTempPath());
        if (id == Guid.Empty || local.Length == 0 || !ExpectedIdentity.Same(temp, Path.Combine(local, "Temp")) ||
            Path.GetPathRoot(temp) is not { Length: 3 } drive || new DriveInfo(drive).DriveType != DriveType.Fixed)
            throw new IOException("invalid_fixture_root");
        root = Path.Combine(temp, $"sovereign-p-{id:N}");
        try
        {
            var chain = new Stack<string>();
            for (DirectoryInfo? p = new(temp); p is not null; p = p.Parent) chain.Push(p.FullName);
            foreach (var p in chain) directories.Add(SyntheticStateJournal.OpenDirectory(p, false));
            if (create && !SyntheticStateJournal.CreateDirectory(root, IntPtr.Zero)) throw new IOException("fixture_exists");
            directories.Add(SyntheticStateJournal.OpenDirectory(root, false));
            var witness = Path.Combine(root, "witness");
            if (create && !SyntheticStateJournal.CreateDirectory(witness, IntPtr.Zero)) throw new IOException("witness_exists");
            directories.Add(SyntheticStateJournal.OpenDirectory(witness, false));
        }
        catch { Dispose(); throw; }
    }

    string PathFor(string name, bool witness)
    {
        if (name is not ("journal" or "lease" or "quarantine" or "head") && !IsStore(name))
            throw new IOException("invalid_fixture_file");
        return Path.Combine(witness ? Path.Combine(root, "witness") : root, name);
    }

    internal static bool IsStore(string name) => name.Length == 34 && name[32] == '-' &&
        name[33] is >= '0' and <= '4' && Guid.TryParseExact(name[..32], "N", out Guid id) &&
        name[..32] == id.ToString("N") && id != Guid.Empty;

    SafeFileHandle? Open(string name, uint access, uint disposition, bool witness = false)
    {
        string path = PathFor(name, witness);
        var handle = SyntheticStateJournal.CreateFile(path, access, 0, IntPtr.Zero, disposition, 0x00200000, IntPtr.Zero);
        if (handle.IsInvalid)
        {
            int error = Marshal.GetLastWin32Error();
            handle.Dispose();
            if (error == 2 && disposition == 3) return null;
            if (error is 32 or 33 && name == "lease") throw new FixtureBusy();
            throw new IOException("fixture_open");
        }
        if (SyntheticStateJournal.Pinned(handle, path, false)) return handle;
        handle.Dispose();
        throw new IOException("unsafe_fixture_file");
    }

    internal SafeFileHandle Acquire() => Open("lease", 0x80000000, 3) ?? throw new IOException("lease_missing");

    internal byte[]? Read(string name, bool witness = false)
    {
        using var handle = Open(name, 0x80000000, 3, witness);
        if (handle is null) return null;
        long length = RandomAccess.GetLength(handle);
        if (length > Limit) throw new IOException("fixture_file_limit");
        byte[] bytes = new byte[length];
        for (int offset = 0; offset < bytes.Length;)
        {
            int count = RandomAccess.Read(handle, bytes.AsSpan(offset), offset);
            if (count <= 0) throw new IOException("fixture_short_read");
            offset += count;
        }
        return bytes;
    }

    internal void Write(string name, byte[] bytes, bool create, bool witness = false, bool append = false)
    {
        using var handle = Open(name, 0xC0000000, create ? 1U : 3U, witness) ?? throw new IOException("fixture_missing");
        long offset = append ? RandomAccess.GetLength(handle) : 0;
        if (offset + bytes.Length > Limit) throw new IOException("fixture_file_limit");
        RandomAccess.Write(handle, bytes, offset);
        if (!append) RandomAccess.SetLength(handle, bytes.Length);
        RandomAccess.FlushToDisk(handle);
        byte[] reread = new byte[bytes.Length];
        for (int read = 0; read < reread.Length;)
        {
            int count = RandomAccess.Read(handle, reread.AsSpan(read), offset + read);
            if (count <= 0) throw new IOException("fixture_short_read");
            read += count;
        }
        if (RandomAccess.GetLength(handle) != offset + bytes.Length || !reread.AsSpan().SequenceEqual(bytes))
            throw new IOException("fixture_readback");
    }

    internal bool Remove(string name, bool witness = false)
    {
        using var handle = Open(name, 0x10080, 3, witness);
        return handle is null || SyntheticStateJournal.DeleteAndConfirm(PathFor(name, witness), handle);
    }

    internal string[] Members() => Directory.EnumerateFileSystemEntries(root).Take(401).Select(Path.GetFileName).Select(n => n!).ToArray();

    internal void Cleanup()
    {
        using (Acquire())
        {
            foreach (string name in Members().Where(IsStore))
                if (!Remove(name)) throw new IOException("cleanup_failed");
            if (!Remove("head", true) || !Remove("journal") || !Remove("quarantine")) throw new IOException("cleanup_failed");
        }
        if (!Remove("lease")) throw new IOException("cleanup_failed");
        for (int i = 0; i < 2; i++)
        {
            directories[^1].Dispose();
            directories.RemoveAt(directories.Count - 1);
            string path = i == 0 ? Path.Combine(root, "witness") : root;
            using var directory = SyntheticStateJournal.OpenDirectory(path, true);
            if (!SyntheticStateJournal.DeleteAndConfirm(path, directory)) throw new IOException("cleanup_failed");
        }
    }

    public void Dispose() { foreach (var handle in directories.AsEnumerable().Reverse()) handle.Dispose(); directories.Clear(); }
}

internal sealed class FixtureBusy : IOException;
