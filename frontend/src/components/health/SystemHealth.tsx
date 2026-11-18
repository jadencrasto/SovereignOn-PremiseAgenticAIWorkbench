import React from 'react';
import { Activity, RefreshCw } from 'lucide-react';
import { capabilityFor, capabilityLabels, describeCapability, type CapabilityName } from '../../api/capabilities';
import { useWorkbench } from '../../context/WorkbenchContext';

export const SystemHealth: React.FC = () => {
  const { capabilities, isCheckingHealth, refreshCapabilities } = useWorkbench();
  const backend = capabilityFor(capabilities, 'backend', !isCheckingHealth);
  const documents = capabilities?.document_intelligence;

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
      <section aria-labelledby="document-capability-heading">
        <h2 id="document-capability-heading" className="text-sm font-semibold">Document workflows</h2>
        <dl className="mt-2 space-y-2 text-sm">
          <div>
            <dt className="font-medium">TXT, CSV and JSON attachments</dt>
            <dd className="text-slate-600">{capabilities?.text_file_understanding === true
              ? 'Request-scoped analysis is enabled for authenticated local chat with a ready model.'
              : capabilities?.text_file_understanding === false ? 'Unavailable in the current chat configuration.' : 'Status unknown.'}</dd>
          </div>
          <div>
            <dt className="font-medium">PDF, DOCX and XLSX</dt>
            <dd className="text-slate-600">{documents
              ? 'Extraction, source-grounded reasoning and agent planning verified offline. Direct document chat is blocked.'
              : 'Offline document verification status unknown.'}</dd>
          </div>
          {documents && <>
            <div><dt className="font-medium">Protected artifact publication</dt><dd className="text-slate-600">Blocked.</dd></div>
            <div><dt className="font-medium">External browser integration</dt><dd className="text-slate-600">Unverified.</dd></div>
          </>}
        </dl>
      </section>
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
