using System.Diagnostics;
using System.IO.Pipes;
using System.Runtime.InteropServices;
using System.Security.Principal;
using Microsoft.Win32.SafeHandles;

internal static partial class Program
{
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool GetNamedPipeServerProcessId(SafePipeHandle pipe, out uint serverPid);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern uint GetProcessId(SafeProcessHandle process);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern uint WaitForSingleObject(SafeProcessHandle process, uint milliseconds);

    // The delegates make the very same decision logic testable without native IPC.
    static bool ObserveExpected(Func<bool> alive, Func<uint> expected, Func<uint> observed)
    {
        try {
            if (!alive()) return false;
            uint a = expected(), b = observed();
            return a != 0 && a == b && alive() && observed() == a && alive();
        } catch (Exception) { return false; }
    }

    static int LauncherSelfTest()
    {
        int calls = 0;
        if (!ObserveExpected(() => true, () => 42, () => 42) ||
            ObserveExpected(() => true, () => 42, () => 43) ||
            ObserveExpected(() => false, () => 42, () => 42) ||
            ObserveExpected(() => ++calls < 2, () => 42, () => 42) ||
            ObserveExpected(() => true, () => throw new Exception(), () => 42) ||
            ObserveExpected(() => true, () => 0, () => 0)) return 1;
        int observations = 0;
        if (ObserveExpected(() => true, () => 42, () => ++observations == 1 ? 42U : 43U)) return 1;
        Console.WriteLine("Launcher observation selftest passed; authentication unsupported."); return 0;
    }

    // Explicit manual development invocation. No paths or commands are accepted from callers.
    static async Task<int> InspectLaunch()
    {
        string executable = Path.Combine(AppContext.BaseDirectory, "Broker.exe");
        if (!File.Exists(executable)) { Console.Error.WriteLine("LAUNCH category=broker_unavailable"); return 1; }
        string name = "SovereignAI-dev-" + Guid.NewGuid().ToString("N");
        using var child = new Process { StartInfo = new ProcessStartInfo {
            FileName = executable, UseShellExecute = false, CreateNoWindow = true,
            RedirectStandardOutput = true, RedirectStandardError = true,
            ArgumentList = { "serve", name, "10" }
        } };
        try {
            if (!child.Start()) return 1;
            // Retain a process object handle before waiting or connecting.
            SafeProcessHandle expectedHandle = child.SafeHandle;
            Task<string> errors = child.StandardError.ReadToEndAsync();
            Task<string?> ready = child.StandardOutput.ReadLineAsync();
            if (await Task.WhenAny(ready, Task.Delay(5000)) != ready || await ready != "READY") return 1;
            using var pipe = new NamedPipeClientStream(".", name, (PipeAccessRights)ClientRights,
                PipeOptions.Asynchronous, TokenImpersonationLevel.Identification, HandleInheritability.None);
            using var timeout = new CancellationTokenSource(3000);
            await pipe.ConnectAsync(timeout.Token);
            bool match = ObserveExpected(
                () => WaitForSingleObject(expectedHandle, 0) == 0x102,
                () => GetProcessId(expectedHandle),
                () => GetNamedPipeServerProcessId(pipe.SafePipeHandle, out uint pid) ? pid : 0);
            Console.WriteLine(match ? "LAUNCH observation=child_process_match authentication=unsupported" : "LAUNCH observation=rejected authentication=unsupported");
            return match ? 0 : 1;
        }
        catch (Exception) { Console.Error.WriteLine("LAUNCH category=inspection_failed authentication=unsupported"); return 1; }
        finally {
            try { if (!child.HasExited) { child.Kill(); await child.WaitForExitAsync().WaitAsync(TimeSpan.FromSeconds(5)); } } catch (Exception) { Console.Error.WriteLine("LAUNCH category=cleanup_failed"); }
        }
    }
}
