using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.AccessControl;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using Microsoft.Win32.SafeHandles;

internal interface IServiceLease : IDisposable
{
    ServiceEvidence Evidence { get; }
    bool Alive { get; }
    bool SameProcess(IServiceLease other);
    bool MatchesPipeServer(uint pid);
}

internal interface IServiceInspector
{
    ServiceConfiguration ReadConfiguration(ExpectedIdentity expected);
    IServiceLease Inspect(ExpectedIdentity expected, CancellationToken cancellation);
}

[SupportedOSPlatform("windows")]
internal sealed partial class WindowsInspector : IServiceInspector
{
    const uint ReadControl = 0x20000, Synchronize = 0x100000, QueryLimited = 0x1000;
    const int InsufficientBuffer = 122;
    const uint MaxNativeBuffer = 128 * 1024;

    public ServiceConfiguration ReadConfiguration(ExpectedIdentity expected)
    {
        using var manager = OpenSCManager(null, null, 1);
        Require(!manager.IsInvalid);
        using var service = OpenService(manager, expected.Manifest.ServiceName, 0x5 | ReadControl);
        Require(!service.IsInvalid);
        Require(!QueryServiceConfig(service, IntPtr.Zero, 0, out uint size) && Marshal.GetLastWin32Error() == InsufficientBuffer);
        using var buffer = Buffer(size);
        Require(QueryServiceConfig(service, buffer.DangerousGetHandle(), size, out _));
        Require(size >= Marshal.SizeOf<Config>());
        var config = Marshal.PtrToStructure<Config>(buffer.DangerousGetHandle());
        Require(QueryServiceConfig2(service, 5, out uint sidType, sizeof(uint), out _));
        Require(QueryServiceStatusEx(service, 0, out var status, (uint)Marshal.SizeOf<Status>(), out _));
        Require(status.Type == config.Type);
        var acl = InspectAccess(service, 2);
        var admins = expected.Manifest.InstallAdministrators;
        const uint serviceMutation = 0x500D01F2; // config, start/stop, custom control, DAC and ownership
        bool protectedConfig = acl.Complete && admins.Contains(acl.Owner, StringComparer.Ordinal) &&
            acl.Allows.All(a => (a.Mask & serviceMutation) == 0 || admins.Contains(a.Sid, StringComparer.Ordinal));
        return new(ReadString(config.Binary, buffer.DangerousGetHandle(), size),
            ReadString(config.Account, buffer.DangerousGetHandle(), size),
            config.Type, config.Start, sidType, status.State == 4 ? status.Pid : 0, acl.DescriptorSha256, protectedConfig);
    }

    public IServiceLease Inspect(ExpectedIdentity expected, CancellationToken cancellation)
    {
        cancellation.ThrowIfCancellationRequested();
        var config = ReadConfiguration(expected);
        if (IdentityPolicy.Configuration(expected, config) != Finding.VerifiedEvidence) throw new InspectionException(Finding.InvalidConfiguration);
        if (config.Pid == 0) throw new InspectionException(Finding.UnexpectedProcess);
        if (LookupServiceSid() != expected.Manifest.ServiceSid) throw new InspectionException(Finding.MismatchedIdentity);
        var process = OpenObservedProcess(config.Pid);
        var objects = new List<SafeFileHandle>();
        try
        {
            Require(GetProcessId(process) == config.Pid && IsAlive(process));
            var image = new StringBuilder(1024);
            uint length = (uint)image.Capacity;
            Require(QueryFullProcessImageName(process, 0, image, ref length));
            if (!ExpectedIdentity.Same(image.ToString(), expected.Executable)) throw new InspectionException(Finding.UntrustedInstallation);
            var token = InspectToken(process);
            var inventory = new List<ObjectEvidence>();
            foreach (var pin in expected.Manifest.Installation)
            {
                cancellation.ThrowIfCancellationRequested();
                Require(GetDriveType(pin.Path[..3]) == 3);
                var handle = CreateFile(pin.Path, ReadControl | 0x80 | (pin.Sha256 is null ? 0U : 1U),
                    1, IntPtr.Zero, 3, 0x02200000, IntPtr.Zero);
                if (handle.IsInvalid) { handle.Dispose(); throw new InspectionException(Finding.UntrustedInstallation); }
                objects.Add(handle);
                inventory.Add(InspectObject(handle, pin, expected, cancellation));
            }
            Require(ReadConfiguration(expected) == config && IsAlive(process));
            return new Lease(process, objects, new(config, token, image.ToString(), config.Pid, true, inventory.ToArray()));
        }
        catch
        {
            foreach (var obj in objects) obj.Dispose();
            process.Dispose();
            throw;
        }
    }

    sealed class Lease(SafeProcessHandle process, List<SafeFileHandle> objects, ServiceEvidence evidence) : IServiceLease
    {
        readonly SafeProcessHandle ownedProcess = process;
        public ServiceEvidence Evidence => evidence;
        public bool Alive => IsAlive(ownedProcess);
        public bool SameProcess(IServiceLease other) => other is Lease l && Alive && l.Alive &&
            CompareObjectHandles(ownedProcess, l.ownedProcess);
        public bool MatchesPipeServer(uint pid)
        {
            if (pid == 0 || pid != Evidence.ProcessPid || !Alive) return false;
            using var observed = OpenObservedProcess(pid);
            return IsAlive(observed) && CompareObjectHandles(ownedProcess, observed) && Alive;
        }
        public void Dispose()
        {
            foreach (var obj in objects) obj.Dispose();
            ownedProcess.Dispose();
        }
    }

    static SafeProcessHandle OpenObservedProcess(uint pid)
    {
        var handle = OpenProcess(QueryLimited | Synchronize, false, pid);
        if (!handle.IsInvalid) return handle;
        handle.Dispose();
        throw new InspectionException(Finding.UnexpectedProcess);
    }
    static bool IsAlive(SafeProcessHandle process) => WaitForSingleObject(process, 0) == 258;

    internal static TokenEvidence InspectToken(SafeProcessHandle process)
    {
        Require(OpenProcessToken(process, 8, out var token));
        using (token)
        {
            var before = ReadToken(token, 10, ReadTokenStatistics);
            string user = ReadToken(token, 1, ReadTokenSid);
            string[] groups = ReadToken(token, 2, (ptr, size) => Groups(ptr, size, true));
            string[] restricted = ReadToken(token, 11, (ptr, size) => Groups(ptr, size, false));
            string integrity = ReadToken(token, 25, ReadTokenSid);
            Require(integrity.StartsWith("S-1-16-", StringComparison.Ordinal));
            bool unsafePrivileges = ReadToken(token, 3, UnsafePrivileges);
            var statistics = ReadToken(token, 10, ReadTokenStatistics);
            Require(statistics.TokenType == 1 && statistics.TokenId != 0 &&
                statistics.TokenId == before.TokenId && statistics.Modified == before.Modified);
            return new(user, groups, restricted, int.Parse(integrity[7..], System.Globalization.CultureInfo.InvariantCulture),
                unsafePrivileges, statistics.TokenId, statistics.Modified);
        }
    }

    internal static WindowsCallerVerification.TokenStatistics ReadTokenStatistics(IntPtr ptr, uint size)
    {
        Require(size == Marshal.SizeOf<WindowsCallerVerification.TokenStatistics>());
        return Marshal.PtrToStructure<WindowsCallerVerification.TokenStatistics>(ptr);
    }

    static string LookupServiceSid()
    {
        const string account = "NT SERVICE\\" + ExpectedIdentity.ServiceName;
        uint sidBytes = 0, domainChars = 0;
        Require(!LookupAccountName(null, account, IntPtr.Zero, ref sidBytes, null, ref domainChars, out _)
            && Marshal.GetLastWin32Error() == InsufficientBuffer && domainChars is > 0 and <= 1024);
        using var sid = Buffer(sidBytes);
        var domain = new StringBuilder((int)domainChars);
        Require(LookupAccountName(null, account, sid.DangerousGetHandle(), ref sidBytes, domain, ref domainChars, out _));
        return Sid(sid.DangerousGetHandle());
    }

    static bool UnsafePrivileges(IntPtr ptr, uint size)
    {
        string[] forbidden = ["SeBackupPrivilege", "SeRestorePrivilege", "SeTakeOwnershipPrivilege", "SeDebugPrivilege",
            "SeTcbPrivilege", "SeImpersonatePrivilege", "SeAssignPrimaryTokenPrivilege", "SeLoadDriverPrivilege",
            "SeCreateTokenPrivilege", "SeManageVolumePrivilege"];
        var luidSet = new HashSet<long>();
        foreach (var privilege in forbidden)
        {
            Require(LookupPrivilegeValue(null, privilege, out long luid));
            luidSet.Add(luid);
        }
        Require(size >= 4);
        int count = Marshal.ReadInt32(ptr);
        Require(count is >= 0 and <= 1024 && size >= 4 + count * 12);
        // TOKEN_PRIVILEGES starts with DWORD count, followed by LUID (two DWORDs)
        // and DWORD attributes. A disabled privilege can still be enabled later.
        for (int i = 0; i < count; i++)
            if (luidSet.Contains(Marshal.ReadInt64(ptr, 4 + i * 12))) return true;
        return false;
    }

    static string[] Groups(IntPtr ptr, uint size, bool enabledOnly)
    {
        Require(size >= IntPtr.Size);
        int count = Marshal.ReadInt32(ptr);
        int stride = Marshal.SizeOf<SidAndAttributes>();
        Require(count is >= 0 and <= 1024 && size >= IntPtr.Size + count * stride);
        var result = new List<string>();
        for (int i = 0; i < count; i++)
        {
            var entry = Marshal.PtrToStructure<SidAndAttributes>(ptr + IntPtr.Size + i * stride);
            if (!enabledOnly || (entry.Attributes & 0x14) == 4) result.Add(BoundedSid(entry.Sid, ptr, size));
        }
        return result.ToArray();
    }

    internal static T ReadToken<T>(SafeAccessTokenHandle token, int kind, Func<IntPtr, uint, T> read)
    {
        Require(!GetTokenInformation(token, kind, IntPtr.Zero, 0, out uint size) && Marshal.GetLastWin32Error() == InsufficientBuffer);
        using var buffer = Buffer(size);
        Require(GetTokenInformation(token, kind, buffer.DangerousGetHandle(), size, out _));
        return read(buffer.DangerousGetHandle(), size);
    }

    internal static string ReadTokenSid(IntPtr ptr, uint size)
    {
        Require(size >= Marshal.SizeOf<SidAndAttributes>());
        return BoundedSid(Marshal.ReadIntPtr(ptr), ptr, size);
    }

    static string BoundedSid(IntPtr address, IntPtr start, uint size)
    {
        long offset = address.ToInt64() - start.ToInt64();
        Require(offset >= 0 && offset + 8 <= size);
        Require(offset + 8 + Marshal.ReadByte(address, 1) * 4 <= size);
        return Sid(address);
    }

    static string ReadString(IntPtr address, IntPtr start, uint size)
    {
        long offset = address.ToInt64() - start.ToInt64();
        Require(offset >= 0 && offset + 2 <= size && offset % 2 == 0);
        int limit = (int)((size - offset) / 2);
        for (int i = 0; i < limit; i++)
            if (Marshal.ReadInt16(address, i * 2) == 0) return Marshal.PtrToStringUni(address, i) ?? "";
        throw new InspectionException(Finding.InvalidConfiguration);
    }

    static string Sid(IntPtr address)
    {
        Require(address != IntPtr.Zero);
        return new SecurityIdentifier(address).Value;
    }

    static ObjectEvidence InspectObject(SafeFileHandle file, ObjectPin pin, ExpectedIdentity expected, CancellationToken cancellation)
    {
        var filesystem = new StringBuilder(32);
        Require(GetVolumeInformationByHandle(file, IntPtr.Zero, 0, out _, out _, out _, filesystem, (uint)filesystem.Capacity)
            && filesystem.ToString() == "NTFS");
        Require(GetFileInformationByHandle(file, out var information));
        Require(GetFileInformationByHandleEx(file, 18, out var identity, (uint)Marshal.SizeOf<FileIdentity>()));
        var final = new StringBuilder(1024);
        uint length = GetFinalPathNameByHandle(file, final, (uint)final.Capacity, 0);
        bool pathMatches = length > 0 && length < final.Capacity &&
            ExpectedIdentity.Same(final.ToString(), @"\\?\" + pin.Path);
        bool directory = (information.Attributes & 16) != 0, reparse = (information.Attributes & 1024) != 0;
        string fileId = $"{identity.Volume:X16}:{identity.FileId:N}";
        var access = InspectAccess(file, 1);
        // Check every ancestor before opening the child, and check file identity
        // and access before reading bytes through that same retained handle.
        if (reparse || !pathMatches || fileId != pin.FileId || directory != (pin.Sha256 is null) ||
            (!directory && information.Links != 1) ||
            !IdentityPolicy.Protected(access, expected.Manifest.InstallAdministrators, pin.DescriptorSha256))
            throw new InspectionException(Finding.UntrustedInstallation);
        string? hash = directory ? null : Hash(file, cancellation);
        if (!ExpectedIdentity.Same(hash, pin.Sha256)) throw new InspectionException(Finding.UntrustedInstallation);
        return new(pin.Path, fileId, hash, reparse, directory, pathMatches, information.Links, access);
    }

    static string Hash(SafeFileHandle file, CancellationToken cancellation)
    {
        long size = RandomAccess.GetLength(file);
        Require(size is > 0 and <= 64 * 1024 * 1024);
        using var hash = IncrementalHash.CreateHash(HashAlgorithmName.SHA256);
        byte[] buffer = new byte[65536];
        for (long offset = 0; offset < size;)
        {
            cancellation.ThrowIfCancellationRequested();
            int read = RandomAccess.Read(file, buffer.AsSpan(0, (int)Math.Min(buffer.Length, size - offset)), offset);
            Require(read > 0);
            hash.AppendData(buffer, 0, read);
            offset += read;
        }
        Require(RandomAccess.GetLength(file) == size);
        return Convert.ToHexString(hash.GetHashAndReset());
    }

    // SE_FILE_OBJECT also covers a named-pipe handle. The resulting descriptor
    // is an observation. It does not substitute for effective-access testing.
    internal static AccessEvidence InspectAccess(SafeHandle handle, uint kind)
    {
        uint result = GetSecurityInfo(handle, kind, 5, out _, out _, out _, out _, out IntPtr descriptor);
        try
        {
            if (result != 0) throw new Win32Exception((int)result);
            Require(descriptor != IntPtr.Zero);
            uint length = GetSecurityDescriptorLength(descriptor);
            Require(length is > 0 and <= MaxNativeBuffer);
            var data = new byte[length];
            Marshal.Copy(descriptor, data, 0, data.Length);
            var sd = new RawSecurityDescriptor(data, 0);
            bool complete = sd.Owner is not null && sd.DiscretionaryAcl is not null &&
                sd.ControlFlags.HasFlag(ControlFlags.DiscretionaryAclPresent);
            var allows = new List<AccessRule>();
            if (sd.DiscretionaryAcl is not null)
            {
                foreach (GenericAce ace in sd.DiscretionaryAcl)
                {
                    if (ace is not CommonAce { IsCallback: false } common ||
                        common.AceQualifier is not (AceQualifier.AccessAllowed or AceQualifier.AccessDenied))
                    { complete = false; continue; }
                    if (common.AceQualifier == AceQualifier.AccessAllowed)
                        allows.Add(new(common.SecurityIdentifier.Value, unchecked((uint)common.AccessMask), common.IsInherited));
                }
            }
            string sddl = sd.GetSddlForm(AccessControlSections.Owner | AccessControlSections.Access);
            return new(sd.Owner?.Value ?? "", complete, Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(sddl))), allows.ToArray());
        }
        finally { if (descriptor != IntPtr.Zero) _ = LocalFree(descriptor); }
    }

    static void Require(bool condition) { if (!condition) throw new Win32Exception(); }
    static NativeBuffer Buffer(uint size)
    {
        Require(size is > 0 and <= MaxNativeBuffer);
        return new((int)size);
    }
    sealed class NativeBuffer : SafeHandleZeroOrMinusOneIsInvalid
    {
        internal NativeBuffer(int size) : base(true) => SetHandle(Marshal.AllocHGlobal(size));
        protected override bool ReleaseHandle() { Marshal.FreeHGlobal(handle); return true; }
    }
    sealed class ScmHandle : SafeHandleZeroOrMinusOneIsInvalid
    {
        public ScmHandle() : base(true) { }
        protected override bool ReleaseHandle() => CloseServiceHandle(handle);
    }
    [StructLayout(LayoutKind.Sequential)] struct Config
    {
        public uint Type, Start, Error;
        public IntPtr Binary, Group;
        public uint Tag;
        public IntPtr Dependencies, Account, Name;
    }
    [StructLayout(LayoutKind.Sequential)] struct Status
    {
        public uint Type, State, Controls, Win32, Specific, Checkpoint, WaitHint, Pid, Flags;
    }
    [StructLayout(LayoutKind.Sequential)] struct SidAndAttributes { public IntPtr Sid; public uint Attributes; }
    [StructLayout(LayoutKind.Sequential)] struct FileIdentity { public ulong Volume; public Guid FileId; }
    [StructLayout(LayoutKind.Sequential)] struct FileInformation
    {
        public uint Attributes;
        public System.Runtime.InteropServices.ComTypes.FILETIME Created, Accessed, Written;
        public uint Volume, SizeHigh, SizeLow, Links, IndexHigh, IndexLow;
    }

    [UnsupportedOSPlatform("browser")]
    internal static bool AbiLayoutMatches() =>
        Marshal.SizeOf<Config>() == (IntPtr.Size == 8 ? 64 : 36) &&
        Marshal.OffsetOf<Config>(nameof(Config.Account)).ToInt64() == (IntPtr.Size == 8 ? 48 : 28) &&
        Marshal.SizeOf<Status>() == 36 && Marshal.OffsetOf<Status>(nameof(Status.Pid)).ToInt64() == 28 &&
        Marshal.SizeOf<SidAndAttributes>() == IntPtr.Size * 2 &&
        Marshal.SizeOf<FileIdentity>() == 24 && Marshal.SizeOf<FileInformation>() == 52 &&
        Marshal.SizeOf<WindowsCallerVerification.TokenStatistics>() == 56 &&
        Marshal.OffsetOf<WindowsCallerVerification.TokenStatistics>(nameof(WindowsCallerVerification.TokenStatistics.Modified)).ToInt64() == 48;

    [DllImport("advapi32.dll", EntryPoint="OpenSCManagerW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern ScmHandle OpenSCManager(string? machine, string? database, uint access);
    [DllImport("advapi32.dll", EntryPoint="OpenServiceW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern ScmHandle OpenService(ScmHandle manager, string name, uint access);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool CloseServiceHandle(IntPtr handle);
    [DllImport("advapi32.dll", EntryPoint="QueryServiceConfigW", ExactSpelling=true, SetLastError=true)]
    static extern bool QueryServiceConfig(ScmHandle service, IntPtr buffer, uint bytes, out uint needed);
    [DllImport("advapi32.dll", EntryPoint="QueryServiceConfig2W", ExactSpelling=true, SetLastError=true)]
    static extern bool QueryServiceConfig2(ScmHandle service, uint level, out uint buffer, uint bytes, out uint needed);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool QueryServiceStatusEx(ScmHandle service, uint level, out Status status, uint bytes, out uint needed);
    [DllImport("kernel32.dll", SetLastError=true)] static extern SafeProcessHandle OpenProcess(uint access, bool inherit, uint pid);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint GetProcessId(SafeProcessHandle process);
    [DllImport("kernel32.dll", SetLastError=true)] static extern uint WaitForSingleObject(SafeProcessHandle process, uint millis);
    [DllImport("kernelbase.dll", SetLastError=true)] static extern bool CompareObjectHandles(SafeProcessHandle a, SafeProcessHandle b);
    [DllImport("kernel32.dll", EntryPoint="QueryFullProcessImageNameW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern bool QueryFullProcessImageName(SafeProcessHandle process, uint flags, StringBuilder name, ref uint size);
    [DllImport("advapi32.dll", SetLastError=true)] static extern bool OpenProcessToken(SafeProcessHandle process, uint access, out SafeAccessTokenHandle token);
    [DllImport("advapi32.dll", SetLastError=true)]
    static extern bool GetTokenInformation(SafeAccessTokenHandle token, int kind, IntPtr data, uint length, out uint needed);
    [DllImport("advapi32.dll", EntryPoint="LookupAccountNameW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern bool LookupAccountName(string? system, string account, IntPtr sid, ref uint sidBytes,
        StringBuilder? domain, ref uint domainChars, out uint sidUse);
    [DllImport("advapi32.dll", EntryPoint="LookupPrivilegeValueW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern bool LookupPrivilegeValue(string? system, string name, out long luid);
    [DllImport("kernel32.dll", EntryPoint="CreateFileW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern SafeFileHandle CreateFile(string path, uint access, uint share, IntPtr security, uint disposition, uint flags, IntPtr template);
    [DllImport("kernel32.dll", EntryPoint="GetDriveTypeW", CharSet=CharSet.Unicode, ExactSpelling=true)]
    static extern uint GetDriveType(string root);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetFileInformationByHandle(SafeFileHandle file, out FileInformation information);
    [DllImport("kernel32.dll", SetLastError=true)] static extern bool GetFileInformationByHandleEx(SafeFileHandle file, int kind, out FileIdentity information, uint size);
    [DllImport("kernel32.dll", EntryPoint="GetVolumeInformationByHandleW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern bool GetVolumeInformationByHandle(SafeFileHandle file, IntPtr volumeName, uint volumeLength,
        out uint serial, out uint maxComponent, out uint flags, StringBuilder filesystem, uint filesystemLength);
    [DllImport("kernel32.dll", EntryPoint="GetFinalPathNameByHandleW", CharSet=CharSet.Unicode, ExactSpelling=true, SetLastError=true)]
    static extern uint GetFinalPathNameByHandle(SafeFileHandle file, StringBuilder path, uint length, uint flags);
    [DllImport("advapi32.dll")] static extern uint GetSecurityInfo(SafeHandle handle, uint kind, uint requested,
        out IntPtr owner, out IntPtr group, out IntPtr dacl, out IntPtr sacl, out IntPtr descriptor);
    [DllImport("advapi32.dll")] static extern uint GetSecurityDescriptorLength(IntPtr descriptor);
    [DllImport("kernel32.dll")] static extern IntPtr LocalFree(IntPtr memory);
}

internal sealed class InspectionException(Finding finding) : Exception
{
    internal Finding Finding => finding;
}
