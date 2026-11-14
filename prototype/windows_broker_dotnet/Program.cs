using System.IO.Pipes;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Security.Principal;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Microsoft.Win32.SafeHandles;

// Development experiment: no production data, authorization, or server trust.
internal static partial class Program
{
    const int MaxRequest = 1024, MaxResponse = 2048, TimeoutMs = 2500;
    const uint Duplex = 3, Overlapped = 0x40000000, FirstInstance = 0x80000;
    const uint RejectRemote = 8, ClientRights = 0x00100183;
    static readonly Regex PipeName = new("^SovereignAI-dev-[a-f0-9]{32}$", RegexOptions.CultureInvariant);
    static int diagnosticCount;
    static int securityDiagnosticCount;

    static void Diagnostic(string stage, Exception e)
    {
        if (Interlocked.Increment(ref diagnosticCount) > 32) return;
        string category = e switch { OperationCanceledException => "timeout", UnauthorizedAccessException => "access_denied",
            Win32Exception => "native_error", InvalidDataException => "invalid_data", IOException => "io_error",
            _ => "operation_failed" };
        int code = e is Win32Exception w ? w.NativeErrorCode : e.HResult;
        Console.Error.WriteLine($"IPC stage={stage} category={category} code={code}");
    }

    [StructLayout(LayoutKind.Sequential)]
    struct SecurityAttributes { public int Length; public IntPtr Descriptor; public int Inherit; }

    [DllImport("advapi32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern bool ConvertStringSecurityDescriptorToSecurityDescriptor(string text, uint revision, out IntPtr descriptor, out uint size);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    static extern SafePipeHandle CreateNamedPipe(string name, uint openMode, uint pipeMode, uint maxInstances,
        uint outBuffer, uint inBuffer, uint defaultTimeout, ref SecurityAttributes security);
    [DllImport("kernel32.dll")]
    static extern IntPtr LocalFree(IntPtr memory);

    static NamedPipeServerStream CreateServer(string name, string expectedProcessSid)
    {
        // Data read/write, read/write attributes and synchronize; excludes create-instance bit.
        string sddl = $"D:P(A;;0x{ClientRights:x8};;;{expectedProcessSid})";
        if (!ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1, out IntPtr sd, out _))
            throw new Win32Exception(Marshal.GetLastWin32Error());
        try
        {
            var attrs = new SecurityAttributes { Length = Marshal.SizeOf<SecurityAttributes>(), Descriptor = sd, Inherit = 0 };
            var h = CreateNamedPipe(@"\\.\pipe\" + name, Duplex | Overlapped | FirstInstance,
                RejectRemote, 1, MaxResponse + 1, MaxRequest + 1, TimeoutMs, ref attrs);
            if (h.IsInvalid) { int error = Marshal.GetLastWin32Error(); h.Dispose(); throw new Win32Exception(error); }
            try { return new NamedPipeServerStream(PipeDirection.InOut, true, false, h); }
            catch { h.Dispose(); throw; }
        }
        finally { LocalFree(sd); }
    }

    static byte[] Response(bool ok, string value) => JsonSerializer.SerializeToUtf8Bytes(new {
        version = 1, ok, development_only = true, trusted_broker = false,
        result = ok ? value : null, error = ok ? null : value
    });

    sealed record SidCheck(bool Allowed, bool PeerAvailable, bool ExpectedAvailable, bool CleanupVerified, string Reason);

    static SidCheck CompareSids(string? expectedSid, string? peerSid, bool tokenAcquired = true, bool cleanupVerified = true)
    {
        bool expectedAvailable = !string.IsNullOrWhiteSpace(expectedSid);
        bool peerAvailable = !string.IsNullOrWhiteSpace(peerSid);
        if (!expectedAvailable) return new(false, peerAvailable, false, cleanupVerified, "EXPECTED_SID_UNAVAILABLE");
        if (!tokenAcquired) return new(false, false, true, cleanupVerified, "TOKEN_ACQUISITION_FAILURE");
        if (!cleanupVerified) return new(false, peerAvailable, true, false, "IMPERSONATION_CLEANUP_FAILURE");
        if (!peerAvailable) return new(false, false, true, true, "PEER_SID_UNAVAILABLE");
        bool match = string.Equals(expectedSid, peerSid, StringComparison.OrdinalIgnoreCase);
        return new(match, true, true, true, match ? "SID_MATCH" : "SID_MISMATCH");
    }

    static SidCheck EvaluateCapturedSid(string? expectedSid, Func<string?> acquirePeerSid, Func<bool> verifyCleanup)
    {
        string? peerSid;
        try { peerSid = acquirePeerSid(); }
        catch (Exception) { return CompareSids(expectedSid, null, tokenAcquired: false); }
        try { return CompareSids(expectedSid, peerSid, cleanupVerified: verifyCleanup()); }
        catch (Exception) { return CompareSids(expectedSid, peerSid, cleanupVerified: false); }
    }

    static string SidDiagnostic(SidCheck check) =>
        $"peer_sid_available={check.PeerAvailable.ToString().ToLowerInvariant()} expected_sid_available={check.ExpectedAvailable.ToString().ToLowerInvariant()} peer_sid_matches={check.Allowed.ToString().ToLowerInvariant()} cleanup_verified={check.CleanupVerified.ToString().ToLowerInvariant()} reason={check.Reason}";

    static string GetProcessSid()
    {
        using WindowsIdentity? process = WindowsIdentity.GetCurrent(false);
        return process?.User?.Value ?? throw new InvalidOperationException("Process identity unavailable");
    }

    // This method intentionally has no await: RunAsClient changes the executing Windows thread only.
    static SidCheck VerifyIdentity(string? expectedProcessSid, Action<Action> scope, Func<IDisposable?> token, Func<IDisposable, string?> sid, Func<bool> noThreadToken, Func<string> processSid)
    {
        if (string.IsNullOrWhiteSpace(expectedProcessSid)) return CompareSids(expectedProcessSid, null);
        string? peerSid = null;
        bool tokenAcquired = false;
        try {
            scope(() => {
                using IDisposable? client = token();
                peerSid = client is null ? null : sid(client);
            });
            tokenAcquired = true;
        }
        catch (Exception) { tokenAcquired = false; }

        try {
            bool reverted = noThreadToken() && string.Equals(processSid(), expectedProcessSid, StringComparison.OrdinalIgnoreCase);
            if (!reverted) return new(false, false, true, false, "IMPERSONATION_CLEANUP_FAILURE");
            return CompareSids(expectedProcessSid, peerSid, tokenAcquired: tokenAcquired, cleanupVerified: true);
        }
        catch (Exception) { return CompareSids(expectedProcessSid, peerSid, cleanupVerified: false); }
    }

    static SidCheck VerifyConnectedClientSid(NamedPipeServerStream pipe, string? expectedProcessSid) =>
        VerifyIdentity(expectedProcessSid, action => pipe.RunAsClient(() => action()),
            () => WindowsIdentity.GetCurrent(true), client => ((WindowsIdentity)client).User?.Value,
            () => { using WindowsIdentity? residual = WindowsIdentity.GetCurrent(true); return residual is null; }, GetProcessSid);

    static bool StopOnUnsafeCleanup(SidCheck identity, Action terminate)
    {
        if (identity.CleanupVerified) return false;
        terminate(); return true;
    }

    // Only known protocol values may leave this diagnostic client on stdout.
    // A valid negative response is a protocol-level denial, distinct from malformed data.
    static bool TryValidateResponse(byte[] bytes, string scenario, out bool ok, out byte[] sanitized)
    {
        ok = false; sanitized = Array.Empty<byte>();
        try {
            using var doc = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 4 });
            if (doc.RootElement.ValueKind != JsonValueKind.Object) return false;
            var fields = new Dictionary<string, JsonElement>(StringComparer.Ordinal);
            foreach (var field in doc.RootElement.EnumerateObject())
                if (!fields.TryAdd(field.Name, field.Value)) return false;
            if (fields.Count != 6 || !fields.TryGetValue("version", out var version) ||
                version.ValueKind != JsonValueKind.Number || !version.TryGetInt32(out int v) || v != 1 ||
                !fields.TryGetValue("ok", out var outcome) || outcome.ValueKind is not (JsonValueKind.True or JsonValueKind.False) ||
                !fields.TryGetValue("development_only", out var development) || development.ValueKind != JsonValueKind.True ||
                !fields.TryGetValue("trusted_broker", out var trusted) || trusted.ValueKind != JsonValueKind.False ||
                !fields.TryGetValue("result", out var result) || !fields.TryGetValue("error", out var error)) return false;
            ok = outcome.GetBoolean();
            if (ok) {
                if (result.ValueKind != JsonValueKind.String || error.ValueKind != JsonValueKind.Null) return false;
                string? value = result.GetString();
                string? expected = scenario switch {
                    "ping" => "pong", "status" => "development_only",
                    "capabilities" => "ping,status,capabilities", _ => null };
                if (expected is null || value != expected) return false;
                sanitized = Response(true, value);
            } else {
                if (result.ValueKind != JsonValueKind.Null || error.ValueKind != JsonValueKind.String) return false;
                string? value = error.GetString();
                if (value is not ("DENIED" or "REQUEST_TOO_LARGE" or "INVALID_REQUEST" or "UNSUPPORTED_COMMAND")) return false;
                sanitized = Response(false, value);
            }
            return true;
        } catch (JsonException) { return false; }
    }

    static byte[] Process(byte[] bytes)
    {
        try
        {
            using var doc = JsonDocument.Parse(bytes, new JsonDocumentOptions { MaxDepth = 4 });
            if (doc.RootElement.ValueKind != JsonValueKind.Object) return Response(false, "INVALID_REQUEST");
            int count = 0; JsonElement ver = default, cmd = default;
            foreach (var prop in doc.RootElement.EnumerateObject()) {
                count++;
                if (prop.NameEquals("version") && ver.ValueKind == JsonValueKind.Undefined) ver = prop.Value;
                else if (prop.NameEquals("command") && cmd.ValueKind == JsonValueKind.Undefined) cmd = prop.Value;
                else return Response(false, "INVALID_REQUEST");
            }
            if (count != 2 || ver.ValueKind != JsonValueKind.Number || !ver.TryGetInt32(out int v) || v != 1 || cmd.ValueKind != JsonValueKind.String)
                return Response(false, "INVALID_REQUEST");
            return cmd.GetString() switch {
                "ping" => Response(true, "pong"), "status" => Response(true, "development_only"),
                "capabilities" => Response(true, "ping,status,capabilities"), _ => Response(false, "UNSUPPORTED_COMMAND") };
        }
        catch (JsonException) { return Response(false, "INVALID_REQUEST"); }
    }

    // Newline-delimited frames. EOF before newline is never a complete request.
    static async Task<byte[]> ReadFrame(Stream stream, int limit, CancellationToken ct)
    {
        using var buffer = new MemoryStream();
        byte[] one = new byte[1];
        while (true) {
            int n = await stream.ReadAsync(one, ct);
            if (n == 0) throw new EndOfStreamException();
            if (one[0] == 10) return buffer.ToArray();
            if (buffer.Length >= limit) throw new InvalidDataException("Too large");
            buffer.WriteByte(one[0]);
        }
    }

    static async Task WriteFrame(Stream stream, byte[] payload, CancellationToken ct)
    {
        if (payload.Length > MaxResponse) throw new InvalidDataException();
        byte[] frame = new byte[payload.Length + 1]; payload.CopyTo(frame, 0); frame[^1] = 10;
        await stream.WriteAsync(frame, ct);
        await stream.FlushAsync(ct);
    }

    static async Task<int> Serve(string name, int seconds)
    {
        string expectedProcessSid;
        try { expectedProcessSid = GetProcessSid(); }
        catch (Exception e) { Diagnostic("server_identity", e); return 1; }
        using var lifetime = new CancellationTokenSource(TimeSpan.FromSeconds(seconds));
        Console.CancelKeyPress += (_, e) => { e.Cancel = true; lifetime.Cancel(); };
        bool readyReported = false;
        while (!lifetime.IsCancellationRequested) {
            NamedPipeServerStream pipe;
            try { pipe = CreateServer(name, expectedProcessSid); }
            catch (Exception e) { Diagnostic("server_create", e); return 1; }
            using (pipe) {
            if (!readyReported) { Console.WriteLine("READY"); readyReported = true; }
            string stage = "server_connect";
            try {
                using (var connect = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token)) {
                    connect.CancelAfter(TimeoutMs); await pipe.WaitForConnectionAsync(connect.Token);
                }
                byte[]? req = null;
                bool oversized = false;
                try {
                    stage = "server_read";
                    using var read = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
                    read.CancelAfter(TimeoutMs); req = await ReadFrame(pipe, MaxRequest, read.Token);
                }
                catch (InvalidDataException) { oversized = true; }
                // SID comparison is development-only; it does not authenticate this server to the client.
                stage = "peer_verification";
                SidCheck identity = VerifyConnectedClientSid(pipe, expectedProcessSid);
                if (identity.Allowed ? Interlocked.Increment(ref diagnosticCount) <= 16 : Interlocked.Increment(ref securityDiagnosticCount) <= 16) Console.Error.WriteLine(SidDiagnostic(identity));
                bool stop = false;
                if (StopOnUnsafeCleanup(identity, () => stop = true) && stop) return 1;
                byte[] response = !identity.Allowed
                    ? Response(false, "DENIED") : oversized ? Response(false, "REQUEST_TOO_LARGE") : Process(req!);
                stage = "server_write";
                using var write = CancellationTokenSource.CreateLinkedTokenSource(lifetime.Token);
                write.CancelAfter(TimeoutMs); await WriteFrame(pipe, response, write.Token);
            }
            catch (OperationCanceledException) when (lifetime.IsCancellationRequested) { break; }
            catch (OperationCanceledException) when (stage == "server_connect") { }
            catch (Exception e) { Diagnostic(stage, e); }
            }
        }
        return 0;
    }

    static async Task<int> Client(string name, string scenario)
    {
        byte[] raw = scenario switch {
            "ping" or "status" or "capabilities" => Encoding.UTF8.GetBytes($"{{\"version\":1,\"command\":\"{scenario}\"}}"),
            "unsupported" => "{\"version\":1,\"command\":\"ingest\"}"u8.ToArray(),
            "malformed" => "{"u8.ToArray(), "oversized" => Enumerable.Repeat((byte)'x', MaxRequest + 1).ToArray(),
            "stall" or "disconnect" => Array.Empty<byte>(), _ => throw new ArgumentException("Invalid scenario") };
        using var pipe = new NamedPipeClientStream(".", name, (PipeAccessRights)ClientRights,
            PipeOptions.Asynchronous, TokenImpersonationLevel.Impersonation, HandleInheritability.None);
        string stage = "client_connect";
        try {
        using (var connect = new CancellationTokenSource(TimeoutMs)) await pipe.ConnectAsync(connect.Token);
        if (scenario == "disconnect") return 0;
        if (scenario == "stall") { await Task.Delay(TimeoutMs + 500); return 0; }
        byte[] frame = new byte[raw.Length + 1]; raw.CopyTo(frame, 0); frame[^1] = 10;
        stage = "client_write";
        using (var write = new CancellationTokenSource(TimeoutMs)) await pipe.WriteAsync(frame, write.Token);
        stage = "client_read";
        byte[] response;
        using (var read = new CancellationTokenSource(TimeoutMs)) response = await ReadFrame(pipe, MaxResponse, read.Token);
        stage = "response_validation";
        if (!TryValidateResponse(response, scenario, out bool ok, out byte[] sanitized)) {
            Console.Error.WriteLine("IPC stage=response_validation category=invalid_protocol code=0");
            return 1;
        }
        Console.WriteLine(Encoding.UTF8.GetString(sanitized));
        return ok ? 0 : 3; // Exit 3 is a validated, explicit protocol denial.
        } catch (Exception e) { Diagnostic(stage, e); return 1; }
    }

    static async Task<int> Main(string[] args)
    {
        if (args.Length == 1 && args[0] == "launcher-selftest") return LauncherSelfTest();
        if (args.Length == 1 && args[0] == "inspect-launch" && OperatingSystem.IsWindows()) return await InspectLaunch();
        if (args.Length == 1 && args[0] == "selftest") {
            const string expected = "test-sid";
            SidCheck Test(Action<Action> scope, Func<IDisposable?> token, Func<IDisposable, string?> sid, Func<bool> thread, Func<string> process) => VerifyIdentity(expected, scope, token, sid, thread, process);
            Action<Action> normal = action => action();
            Func<IDisposable?> goodToken = () => new MemoryStream();
            Func<IDisposable, string?> goodSid = _ => expected;
            Func<bool> clean = () => true;
            Func<string> same = () => expected;
            var cases = new[] {
                Test(normal, goodToken, goodSid, clean, same),
                Test(normal, goodToken, _ => "other", clean, same),
                Test(normal, () => null, goodSid, clean, same),
                Test(normal, () => throw new Exception(), goodSid, clean, same),
                Test(normal, goodToken, _ => throw new Exception(), clean, same),
                Test(_ => throw new Exception(), goodToken, goodSid, clean, same),
                Test(normal, goodToken, goodSid, () => false, same),
                Test(normal, () => throw new Exception(), goodSid, () => false, same),
                Test(normal, goodToken, goodSid, () => throw new Exception(), same),
                Test(normal, goodToken, goodSid, clean, () => "changed") };
            if (!cases[0].Allowed || cases.Skip(1).Any(c => c.Allowed)) return 1;
            for (int i = 6; i < cases.Length; i++) {
                bool terminationRequested = false;
                if (!StopOnUnsafeCleanup(cases[i], () => terminationRequested = true) || !terminationRequested) return 1;
            }
            string ok = Encoding.UTF8.GetString(Process("{\"version\":1,\"command\":\"ping\"}"u8.ToArray()));
            string deny = Encoding.UTF8.GetString(Process("{\"version\":1,\"command\":\"ingest\"}"u8.ToArray()));
            string dup = Encoding.UTF8.GetString(Process("{\"version\":1,\"command\":\"ping\",\"command\":\"status\"}"u8.ToArray()));
            if (!ok.Contains("pong") || !deny.Contains("UNSUPPORTED_COMMAND") || !dup.Contains("INVALID_REQUEST")) return 1;
            if (!TryValidateResponse(Encoding.UTF8.GetBytes(ok), "ping", out bool pass, out _) || !pass ||
                !TryValidateResponse(Response(false, "DENIED"), "ping", out bool denied, out _) || denied ||
                TryValidateResponse("{\"version\":1}"u8.ToArray(), "ping", out _, out _) ||
                TryValidateResponse(Response(true, "unrecognized"), "ping", out _, out _) ||
                TryValidateResponse(Response(true, "pong"), "status", out _, out _) ||
                TryValidateResponse(Response(true, "development_only"), "capabilities", out _, out _) ||
                TryValidateResponse(Response(true, "pong"), "malformed", out _, out _) ||
                !TryValidateResponse(Response(true, "development_only"), "status", out bool statusOk, out _) || !statusOk ||
                !TryValidateResponse(Response(true, "ping,status,capabilities"), "capabilities", out bool capsOk, out _) || !capsOk) return 1;
            SidCheck matching = CompareSids("S-1-5-21-synthetic", "s-1-5-21-SYNTHETIC");
            SidCheck mismatch = CompareSids("S-1-5-21-a", "S-1-5-21-b");
            SidCheck missingPeer = CompareSids("S-1-5-21-a", null);
            SidCheck missingExpected = CompareSids(null, "S-1-5-21-a");
            SidCheck tokenFailure = EvaluateCapturedSid("S-1-5-21-a", () => throw new InvalidOperationException(), () => true);
            SidCheck cleanupFailure = EvaluateCapturedSid("S-1-5-21-a", () => "S-1-5-21-a", () => throw new InvalidOperationException());
            if (!matching.Allowed || mismatch.Allowed || missingPeer.Allowed || missingExpected.Allowed ||
                tokenFailure.Allowed || cleanupFailure.Allowed || mismatch.Reason != "SID_MISMATCH" ||
                missingPeer.Reason != "PEER_SID_UNAVAILABLE" || missingExpected.Reason != "EXPECTED_SID_UNAVAILABLE" ||
                tokenFailure.Reason != "TOKEN_ACQUISITION_FAILURE" || cleanupFailure.Reason != "IMPERSONATION_CLEANUP_FAILURE" ||
                SidDiagnostic(matching) != "peer_sid_available=true expected_sid_available=true peer_sid_matches=true cleanup_verified=true reason=SID_MATCH") return 1;
            Console.WriteLine("Offline protocol selftest passed."); return 0;
        }
        if (!OperatingSystem.IsWindows() || args.Length != 3 || !PipeName.IsMatch(args[1])) return 2;
        try {
            if (args[0] == "serve" && int.TryParse(args[2], out int seconds) && seconds is >= 1 and <= 60) return await Serve(args[1], seconds);
            if (args[0] == "client") return await Client(args[1], args[2]);
            return 2;
        }
        catch (Exception) { Console.Error.WriteLine("Development IPC unavailable."); return 1; }
    }
}
