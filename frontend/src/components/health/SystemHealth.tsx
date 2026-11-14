import React from 'react';
import { Activity, RefreshCw } from 'lucide-react';
import { capabilityFor, capabilityLabels, describeCapability, type CapabilityName } from '../../api/capabilities';
import { useWorkbench } from '../../context/WorkbenchContext';

export const SystemHealth: React.FC = () => {
  const { capabilities, isCheckingHealth, refreshCapabilities } = useWorkbench();
  const backend = capabilityFor(capabilities, 'backend', !isCheckingHealth);

  return (
    <div className="flex-1 overflow-auto bg-white p-6 text-slate-900 space-y-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold">
            <Activity className="w-5 h-5 text-blue-600" />
            Capability status
          </h1>
          <p className="text-sm text-slate-600 mt-1">
            Environment {capabilities?.environment ?? 'unknown'}. Deployment verification pending.
          </p>
        </div>
        <div className="flex gap-2">
          <button className="px-3 py-1.5 border rounded-lg text-sm" disabled={isCheckingHealth}
            onClick={() => void refreshCapabilities()}>
            <RefreshCw className="inline w-3 h-3 mr-1" /> Refresh status
          </button>
          <button className="px-3 py-1.5 border rounded-lg text-sm disabled:opacity-50"
            disabled={isCheckingHealth || backend.state !== 'available' ||
              capabilityFor(capabilities, 'ollama').state === 'blocked'}
            onClick={() => void refreshCapabilities(true)}>
            Check Ollama
          </button>
        </div>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3" aria-live="polite">
        {(Object.entries(capabilityLabels) as [CapabilityName, string][]).map(([key, label]) => {
          const capability = capabilityFor(capabilities, key, !isCheckingHealth);
          return <div className="border border-slate-200 rounded-lg p-3" key={key}>
            <h2 className="text-sm font-semibold">{label}</h2>
            <div className="text-xs font-semibold uppercase text-blue-700 mt-1">{capability.state}</div>
            <p className="text-xs text-slate-600 mt-1">{describeCapability(capability)}</p>
          </div>;
        })}
      </div>
      {capabilities && <section>
        <h2 className="text-sm font-semibold">Configured models</h2>
        <dl className="mt-2 text-sm space-y-1">
          {Object.entries(capabilities.models).map(([role, model]) => <div key={role}>
            <dt className="inline font-medium">{role.replaceAll('_', ' ')} </dt>
            <dd className="inline">{model.id ?? 'Unconfigured'} · {model.state} · {describeCapability(model)}</dd>
          </div>)}
        </dl>
      </section>}
    </div>
  );
};
