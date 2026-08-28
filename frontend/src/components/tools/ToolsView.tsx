import React, { useEffect, useState } from 'react';
import { Wrench, Search, FileCode, Calculator, FileText, FilePlus, Shield, Loader2 } from 'lucide-react';
import { Badge } from '../common/Badge';
import { fetchTools } from '../../api/tools';
import type { ToolInfo } from '../../types';

const TOOL_ICONS: Record<string, React.FC<{ className?: string }>> = {
  document_search: Search,
  file_list: FileText,
  file_read: FileCode,
  calculator: Calculator,
  file_write: FilePlus,
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
    <div className="flex-1 flex flex-col h-full overflow-y-auto bg-[#090d16] p-6">
      <div className="max-w-5xl mx-auto w-full space-y-6">
        {/* Header */}
        <div>
          <h1 className="text-xl font-bold text-white tracking-tight flex items-center gap-2">
            <Wrench className="w-5 h-5 text-amber-400" />
            Agent Tool Registry &amp; Capabilities
          </h1>
          <p className="text-xs text-slate-400 mt-1 max-w-xl">
            Controlled tool integrations available to the agent engine. Tools execute strictly inside isolated local boundaries with audit logging and policy controls.
          </p>
        </div>

        {/* Security Assurance Banner */}
        <div className="p-4 rounded-xl border border-slate-800 bg-[#0d1424]/60 flex items-center gap-3">
          <Shield className="w-6 h-6 text-emerald-400 shrink-0" />
          <div className="text-xs text-slate-300">
            <span className="font-semibold text-white">Tool Sandbox Policy:</span> All tool dispatches are constrained to local directories and subprocesses. Outbound cloud API requests and uncontrolled filesystem operations are prohibited by design. Every execution is audit-logged.
          </div>
        </div>

        {/* Loading State */}
        {loading && (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="w-6 h-6 text-slate-500 animate-spin" />
            <span className="ml-2 text-sm text-slate-400">Loading tool registry...</span>
          </div>
        )}

        {/* Error State */}
        {error && (
          <div className="p-4 rounded-xl border border-rose-800/50 bg-rose-950/20 text-rose-300 text-sm">
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
                  className="p-5 rounded-xl border border-slate-800 bg-[#0d1424]/50 flex flex-col justify-between"
                >
                  <div className="space-y-3">
                    <div className="flex items-center justify-between">
                      <div className="flex items-center gap-2.5">
                        <div className="w-8 h-8 rounded-lg bg-slate-800 flex items-center justify-center text-slate-300">
                          <Icon className="w-4 h-4 text-emerald-400" />
                        </div>
                        <div>
                          <h3 className="text-sm font-semibold text-white font-mono">{tool.name}</h3>
                          <p className="text-[11px] text-slate-500 font-mono">{tool.category}</p>
                        </div>
                      </div>
                      <Badge variant={statusVariant}>{statusText}</Badge>
                    </div>

                    <p className="text-xs text-slate-300 leading-relaxed">{tool.description}</p>

                    {/* Parameter Schema */}
                    <div className="p-2.5 rounded-lg bg-[#090d16] border border-slate-800 font-mono text-[11px] space-y-1">
                      <div className="text-slate-500 font-medium">Input Contract:</div>
                      <div className="text-slate-300 space-y-0.5">
                        {tool.input_schema?.properties &&
                          Object.entries(tool.input_schema.properties as Record<string, Record<string, string>>).map(([pname, pinfo]) => (
                            <div key={pname}>
                              • <span className="text-amber-300">{pname}</span>
                              <span className="text-slate-500">: {pinfo?.type || 'any'}</span>
                              {pinfo?.description && (
                                <span className="text-slate-600 ml-1">— {pinfo.description.substring(0, 80)}</span>
                              )}
                            </div>
                          ))}
                      </div>
                    </div>
                  </div>

                  <div className="mt-4 pt-3 border-t border-slate-800/80 flex items-center justify-between text-xs font-mono">
                    <div className="flex items-center gap-3">
                      <span className={tool.read_only ? 'text-emerald-400' : 'text-amber-400'}>
                        {tool.read_only ? '● Read-Only' : '● Mutating'}
                      </span>
                      {tool.requires_confirmation && (
                        <span className="text-amber-400/70">Requires Confirmation</span>
                      )}
                    </div>
                    <span className="text-slate-500">Registry ID: {tool.name}</span>
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
