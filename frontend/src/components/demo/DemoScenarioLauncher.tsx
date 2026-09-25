/**
 * frontend/src/components/demo/DemoScenarioLauncher.tsx
 * ------------------------------------------------------
 * Precision Industrial Procedures (White & Light Blue Style)
 */

import React, { useState, useEffect } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import {
  AlertTriangle,
  Play,
  CheckCircle2,
  ArrowRight,
  ShieldCheck,
  FileCheck,
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
  const [, setLoading] = useState<boolean>(true);
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
          badgeBg: 'bg-blue-50 text-blue-600 border-blue-200',
          btnBg: 'bg-blue-500 hover:bg-blue-600 text-white',
          borderHover: 'hover:border-blue-300',
          shadow: 'shadow-sm',
        };
      case 'equipment_diagnostics':
        return {
          code: 'PROC-02',
          tag: 'MECHANICAL NDT',
          badgeBg: 'bg-indigo-50 text-indigo-600 border-indigo-200',
          btnBg: 'bg-indigo-500 hover:bg-indigo-600 text-white',
          borderHover: 'hover:border-indigo-300',
          shadow: 'shadow-sm',
        };
      case 'incident_runbook':
        return {
          code: 'PROC-03',
          tag: 'PROCESS INTERLOCK',
          badgeBg: 'bg-amber-50 text-amber-600 border-amber-200',
          btnBg: 'bg-amber-500 hover:bg-amber-600 text-white',
          borderHover: 'hover:border-amber-300',
          shadow: 'shadow-sm',
        };
      default:
        return {
          code: 'PROC-00',
          tag: 'STANDARD',
          badgeBg: 'bg-slate-100 text-slate-600 border-slate-200',
          btnBg: 'bg-slate-600 hover:bg-slate-700 text-white',
          borderHover: 'hover:border-slate-300',
          shadow: 'shadow-sm',
        };
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-slate-50 text-slate-800 p-8 space-y-6 font-sans">
      {/* Station Header Bar */}
      <div className="flex flex-col md:flex-row md:items-center justify-between gap-4 p-6 bg-white border border-slate-200 rounded-xl shadow-sm">
        <div>
          <div className="flex items-center gap-3">
            <span className="w-3 h-3 bg-blue-500 rounded-full inline-block" />
            <h1 className="text-xl font-bold tracking-tight text-slate-900">
              Industrial Verification Procedures
            </h1>
            <span className="text-[10px] font-semibold px-2 py-0.5 bg-blue-50 text-blue-600 border border-blue-200 rounded-full uppercase">
              SIH26117 Internal Round
            </span>
          </div>
          <p className="text-xs text-slate-500 mt-2 font-sans">
            Standard operating procedures with pre-indexed engineering specifications and host VRAM safety management.
          </p>
        </div>

        <div className="flex items-center gap-2 text-xs text-blue-600 bg-blue-50 border border-blue-100 rounded-lg px-4 py-2 font-medium self-start md:self-auto">
          <ShieldCheck className="w-4 h-4 text-blue-500" />
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
              className={`p-6 bg-white border rounded-xl transition-all duration-150 cursor-pointer flex flex-col justify-between space-y-5 shadow-sm ${
                isSelected ? 'border-blue-500 ring-2 ring-blue-500/10' : `border-slate-200 ${theme.borderHover}`
              }`}
            >
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-bold text-blue-600">
                    {theme.code}
                  </span>
                  <span className={`text-[10px] font-semibold px-2 py-0.5 uppercase border rounded ${theme.badgeBg}`}>
                    {theme.tag}
                  </span>
                </div>

                <div>
                  <h3 className="font-bold text-base text-slate-900">{sc.title}</h3>
                  <div className="text-xs font-medium text-slate-500 mt-1">{sc.unit}</div>
                </div>

                <p className="text-xs text-slate-600 leading-relaxed font-sans">
                  {sc.description}
                </p>
              </div>

              <div className="pt-4 border-t border-slate-100 flex items-center justify-between">
                <span className="text-[10px] font-medium text-slate-400">
                  {sc.is_multimodal ? 'IMAGE + SOP' : 'DATASET + SOP'}
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleLaunchScenario(sc);
                  }}
                  className={`flex items-center gap-1.5 px-4 py-2 font-medium text-sm rounded-lg shadow-sm transition-all ${theme.btnBg}`}
                >
                  <Play className="w-3.5 h-3.5 fill-current" />
                  <span>Execute</span>
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Selected Procedure Specification Details */}
      {selectedScenario && (
        <div className="bg-white border border-slate-200 rounded-xl p-6 space-y-4 shadow-sm">
          <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-slate-100 pb-4">
            <div>
              <div className="text-[10px] font-semibold text-slate-500 uppercase tracking-widest">
                Procedure Specification
              </div>
              <h2 className="text-base font-bold text-slate-900 mt-1">
                {selectedScenario.title}
              </h2>
            </div>
            <button
              onClick={() => handleLaunchScenario(selectedScenario)}
              className="flex items-center gap-2 px-5 py-2.5 bg-blue-500 hover:bg-blue-600 text-white font-medium text-sm rounded-lg shadow-sm transition-all self-start sm:self-auto"
            >
              <span>Load Into Console</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
            {/* Step-by-step sequence */}
            <div className="space-y-3 bg-slate-50 p-5 rounded-lg border border-slate-200">
              <div className="text-xs font-semibold text-slate-800 uppercase tracking-wider">
                Automated Procedure Steps
              </div>
              <div className="space-y-2.5 text-sm text-slate-600 font-sans">
                <div className="flex items-start gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                  <span><strong>1. Input Ingestion:</strong> Evaluates dataset or inspection photograph</span>
                </div>
                <div className="flex items-start gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                  <span><strong>2. Standard Retrieval:</strong> Cross-checks <em>{selectedScenario.benchmark_doc}</em></span>
                </div>
                <div className="flex items-start gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                  <span><strong>3. Memory Safe Execution:</strong> Evicts inactive model to avoid VRAM bottleneck</span>
                </div>
                <div className="flex items-start gap-2.5">
                  <CheckCircle2 className="w-4 h-4 text-emerald-500 shrink-0 mt-0.5" />
                  <span><strong>4. Verified Artifact:</strong> Generates <em>{selectedScenario.expected_artifact}</em></span>
                </div>
              </div>
            </div>

            {/* Instruction Command */}
            <div className="space-y-3 bg-slate-50 p-5 rounded-lg border border-slate-200 flex flex-col justify-between">
              <div>
                <div className="text-xs font-semibold text-slate-800 uppercase tracking-wider">
                  Instruction Command
                </div>
                <p className="text-sm text-slate-600 mt-2 p-3 bg-white rounded border border-slate-200 leading-relaxed font-sans">
                  "{selectedScenario.prompt}"
                </p>
              </div>
              <div className="text-[10px] text-slate-500 font-medium flex items-center gap-1.5 mt-4">
                <FileCheck className="w-4 h-4 text-blue-500" />
                <span>SHA-256 integrity check logged to immutable SQLite WAL table.</span>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* Advisory Footer */}
      <div className="p-4 bg-blue-50 border border-blue-100 rounded-lg text-blue-700 text-xs flex items-center gap-3 font-sans font-medium shadow-sm">
        <AlertTriangle className="w-5 h-5 text-blue-500 shrink-0" />
        <span>
          <strong>Engineering Advisory:</strong> Workflows operate in an assistive capacity to support plant operator decision-making.
        </span>
      </div>
    </div>
  );
};
