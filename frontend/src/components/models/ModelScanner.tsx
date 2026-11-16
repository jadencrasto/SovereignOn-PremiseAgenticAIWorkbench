import React from 'react';
import { Cpu } from 'lucide-react';
import { capabilityFor, describeCapability } from '../../api/capabilities';
import { useWorkbench } from '../../context/WorkbenchContext';

export const ModelScanner: React.FC = () => {
  const { capabilities, availableModels, defaultModel, isBackendConnected, isCheckingHealth,
    refreshModels, selectedModel, setSelectedModel, checkSelectedModel,
    selectedVisionModel, setSelectedVisionModel, checkSelectedVision } = useWorkbench();
  const ollama = capabilityFor(capabilities, 'ollama');
  const visionModels = capabilities?.vision_models ?? [];
  const visionStatus = capabilityFor(capabilities, 'multimodal');
  const modelKey = (id: string) => {
    const name = id.startsWith('ollama/') ? id.slice('ollama/'.length) : id;
    return name.lastIndexOf(':') > name.lastIndexOf('/') ? name : `${name}:latest`;
  };

  return (
    <div className="flex-1 overflow-y-auto bg-slate-50 p-6 space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border bg-white p-5">
        <div>
          <h1 className="flex items-center gap-2 text-xl font-bold">
            <Cpu className="w-5 h-5 text-blue-600" /> Local model inventory
          </h1>
          <p className="text-sm text-slate-600 mt-1">{describeCapability(ollama)}</p>
          <p className="text-sm text-slate-600 mt-1">Configured default {defaultModel || 'unknown'}</p>
        </div>
        <button className="rounded-lg bg-blue-600 text-white px-4 py-2 text-sm disabled:opacity-50"
          onClick={() => void refreshModels()}
          disabled={!isBackendConnected || isCheckingHealth || ollama.state === 'blocked'}>
          Check Ollama
        </button>
      </div>
      <section className="rounded-xl border bg-white p-5 space-y-3">
        <label htmlFor="vision-model" className="block font-semibold">Image understanding model</label>
        <p className="text-sm text-slate-600">{describeCapability(visionStatus)}</p>
        <div className="flex flex-wrap gap-2">
          <select id="vision-model" className="border rounded px-2 py-1 text-sm"
            value={visionModels.includes(selectedVisionModel) ? selectedVisionModel : ''}
            disabled={!visionModels.length || isCheckingHealth}
            onChange={event => setSelectedVisionModel(event.target.value)}>
            <option value="" disabled>Choose an installed vision model</option>
            {visionModels.map(model => <option key={model} value={model}>{model}</option>)}
          </select>
          <button className="border rounded px-2 py-1 text-sm disabled:opacity-50"
            disabled={isCheckingHealth || !visionModels.includes(selectedVisionModel)}
            onClick={() => void checkSelectedVision()}>Check vision inference</button>
        </div>
      </section>
      {ollama.state === 'available' && <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {availableModels.length === 0 && <p className="text-sm text-slate-600">No installed model is configured for chat.</p>}
        {availableModels.map(model => <div className="p-4 rounded-xl border bg-white space-y-2" key={model}>
          <h2 className="font-semibold break-all">{model}</h2>
          <p className="text-xs text-slate-600">{modelKey(selectedModel) === modelKey(model) && capabilities?.selected_chat_model?.id === selectedModel
            ? describeCapability(capabilities.selected_chat_model) : 'Installed and configured for chat; inference not checked.'}</p>
          {modelKey(selectedModel) === modelKey(model) && capabilities?.selected_chat_model?.state === 'available' &&
            capabilities.selected_chat_model.identity_evidence === 'unavailable' &&
            <p className="text-xs text-slate-600">No model digest was observed; weight changes may go undetected.</p>}
          <button className="border rounded px-2 py-1 text-sm disabled:opacity-50"
            disabled={modelKey(selectedModel) === modelKey(model)}
            onClick={() => setSelectedModel(model)}>
            {modelKey(selectedModel) === modelKey(model) ? 'Selected for chat' : 'Select for chat'}
          </button>
          {modelKey(selectedModel) === modelKey(model) && <button className="border rounded px-2 py-1 text-sm disabled:opacity-50"
            disabled={isCheckingHealth} onClick={() => void checkSelectedModel()}>Check chat inference</button>}
        </div>)}
      </div>}
    </div>
  );
};
