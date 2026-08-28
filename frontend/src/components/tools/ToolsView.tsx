import React from 'react';
import { Wrench, Search, FileCode, Terminal, Calculator, Shield, Lock } from 'lucide-react';
import { Badge } from '../common/Badge';

export const ToolsView: React.FC = () => {
  const tools = [
    {
      id: 'rag_search',
      title: 'Local Vector Document Search',
      category: 'Information Retrieval',
      status: 'Active in Engine',
      statusVariant: 'emerald' as const,
      icon: Search,
      description:
        'Retrieves semantically relevant document passages from local ChromaDB index using cosine similarity.',
      implemented: true,
      parameters: ['query: string', 'top_k: int (default: 5)'],
    },
    {
      id: 'file_reader',
      title: 'Local File Inspector',
      category: 'File Operations',
      status: 'Coming in Next Phase',
      statusVariant: 'amber' as const,
      icon: FileCode,
      description:
        'Controlled reading and metadata extraction from data/uploads directory with path traversal protection.',
      implemented: false,
      parameters: ['filename: string', 'line_range?: [int, int]'],
    },
    {
      id: 'code_sandbox',
      title: 'Python Execution Sandbox',
      category: 'Code Execution',
      status: 'Coming in Next Phase',
      statusVariant: 'amber' as const,
      icon: Terminal,
      description:
        'Subprocess sandbox with memory caps, timeout boundaries (30s), and network egress blocking.',
      implemented: false,
      parameters: ['code: string', 'timeout_sec: int (default: 30)'],
    },
    {
      id: 'calculator',
      title: 'Deterministic Math & Formula Evaluator',
      category: 'Symbolic Math',
      status: 'Coming in Next Phase',
      statusVariant: 'amber' as const,
      icon: Calculator,
      description:
        'Zero-hallucination arithmetic and financial formula evaluation using Python math AST parser.',
      implemented: false,
      parameters: ['expression: string'],
    },
  ];

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-[#090d16] p-6">
      <div className="max-w-5xl mx-auto w-full space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <Wrench className="w-5 h-5 text-amber-400" />
            Agent Tool Registry & Capabilities
          </h1>
          <p className="text-xs text-slate-400 mt-1 max-w-xl">
            Controlled tool integrations available to the agent engine. Tools execute strictly inside isolated local boundaries with human-in-the-loop or policy controls.
          </p>
        </div>

        {/* Security Assurance Banner */}
        <div className="p-4 rounded-xl border border-slate-800 bg-[#0d1424]/60 flex items-center gap-3">
          <Shield className="w-6 h-6 text-emerald-400 shrink-0" />
          <div className="text-xs text-slate-300">
            <span className="font-semibold text-white">Tool Sandbox Policy:</span> All tool dispatches are constrained to local directories and subprocesses. Outbound cloud API requests and uncontrolled filesystem operations are prohibited by design.
          </div>
        </div>

        {/* Tools Cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
          {tools.map((tool) => {
            const Icon = tool.icon;
            return (
              <div
                key={tool.id}
                className="p-5 rounded-xl border border-slate-800 bg-[#0d1424]/50 flex flex-col justify-between"
              >
                <div className="space-y-3">
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-2.5">
                      <div className="w-8 h-8 rounded-lg bg-slate-800 flex items-center justify-center text-slate-300">
                        <Icon className="w-4 h-4 text-emerald-400" />
                      </div>
                      <div>
                        <h3 className="text-sm font-semibold text-white">{tool.title}</h3>
                        <p className="text-[11px] text-slate-500 font-mono">{tool.category}</p>
                      </div>
                    </div>
                    <Badge variant={tool.statusVariant}>{tool.status}</Badge>
                  </div>

                  <p className="text-xs text-slate-300 leading-relaxed">{tool.description}</p>

                  {/* Parameter Schema */}
                  <div className="p-2.5 rounded-lg bg-[#090d16] border border-slate-800 font-mono text-[11px] space-y-1">
                    <div className="text-slate-500 font-medium">Input Contract:</div>
                    <div className="text-slate-300 space-y-0.5">
                      {tool.parameters.map((param, idx) => (
                        <div key={idx}>• {param}</div>
                      ))}
                    </div>
                  </div>
                </div>

                <div className="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs font-mono">
                  <span className="text-slate-500">Registry ID: {tool.id}</span>
                  {tool.implemented ? (
                    <span className="text-emerald-400 flex items-center gap-1 font-medium">
                      <span>Wired in RAG</span>
                    </span>
                  ) : (
                    <span className="text-slate-500 flex items-center gap-1">
                      <Lock className="w-3 h-3" />
                      <span>Phase 3 Tool Spec</span>
                    </span>
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
