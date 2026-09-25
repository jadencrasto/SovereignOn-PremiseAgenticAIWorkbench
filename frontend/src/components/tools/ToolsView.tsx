import React, { useEffect, useState } from 'react';
import {
  Wrench,
  Search,
  FileCode,
  Calculator,
  FileText,
  FilePlus,
  Shield,
  Loader2,
  Terminal,
  BookOpen,
  FileSpreadsheet,
  CheckCircle2,
  Network,
} from 'lucide-react';
import { Badge } from '../common/Badge';
import { fetchTools } from '../../api/tools';
import type { ToolInfo } from '../../types';

const TOOL_ICONS: Record<string, React.FC<{ className?: string }>> = {
  document_search: Search,
  file_list: FileText,
  file_read: FileCode,
  calculator: Calculator,
  file_write: FilePlus,
  code_execution: Terminal,
  docx_create: BookOpen,
  xlsx_report: FileSpreadsheet,
  artifact_verifier: CheckCircle2,
  knowledge_graph_query: Network,
};

export const ToolsView: React.FC = () => {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchTools()
      .then((data) => {
        if (!cancelled) {
          setTools(data.tools);
          setLoading(false);
        }
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err.message || 'Failed to fetch tools');
          setLoading(false);
        }
      });
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-slate-50 p-6 font-sans">
      <div className="max-w-5xl mx-auto w-full space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-xl font-bold text-slate-900 tracking-tight flex items-center gap-2">
            <Wrench className="w-5 h-5 text-blue-500" />
            Agent Tool Registry &amp; Capabilities
          </h1>
          <p className="text-sm text-slate-500 mt-1 max-w-xl">
            Controlled tool integrations available to the agent engine. Tools execute strictly inside isolated local boundaries with audit logging and policy controls.
          </p>
        </div>

        {/* Security Assurance Banner */}
        <div className="p-4 rounded-xl border border-slate-200 bg-white flex items-center gap-3 shadow-sm">
          <Shield className="w-6 h-6 text-blue-500 shrink-0" />
          <div className="text-sm text-slate-600 leading-relaxed">
            <span className="font-bold text-slate-900">Tool Sandbox Policy:</span> All tool dispatches are constrained to local directories and subprocesses. Outbound cloud API requests and uncontrolled filesystem operations are prohibited by design. Every execution is audit-logged.
          </div>
        </div>

        {/* Loading State */}
        {loading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 text-slate-500 animate-spin" />
            <span className="ml-2 text-sm font-medium text-slate-500">Loading tool registry...</span>
          </div>
        )}

        {/* Error State */}
        {error && (
          <div className="p-4 rounded-xl border border-rose-200 bg-rose-50 text-rose-700 text-sm font-medium shadow-sm">
            Failed to load tools: {error}
          </div>
        )}

        {/* Tools Cards */}
        {!loading && !error && (
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            {tools.map((tool) => {
              const Icon = TOOL_ICONS[tool.name] || Wrench;
              const statusVariant = tool.enabled ? 'emerald' as const : 'slate' as const;
              const statusText = tool.enabled ? 'Active' : 'Disabled';

              return (
                <div
                  key={tool.name}
                  className="p-5 rounded-xl border border-slate-200 bg-white flex flex-col justify-between shadow-sm"
                >
                  <div className="space-y-4">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-blue-50 flex items-center justify-center">
                          <Icon className="w-5 h-5 text-blue-600" />
                        </div>
                        <div>
                          <h3 className="text-sm font-bold text-slate-900">{tool.name}</h3>
                          <p className="text-xs text-slate-500 font-medium uppercase tracking-wider">{tool.category}</p>
                        </div>
                      </div>
                      <Badge variant={statusVariant}>{statusText}</Badge>
                    </div>

                    <p className="text-sm text-slate-600 leading-relaxed">{tool.description}</p>

                    {/* Parameter Schema */}
                    <div className="p-4 rounded-lg bg-slate-50 border border-slate-200 font-mono text-xs space-y-2">
                      <div className="text-slate-800 font-semibold mb-1">Input Contract:</div>
                      <div className="text-slate-600 space-y-1">
                        {tool.input_schema?.properties &&
                          Object.entries(tool.input_schema.properties as Record<string, Record<string, string>>).map(([pname, pinfo]) => (
                            <div key={pname} className="flex flex-col sm:flex-row sm:items-baseline gap-1">
                              <div>
                                • <span className="text-blue-600 font-bold">{pname}</span>
                                <span className="text-slate-500 font-medium">: {pinfo?.type || 'any'}</span>
                              </div>
                              {pinfo?.description && (
                                <span className="text-slate-500 sm:ml-1 hidden sm:inline">— {pinfo.description.substring(0, 80)}</span>
                              )}
                              {pinfo?.description && (
                                <span className="text-slate-500 ml-3 sm:hidden block mt-0.5">{pinfo.description.substring(0, 80)}</span>
                              )}
                            </div>
                          ))}
                      </div>
                    </div>
                  </div>

                  <div className="mt-5 pt-4 border-t border-slate-100 flex items-center justify-between text-xs font-semibold">
                    <div className="flex items-center gap-2">
                      <span className={tool.read_only ? 'text-emerald-600' : 'text-amber-600'}>
                        {tool.read_only ? '● Read-Only' : '● Mutating'}
                      </span>
                      <span className={`px-2 py-0.5 text-[10px] uppercase font-bold rounded border ${
                        tool.risk_level === 'high'
                          ? 'bg-rose-50 text-rose-600 border-rose-200'
                          : tool.risk_level === 'medium'
                          ? 'bg-amber-50 text-amber-600 border-amber-200'
                          : 'bg-blue-50 text-blue-600 border-blue-200'
                      }`}>
                        {tool.risk_level || 'low'} Risk
                      </span>
                      {tool.requires_approval && (
                        <span className="px-2 py-0.5 text-[10px] uppercase font-bold rounded bg-amber-50 text-amber-600 border border-amber-200">
                          Approval Gate
                        </span>
                      )}
                    </div>
                    <span className="text-slate-400 font-medium">ID: {tool.name}</span>
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
};
