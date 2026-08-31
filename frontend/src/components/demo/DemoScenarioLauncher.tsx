/**
 * frontend/src/components/demo/DemoScenarioLauncher.tsx
 * ------------------------------------------------------
 * Live One-Click Industrial Demo Scenarios for SIH Internal Round.
 *
 * Prominently showcases the 3 sovereign industrial workflows:
 * 1. Automated Industrial Diligence & Reporting (MRPL Hydrocarbon Stream XLSX)
 * 2. Multimodal Equipment Diagnostics (MOV-4102-B Valve Pitting Corrosion & SOP)
 * 3. Autonomous Incident Runbook (Flare Knock-Out Drum Pressure Anomaly & Dispatch)
 */

import React, { useState, useEffect } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import {
  FileSpreadsheet,
  Eye,
  AlertTriangle,
  Play,
  CheckCircle2,
  Cpu,
  Layers,
  ArrowRight,
  ShieldCheck,
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
    addToast('info', `Preloading Scenario: ${sc.title}`);
    // Switch to Chat tab
    setActiveTab('chat');
    // Dispatch custom event to auto-fill prompt in ChatView
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

  const getScenarioIcon = (id: string) => {
    switch (id) {
      case 'industrial_diligence':
        return <FileSpreadsheet className="w-5 h-5 text-emerald-400" />;
      case 'equipment_diagnostics':
        return <Eye className="w-5 h-5 text-blue-400" />;
      case 'incident_runbook':
        return <AlertTriangle className="w-5 h-5 text-amber-400" />;
      default:
        return <Layers className="w-5 h-5 text-purple-400" />;
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-[#090d16] text-slate-100 p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between border-b border-slate-800 pb-4">
        <div>
          <div className="flex items-center gap-2">
            <h1 className="text-xl font-bold tracking-tight text-white flex items-center gap-2">
              <Zap className="w-5 h-5 text-amber-400" />
              Live Industrial Demo Scenarios
            </h1>
            <span className="text-[10px] uppercase font-mono px-2 py-0.5 bg-emerald-500/10 border border-emerald-500/30 text-emerald-400 rounded font-semibold">
              SIH26117 Internal Round
            </span>
          </div>
          <p className="text-xs text-slate-400 mt-1">
            Pre-packaged, deterministic industrial decision-support workflows with preloaded synthetic refinery benchmarks.
          </p>
        </div>

        <div className="flex items-center gap-2 text-xs font-mono text-slate-400 bg-slate-900 border border-slate-800 px-3 py-1.5 rounded-lg">
          <ShieldCheck className="w-4 h-4 text-emerald-400" />
          <span>100% Local · Zero Cloud Dependencies</span>
        </div>
      </div>

      {/* Simulated Decision Support Disclaimer */}
      <div className="p-3 bg-amber-950/30 border border-amber-800/40 rounded-xl text-amber-300 text-xs flex items-center gap-2.5">
        <AlertTriangle className="w-4 h-4 shrink-0 text-amber-400" />
        <span>
          <strong>Simulated Decision-Support Notice:</strong> All models, benchmarks, and runbooks operate as assistive advisory tools. This workbench does not claim autonomous physical control or engineering-grade diagnosis.
        </span>
      </div>

      {/* Scenarios Grid */}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-5">
        {scenarios.map((sc) => {
          const isSelected = selectedScenario?.id === sc.id;
          return (
            <div
              key={sc.id}
              onClick={() => setSelectedScenario(sc)}
              className={`p-5 rounded-xl border transition-all cursor-pointer flex flex-col justify-between space-y-4 ${
                isSelected
                  ? 'bg-[#0f172a]/90 border-emerald-500/50 shadow-lg shadow-emerald-950/30'
                  : 'bg-[#0d1424]/60 border-slate-800 hover:border-slate-700'
              }`}
            >
              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <div className="w-9 h-9 rounded-lg bg-slate-900 border border-slate-800 flex items-center justify-center">
                    {getScenarioIcon(sc.id)}
                  </div>
                  <span className="text-[10px] font-mono uppercase px-2 py-0.5 rounded bg-slate-800 border border-slate-700 text-slate-300">
                    {sc.badge}
                  </span>
                </div>

                <div>
                  <h3 className="font-semibold text-sm text-slate-100">{sc.title}</h3>
                  <div className="text-[11px] font-mono text-emerald-400 mt-0.5">{sc.unit}</div>
                </div>

                <p className="text-xs text-slate-400 line-clamp-3 leading-relaxed">
                  {sc.description}
                </p>
              </div>

              <div className="pt-2 border-t border-slate-800/80 flex items-center justify-between">
                <span className="text-[11px] font-mono text-slate-500">
                  {sc.is_multimodal ? '📷 Vision + RAG' : '📄 Data + RAG'}
                </span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    handleLaunchScenario(sc);
                  }}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow transition-colors"
                >
                  <Play className="w-3.5 h-3.5 fill-current" />
                  <span>Launch Live</span>
                </button>
              </div>
            </div>
          );
        })}
      </div>

      {/* Detailed Inspection Box for Selected Scenario */}
      {selectedScenario && (
        <div className="bg-[#0d1424]/80 border border-slate-800 rounded-xl p-5 space-y-4">
          <div className="flex items-center justify-between border-b border-slate-800/80 pb-3">
            <div className="flex items-center gap-2">
              <h2 className="font-bold text-sm text-white">Scenario Workflow Specification</h2>
              <span className="text-xs font-mono text-slate-400">({selectedScenario.title})</span>
            </div>
            <button
              onClick={() => handleLaunchScenario(selectedScenario)}
              className="flex items-center gap-1.5 px-4 py-2 rounded-lg bg-emerald-600 hover:bg-emerald-500 text-white text-xs font-semibold shadow-md transition-colors"
            >
              <span>Execute This Scenario</span>
              <ArrowRight className="w-4 h-4" />
            </button>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs">
            <div className="space-y-2 bg-slate-900/60 p-3.5 rounded-lg border border-slate-800">
              <div className="font-mono uppercase text-[10px] text-slate-400 font-semibold">
                Autonomous Pipeline Execution Plan
              </div>
              <ul className="space-y-1.5 text-slate-300 font-mono text-[11px]">
                <li className="flex items-center gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>1. Parse user query &amp; assess task complexity heuristic</span>
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>2. Retrieve local grounding standard: {selectedScenario.benchmark_doc}</span>
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>3. Adaptive model allocation &amp; VRAM safety check</span>
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>4. Execute tool operations with cryptographic human approval gate</span>
                </li>
                <li className="flex items-center gap-2">
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-400" />
                  <span>5. Produce &amp; verify artifact: {selectedScenario.expected_artifact}</span>
                </li>
              </ul>
            </div>

            <div className="space-y-2 bg-slate-900/60 p-3.5 rounded-lg border border-slate-800">
              <div className="font-mono uppercase text-[10px] text-slate-400 font-semibold">
                Pre-Configured Prompt
              </div>
              <p className="text-slate-300 text-xs italic leading-relaxed bg-slate-950/80 p-2.5 rounded border border-slate-800">
                "{selectedScenario.prompt}"
              </p>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
