import React from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { Cpu, CheckCircle2, Zap, RefreshCw, Eye, MessageSquare, Database, ArrowRight } from 'lucide-react';
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
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-white p-6">
      <div className="max-w-5xl mx-auto w-full space-y-6">
        {/* Header */}
        <div className="flex items-start justify-between">
          <div>
            <h1 className="text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
              <Cpu className="w-5 h-5 text-blue-600" />
              Local Model Providers & Architecture
            </h1>
            <p className="text-xs text-slate-500 mt-1 max-w-xl">
              All inference executes on local compute through provider-agnostic abstractions. External cloud APIs can be toggled in configuration without architectural redesign.
            </p>
          </div>

          <button
            onClick={() => refreshModels()}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg border border-slate-200 bg-white hover:bg-slate-50 shadow-sm text-xs font-mono text-slate-600 transition-colors"
          >
            <RefreshCw className="w-3.5 h-3.5" />
            <span>Scan Models</span>
          </button>
        </div>

        {/* Runtime Status Banner */}
        <div className="p-4 rounded-xl border border-slate-200 bg-slate-50 flex items-center justify-between shadow-sm">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-lg bg-blue-50 border border-blue-200 flex items-center justify-center text-blue-600">
              <Zap className="w-5 h-5" />
            </div>
            <div>
              <div className="text-sm font-semibold text-slate-800">Local Ollama Runtime</div>
              <div className="text-xs text-slate-500 font-mono">Endpoint: http://localhost:11434</div>
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
                    ? 'border-blue-500 bg-blue-50/50 shadow-md shadow-blue-500/10'
                    : 'border-slate-200 bg-white hover:border-slate-300 hover:bg-slate-50 shadow-sm'
                }`}
              >
                <div className="space-y-3">
                  {/* Top tags */}
                  <div className="flex items-center justify-between">
                    <Badge variant={model.badgeVariant}>{model.badge}</Badge>
                    {model.isDefault && (
                      <span className="text-[10px] font-mono text-blue-600 uppercase tracking-wide">
                        Default
                      </span>
                    )}
                  </div>

                  {/* Title */}
                  <div>
                    <h3 className="text-base font-bold text-slate-900 font-mono">{model.name}</h3>
                    <p className="text-xs text-slate-500 mt-0.5">{model.provider}</p>
                  </div>

                  {/* Description */}
                  <p className="text-xs text-slate-600 leading-relaxed">{model.description}</p>

                  {/* Specs */}
                  <div className="pt-2 border-t border-slate-100 grid grid-cols-2 gap-2 text-[11px] font-mono text-slate-500">
                    <div>
                      <span className="text-slate-400">Footprint:</span> {model.size}
                    </div>
                    <div>
                      <span className="text-slate-400">Context:</span> {model.context}
                    </div>
                  </div>
                </div>

                {/* Footer Action */}
                <div className="mt-5 pt-3 border-t border-slate-100">
                  {isEmbedding ? (
                    <div className="text-[11px] font-mono text-indigo-500 flex items-center gap-1">
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      <span>Dedicated RAG Embedder</span>
                    </div>
                  ) : isSelected ? (
                    <div className="w-full py-1.5 px-3 rounded-lg bg-blue-50 border border-blue-200 text-blue-700 text-xs font-mono text-center font-medium flex items-center justify-center gap-1.5">
                      <CheckCircle2 className="w-3.5 h-3.5" />
                      <span>Active Chat Model</span>
                    </div>
                  ) : (
                    <button
                      onClick={() => handleSelectModel(model.id)}
                      className="w-full py-1.5 px-3 rounded-lg bg-slate-100 hover:bg-slate-200 text-slate-700 text-xs font-mono transition-colors cursor-pointer"
                    >
                      Select for Inference
                    </button>
                  )}
                </div>
              </div>
            );
          })}
        </div>
        {/* Phase 5: Capability Routing Section */}
        <div className="rounded-xl border border-slate-200 bg-white shadow-sm p-5 space-y-4">
          <div className="flex items-center gap-2 pb-3 border-b border-slate-100">
            <Cpu className="w-4 h-4 text-blue-600" />
            <h2 className="text-sm font-semibold text-slate-800">Active Capability Routing</h2>
          </div>
          <div className="space-y-2.5">
            {[
              {
                capability: 'Chat / Reasoning',
                model: 'qwen2.5:7b',
                route: 'ollama/qwen2.5:7b',
                icon: MessageSquare,
                color: 'text-blue-600',
                desc: 'Text instructions, tool loop, RAG synthesis',
              },
              {
                capability: 'Vision (Image Analysis)',
                model: 'llava:7b',
                route: 'ollama/llava:7b',
                icon: Eye,
                color: 'text-blue-600',
                desc: 'Local multimodal image understanding — Step 1 of 2-step pipeline',
              },
              {
                capability: 'Embeddings (RAG)',
                model: 'nomic-embed-text',
                route: 'ollama/nomic-embed-text',
                icon: Database,
                color: 'text-indigo-600',
                desc: 'Dense text embeddings for ChromaDB vector search',
              },
            ].map(({ capability, model, icon: Icon, color, desc }) => (
              <div
                key={capability}
                className="flex items-center gap-4 p-3 rounded-lg bg-slate-50 border border-slate-200"
              >
                <div className={`w-8 h-8 rounded-lg bg-white border border-slate-200 flex items-center justify-center shrink-0 ${color}`}>
                  <Icon className="w-4 h-4" />
                </div>
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-slate-700">{capability}</span>
                    <ArrowRight className="w-3 h-3 text-slate-400" />
                    <span className={`text-xs font-mono font-bold ${color}`}>{model}</span>
                  </div>
                  <p className="text-[11px] text-slate-500 mt-0.5">{desc}</p>
                </div>
              </div>
            ))}
          </div>
          <p className="text-[11px] text-slate-600 font-mono">
            Two-step vision architecture: llava:7b generates visual observation → qwen2.5:7b reasons + uses tools
          </p>
        </div>
      </div>
    </div>
  );
};
