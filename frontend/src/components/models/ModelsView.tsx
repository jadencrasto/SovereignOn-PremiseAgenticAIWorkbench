import React from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { Cpu, CheckCircle2, Zap, RefreshCw } from 'lucide-react';
import { Badge } from '../common/Badge';

export const ModelsView: React.FC = () => {
  const {
    availableModels,
    defaultModel,
    selectedModel,
    setSelectedModel,
    refreshModels,
    isBackendConnected,
    addToast,
  } = useWorkbench();

  const handleSelectModel = (modelId: string) => {
    setSelectedModel(modelId);
    addToast('success', `Active model set to ${modelId}`);
  };

  const modelsCatalog = [
    {
      id: 'ollama/qwen2.5:7b',
      name: 'qwen2.5:7b',
      provider: 'Ollama (Local)',
      type: 'Chat / Instruction / Reasoning',
      badge: 'Chat',
      badgeVariant: 'emerald' as const,
      size: '4.7 GB',
      context: '32k tokens',
      description:
        'General-purpose local reasoning model. Excellent multilingual and structured instruction following.',
      installed: availableModels.includes('ollama/qwen2.5:7b'),
      isDefault: defaultModel === 'ollama/qwen2.5:7b',
    },
    {
      id: 'ollama/llava:7b',
      name: 'llava:7b',
      provider: 'Ollama (Local)',
      type: 'Multimodal Vision + Text',
      badge: 'Vision',
      badgeVariant: 'blue' as const,
      size: '4.7 GB',
      context: '4k tokens',
      description:
        'Multimodal visual question answering model for image and chart reasoning.',
      installed: availableModels.includes('ollama/llava:7b'),
      isDefault: defaultModel === 'ollama/llava:7b',
    },
    {
      id: 'ollama/nomic-embed-text',
      name: 'nomic-embed-text',
      provider: 'Ollama (Local)',
      type: 'Dense Text Embeddings',
      badge: 'Embeddings',
      badgeVariant: 'purple' as const,
      size: '274 MB',
      context: '8k tokens',
      description:
        'High-dimensional vector embedding model powering the local ChromaDB RAG pipeline.',
      installed: true,
      isDefault: false,
    },
  ];

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-[#090d16] p-6">
      <div className="max-w-5xl mx-auto w-full space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
              <Cpu className="w-5 h-5 text-blue-400" />
              Local Model Providers & Architecture
            </h1>
            <p className="text-xs text-slate-400 mt-1 max-w-xl">
              All inference executes on local compute through provider-agnostic abstractions. External cloud APIs can be toggled in configuration without architectural redesign.
            </p>
          </div>

          <button
            onClick={() => refreshModels()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-700 bg-slate-900 hover:bg-slate-800 text-xs font-mono text-slate-300 transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Scan Models</span>
          </button>
        </div>

        {/* Runtime Status Banner */}
        <div className="p-4 rounded-xl border border-slate-800 bg-[#0d1424]/70 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-emerald-950/80 border border-emerald-500/40 flex items-center justify-center text-emerald-400">
              <Zap className="w-5 h-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-white">Local Ollama Runtime</div>
              <div className="text-xs text-slate-400 font-mono">Endpoint: http://localhost:11434</div>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Badge variant={isBackendConnected ? 'emerald' : 'rose'}>
              {isBackendConnected ? 'Connected & Verified' : 'Unreachable'}
            </Badge>
          </div>
        </div>

        {/* Model Cards Grid */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          {modelsCatalog.map((model) => {
            const isSelected = selectedModel === model.id;
            const isEmbedding = model.badge === 'Embeddings';

            return (
              <div
                key={model.id}
                className={`p-5 rounded-xl border flex flex-col justify-between transition-all ${
                  isSelected
                    ? 'border-emerald-500/80 bg-[#0d1726] shadow-lg shadow-emerald-950/40'
                    : 'border-slate-800 bg-[#0d1424]/50 hover:border-slate-700 hover:bg-[#0d1424]'
                }`}
              >
                <div className="space-y-3">
                  {/* Top tags */}
                  <div className="flex items-center justify-between">
                    <Badge variant={model.badgeVariant}>{model.badge}</Badge>
                    {model.isDefault && (
                      <span className="text-[10px] font-mono text-emerald-400 uppercase tracking-wide">
                        Default
                      </span>
                    )}
                  </div>

                  {/* Title */}
                  <div>
                    <h3 className="text-base font-bold text-white font-mono">{model.name}</h3>
                    <p className="text-xs text-slate-400 mt-0.5">{model.provider}</p>
                  </div>

                  {/* Description */}
                  <p className="text-xs text-slate-300 leading-relaxed">{model.description}</p>

                  {/* Specs */}
                  <div className="pt-2 border-t border-slate-800/80 grid grid-cols-2 gap-2 text-[11px] font-mono text-slate-400">
                    <div>
                      <span className="text-slate-500">Footprint:</span> {model.size}
                    </div>
                    <div>
                      <span className="text-slate-500">Context:</span> {model.context}
                    </div>
                  </div>
                </div>

                {/* Footer Action */}
                <div className="mt-5 pt-3 border-t border-slate-800/80">
                  {isEmbedding ? (
                    <div className="text-[11px] font-mono text-purple-400 flex items-center gap-1">
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      <span>Dedicated RAG Embedder</span>
                    </div>
                  ) : isSelected ? (
                    <div className="w-full py-1.5 px-3 rounded-lg bg-emerald-950/80 border border-emerald-500/50 text-emerald-400 text-xs font-mono text-center font-medium flex items-center justify-center gap-1.5">
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      <span>Active Chat Model</span>
                    </div>
                  ) : (
                    <button
                      onClick={() => handleSelectModel(model.id)}
                      className="w-full py-1.5 px-3 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-200 text-xs font-mono transition-colors cursor-pointer"
                    >
                      Select for Inference
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      </div>
    </div>
  );
};
