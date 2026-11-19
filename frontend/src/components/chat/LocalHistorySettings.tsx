import { useEffect, useState } from 'react';
import { apiRequest } from '../../api/client';

type HistoryMode = 'disposable' | 'durable';
interface RetentionStatus {
  mode: HistoryMode;
  next_mode: HistoryMode;
  launch_override: boolean;
}

const endpoint = '/api/chat/local-retention';
const modeLabel: Record<HistoryMode, string> = {
  disposable: 'Current run only',
  durable: 'Saved across restarts',
};

export function LocalHistorySettings({ currentMode }: { currentMode?: HistoryMode }) {
  const [status, setStatus] = useState<RetentionStatus>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [refresh, setRefresh] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setError('');
    void apiRequest<RetentionStatus>(endpoint, { signal: controller.signal }).then(result => {
      if (!controller.signal.aborted) setStatus(result);
    }).catch(() => {
      if (!controller.signal.aborted) setError('History settings are unavailable.');
    });
    return () => controller.abort();
  }, [refresh]);

  const change = async (mode: HistoryMode) => {
    if (!status || status.launch_override || busy || mode === status.next_mode) return;
    const confirm = mode === 'durable'
      ? 'Save new conversations across restarts starting with the next run? Current disposable conversations will expire.'
      : 'Start the next run with disposable history? Previously saved conversations remain on this computer.';
    if (!window.confirm(confirm)) return;
    setBusy(true);
    setError('');
    try {
      setStatus(await apiRequest<RetentionStatus>(endpoint, { method: 'PUT', body: JSON.stringify({ mode }) }));
    } catch {
      setError('The server could not confirm the history change.');
    } finally {
      setBusy(false);
    }
  };

  const active = status?.mode ?? currentMode;
  return (
    <section aria-label="Local history retention" className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-700">
      <span>History mode · {active ? modeLabel[active] : 'Checking'}</span>
      {status?.launch_override ? <span>Controlled by the launch setting</span> : status && (
        <label className="flex items-center gap-2">
          After restart
          <select className="rounded border border-slate-200 bg-white px-2 py-1"
            value={status.next_mode} disabled={busy}
            onChange={event => void change(event.currentTarget.value as HistoryMode)}>
            <option value="disposable">{modeLabel.disposable}</option>
            <option value="durable">{modeLabel.durable}</option>
          </select>
        </label>
      )}
      {status && status.next_mode !== status.mode && <span role="status">Restart to apply the saved choice.</span>}
      {error && <>
        <span role="alert">{error}</span>
        <button type="button" className="underline" onClick={() => setRefresh(value => value + 1)}>Refresh settings</button>
      </>}
    </section>
  );
}
