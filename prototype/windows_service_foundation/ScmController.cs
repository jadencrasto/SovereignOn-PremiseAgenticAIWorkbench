internal interface IScmWorker : IAsyncDisposable
{
    Task Initialize(CancellationToken token);
    Task Run(CancellationToken token);
    Task Cleanup(CancellationToken token);
}

internal enum ServiceError : uint
{
    None = 0, InvalidConfiguration = 1, Registration = 2, StartStatus = 3, RunningStatus = 4,
    StopStatus = 5, FinalStatus = 6, Initialization = 7, Worker = 8, Cancellation = 9,
    Cleanup = 10, Disposal = 11, Callback = 12, InitializationDeadline = 13,
    LifetimeDeadline = 14, ShutdownDeadline = 15
}

internal readonly record struct ScmResult(ServiceError Error, uint Acknowledged, bool FinalAttempted,
    ServiceError FinalReportedError, Task? PendingShutdown)
{
    public int ExitCode => Error == ServiceError.None && Acknowledged == ScmController.Stopped ? 0 : 1;
}

// One lifecycle owns the worker and publishes every status. The callback only signals.
internal sealed class ScmController(
    Func<bool> register,
    Func<uint, ServiceError, bool> publish,
    Func<IScmWorker> createWorker,
    TimeSpan deadline,
    TimeSpan lifetime,
    TimeProvider? timeProvider = null,
    Action? signalHook = null)
{
    internal const uint Stopped = 1, StartPending = 2, StopPending = 3, Running = 4;
    internal const uint Stop = 1, Shutdown = 5, Interrogate = 4;
    const int StopRequested = 1, CallbackError = 2, ControlsClosed = 4;
    readonly TaskCompletionSource stop = new(TaskCreationOptions.RunContinuationsAsynchronously);
    readonly TimeProvider clock = timeProvider ?? TimeProvider.System;
    int controls, started;
    uint acknowledged;
    bool finalAttempted;
    ServiceError finalReportedError;

    public void CallbackFailed()
    {
        Interlocked.Or(ref controls, StopRequested | CallbackError);
        stop.TrySetResult();
    }

    public uint Control(uint control)
    {
        if (control == Interrogate) return 0;
        if (control is not (Stop or Shutdown)) return 120;
        try
        {
            int previous;
            do
            {
                previous = Volatile.Read(ref controls);
                if ((previous & ControlsClosed) != 0) return 1061;
                if ((previous & StopRequested) != 0) return 0;
            } while (Interlocked.CompareExchange(ref controls, previous | StopRequested, previous) != previous);
            signalHook?.Invoke();
            stop.TrySetResult();
            return 0;
        }
        catch
        {
            CallbackFailed();
            return 1064;
        }
    }

    bool Publish(uint state, ServiceError error = ServiceError.None)
    {
        if (finalAttempted) return false;
        if (state == Stopped)
        {
            finalAttempted = true;
            finalReportedError = error;
        }
        try
        {
            if (!publish(state, error)) return false;
            acknowledged = state;
            return true;
        }
        catch { return false; }
    }

    async Task<Task> Race(Task operation, TimeSpan limit)
    {
        using var timer = new CancellationTokenSource();
        Task winner = await Task.WhenAny(operation, stop.Task, Task.Delay(limit, clock, timer.Token));
        await timer.CancelAsync();
        return winner;
    }

    public async Task<ScmResult> Run(bool configValid)
    {
        if (Interlocked.Exchange(ref started, 1) != 0)
            return new(ServiceError.InvalidConfiguration, 0, false, ServiceError.None, null);
        try
        {
            if (!register()) return new(ServiceError.Registration, 0, false, ServiceError.None, null);
        }
        catch { return new(ServiceError.Registration, 0, false, ServiceError.None, null); }

        IScmWorker? worker = null;
        var cancellation = new CancellationTokenSource();
        Task? active = null;
        ServiceError error = ServiceError.None, operationError = ServiceError.Initialization;
        try
        {
            if (!Publish(StartPending)) error = ServiceError.StartStatus;
            else if (!configValid) error = ServiceError.InvalidConfiguration;
            else if (!stop.Task.IsCompleted)
            {
                worker = createWorker();
                active = Task.Run(() => worker.Initialize(cancellation.Token));
                Task winner = await Race(active, deadline);
                if (winner == active) await active;
                else if (winner != stop.Task) error = ServiceError.InitializationDeadline;
                if (winner == active && !stop.Task.IsCompleted)
                {
                    active = null;
                    if (!Publish(Running)) error = ServiceError.RunningStatus;
                    else if (!stop.Task.IsCompleted)
                    {
                        operationError = ServiceError.Worker;
                        active = Task.Run(() => worker.Run(cancellation.Token));
                        winner = await Race(active, lifetime);
                        if (winner == active) error = ServiceError.Worker;
                        else if (winner != stop.Task) error = ServiceError.LifetimeDeadline;
                    }
                }
            }
        }
        catch { error = operationError; }

        if (acknowledged == Running && !Publish(StopPending) && error == ServiceError.None)
            error = ServiceError.StopStatus;
        // This task retains ownership even if the controller's wait expires. Cleanup
        // and disposal cannot run concurrently with initialization or active work.
        Task<ServiceError> shutdown = Task.Run(() => ShutdownWorker(worker, active, cancellation, operationError));
        ServiceError shutdownError;
        try { shutdownError = await shutdown.WaitAsync(deadline, clock); }
        catch (TimeoutException)
        {
            Interlocked.Or(ref controls, ControlsClosed);
            return new(ServiceError.ShutdownDeadline, acknowledged, false, ServiceError.None, shutdown);
        }

        int finalControls = Interlocked.Or(ref controls, ControlsClosed);
        if (error == ServiceError.None) error = shutdownError;
        if (error == ServiceError.None && (finalControls & CallbackError) != 0) error = ServiceError.Callback;
        if (!Publish(Stopped, error) && error == ServiceError.None) error = ServiceError.FinalStatus;
        return new(error, acknowledged, finalAttempted, finalReportedError, null);
    }

    async Task<ServiceError> ShutdownWorker(IScmWorker? worker, Task? active,
        CancellationTokenSource cancellation, ServiceError operationError)
    {
        using (cancellation)
        {
            ServiceError error = ServiceError.None;
            try { await cancellation.CancelAsync(); } catch { error = ServiceError.Cancellation; }
            if (active is not null)
            {
                try { await active; }
                catch (OperationCanceledException ex) when (ex.CancellationToken == cancellation.Token && cancellation.IsCancellationRequested) { }
                catch { if (error == ServiceError.None) error = operationError; }
            }
            if (worker is null) return error;
            using var cleanupDeadline = new CancellationTokenSource(deadline, clock);
            try { await worker.Cleanup(cleanupDeadline.Token); }
            catch { if (error == ServiceError.None) error = ServiceError.Cleanup; }
            try { await worker.DisposeAsync(); }
            catch { if (error == ServiceError.None) error = ServiceError.Disposal; }
            return error;
        }
    }
}
