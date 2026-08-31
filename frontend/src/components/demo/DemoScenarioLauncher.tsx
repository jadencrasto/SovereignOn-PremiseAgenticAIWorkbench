/**
 * frontend/src/components/demo/DemoScenarioLauncher.tsx
 * ------------------------------------------------------
 * Precision Industrial Procedures (White & Light Blue Style)
 */

import React, { useState, useEffect } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import {
  FileSpreadsheet,
  Eye,
  AlertTriangle,
  Play,
  CheckCircle2,
  ArrowRight,
  ShieldCheck,
  Cpu,
  Layers,
  FileCheck,
  Activity,
  Zap,
} from 'lucide-react';

interface DemoScenario {
  id: string;
  title: string;
  category: string;
  unit: string;
  badge: string;
  description: string;
  prompt: string;
  dataset_file?: string;
  image_file?: string;
  benchmark_doc: string;
  expected_artifact: string;
  is_multimodal: boolean;
}

export const DemoScenarioLauncher: React.FC = () => {
  const [scenarios, setScenarios] = useState<DemoScenario[]>([]);
  const [loading, setLoading] = useState<boolean>(true);
  const [selectedScenario, setSelectedScenario] = useState<DemoScenario | null>(null);

  const { setActiveTab, addToast } = useWorkbench();

  useEffect(() => {
    fetch('/api/demo/scenarios')
      .then((res) => res.json())
      .then((data) => {
        if (data.scenarios) {
          setScenarios(data.scenarios);
          if (data.scenarios.length > 0) {
            setSelectedScenario(data.scenarios[0]);
          }
        }
      })
      .catch((err) => console.error('Failed to load demo scenarios:', err))
      .finally(() => setLoading(false));
  }, []);

  const handleLaunchScenario = (sc: DemoScenario) => {
    addToast('info', `Initializing workflow: ${sc.title}`);
    setActiveTab('chat');
    window.dispatchEvent(
      new CustomEvent('workbench:preload-demo', {
        detail: {
          prompt: sc.prompt,
          imageFile: sc.image_file,
          isMultimodal: sc.is_multimodal,
        },
      })
    );
  };

  const getScenarioTheme = (id: string) => {
    switch (id) {
      case 'industrial_diligence':
        return {
          code: 'PROC-01',
          tag: 'HYDROCARBON QA',
          badgeBg: 'bg-[#e0f2fe] text-[#0369a1] border-[#bae6fd]',
          btnBg: 'bg-[#0284c7] hover:bg-[#0369a1] text-white',
          borderHover: 'hover:border-[#0284c7]',
          shadow: 'brutal-shadow-blue',
        };
      case 'equipment_diagnostics':
        return {
          code: 'PROC-02',
          tag: 'MECHANICAL NDT',
          badgeBg: 'bg-[#dbeafe] text-[#1d4ed8] border-[#bfdbfe]',
          btnBg: 'bg-[#2563eb] hover:bg-[#1d4ed8] text-white',
          borderHover: 'hover:border-[#2563eb]',
          shadow: 'brutal-shadow-sky',
        };
      case 'incident_runbook':
        return {
          code: 'PROC-03',
          tag: 'PROCESS INTERLOCK',
          badgeBg: 'bg-[#fef3c7] text-[#92400e] border-[#fde68a]',
          btnBg: 'bg-[#d97706] hover:bg-[#b45309] text-white',
          borderHover: 'hover:border-[#d97706]',
          shadow: 'brutal-shadow-yellow',
        };
      default:
        return {
          code: 'PROC-00',
          tag: 'STANDARD',
          badgeBg: 'bg-[#f1f5f9] text-[#334155] border-[#cbd5e1]',
          btnBg: 'bg-[#334155] hover:bg-[#1e293b] text-white',
          borderHover: 'hover:border-[#334155]',
          shadow: 'brutal-shadow-dark',
        };
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-[#f0f7ff] text-[#0f172a] p-8 space-y-6 font-mono">
      {/* Station Header Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-6 bg-white border-2 border-[#cbd5e1] brutal-shadow-blue">
        <div>
          <div className="flex items-center gap-3">
            <span className="w-3 h-3 bg-[#0284c7] inline-block" />
            <h1 className="text-xl font-black font-display tracking-tight text-[#0f172a] uppercase">
              Industrial Verification Procedures
            </h1>
            <span className="text-[10px] font-bold px-2 py-0.5 bg-[#e0f2fe] text-[#0369a1] border border-[#bae6fd] uppercase">
              SIH26117 Internal Round
            </span>
          </div>
          <p className="text-xs text-slate-600 mt-1 font-sans">
            Standard operating procedures with pre-indexed engineering specifications and host VRAM safety management.
          </p>
        </div>

        <div className="flex items-center gap-2 text-xs text-[#0369a1] bg-[#f0f9ff] border border-[#bae6fd] px-3.5 py-1.5 font-bold self-start md:self-auto">
          <ShieldCheck className="w-4 h-4 text-[#0284c7]" />
          <span>Local Engine &bull; Deterministic SOP Benchmark</span>
        </div>
      </div>

      {/* 3 Physical Instrument-Style Workflow Cards */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-5">
        {scenarios.map((sc) => {
          const theme = getScenarioTheme(sc.id);
          const isSelected = selectedScenario?.id === sc.id;

          return (
            <div
              key={sc.id}
              onClick={() => setSelectedScenario(sc)}
              className={`p-6 bg-white border-2 transition-all duration-150 cursor-pointer flex flex-col justify-between space-y-5 ${
                isSelected ? 'border-[#0284c7] brutal-shadow-blue ring-2 ring-[#0284c7]/20' : `border-[#cbd5e1] ${theme.borderHover}`
              }`}
            >
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-black font-display text-[#0284c7]">
                    {theme.code}
                  </span>
                  <span className={`text-[9px] font-bold px-2 py-0.5 uppercase border ${theme.badgeBg}`}>
                    {theme.tag}
                  </span>
                </div>

                <div>
                  <h3 className="font-black font-display text-base text-[#0f172a] uppercase">{sc.title}</h3>
                  <div className="text-[11px] font-bold text-slate-500 mt-0.5">{sc.unit}</div>
                </div>

                <p className="text-xs text-slate-600 leading-relaxed font-sans">
                  {sc.description}
                </p>
              </div>

              <div className="pt-4 border-t-2 border-[#f1f5f9] flex items-center justify-between">
                <span className="text-[10px] font-bold text-slate-500">
                  {sc.is_multimodal ? 'IMAGE + SOP' : 'DATASET + SOP'}
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleLaunchScenario(sc);
                  }}
                  className={`flex items-center gap-1.5 px-4 py-2 font-bold text-xs uppercase border-2 border-black brutal-shadow-dark brutal-btn ${theme.btnBg}`}
                >
                  <Play className="w-3 h-3 fill-current" />
                  <span>Execute</span>
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Selected Procedure Specification Details */}
      {selectedScenario && (
        <div className="bg-white border-2 border-[#cbd5e1] p-6 space-y-4 brutal-shadow-blue">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b-2 border-[#f1f5f9] pb-4">
            <div>
              <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest">
                Procedure Specification
              </div>
              <h2 className="text-base font-black font-display text-[#0f172a] uppercase mt-0.5">
                {selectedScenario.title}
              </h2>
            </div>
            <button
              onClick={() => handleLaunchScenario(selectedScenario)}
              className="flex items-center gap-2 px-5 py-2.5 bg-[#0284c7] hover:bg-[#0369a1] text-white font-bold text-xs uppercase border-2 border-black brutal-shadow-dark brutal-btn self-start sm:self-auto"
            >
              <span>Load Into Console</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {/* Step-by-step sequence */}
            <div className="space-y-3 bg-[#f8fafc] p-4 border border-[#e2e8f0]">
              <div className="text-xs font-bold text-[#0f172a] uppercase tracking-wider">
                Automated Procedure Steps
              </div>
              <div className="space-y-2 text-xs text-slate-700 font-sans">
                <div className="flex items-start gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-[#059669] shrink-0 mt-0.5" />
                  <span><strong>1. Input Ingestion:</strong> Evaluates dataset or inspection photograph</span>
                </div>
                <div className="flex items-start gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-[#059669] shrink-0 mt-0.5" />
                  <span><strong>2. Standard Retrieval:</strong> Cross-checks <em>{selectedScenario.benchmark_doc}</em></span>
                </div>
                <div className="flex items-start gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-[#059669] shrink-0 mt-0.5" />
                  <span><strong>3. Memory Safe Execution:</strong> Evicts inactive model to avoid VRAM bottleneck</span>
                </div>
                <div className="flex items-start gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-[#059669] shrink-0 mt-0.5" />
                  <span><strong>4. Verified Artifact:</strong> Generates <em>{selectedScenario.expected_artifact}</em></span>
                </div>
              </div>
            </div>

            {/* Instruction Command */}
            <div className="space-y-3 bg-[#f8fafc] p-4 border border-[#e2e8f0] flex flex-col justify-between">
              <div>
                <div className="text-xs font-bold text-[#0f172a] uppercase tracking-wider">
                  Instruction Command
                </div>
                <p className="text-xs text-slate-700 mt-2 p-3 bg-white border border-[#cbd5e1] leading-relaxed font-sans">
                  "{selectedScenario.prompt}"
                </p>
              </div>
              <div className="text-[10px] text-slate-500 font-bold flex items-center gap-1.5">
                <FileCheck className="w-3.5 h-3.5 text-[#0284c7]" />
                <span>SHA-256 integrity check logged to immutable SQLite WAL table.</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Advisory Footer */}
      <div className="p-3.5 bg-[#e0f2fe] border border-[#bae6fd] text-[#0369a1] text-xs flex items-center gap-3 font-sans font-medium">
        <AlertTriangle className="w-4 h-4 text-[#0284c7] shrink-0" />
        <span>
          <strong>Engineering Advisory:</strong> Workflows operate in an assistive capacity to support plant operator decision-making.
        </span>
      </div>
    </div>
  );
};
