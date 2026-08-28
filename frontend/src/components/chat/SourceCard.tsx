import React, { useState } from 'react';
import type { SourceReference } from '../../types';
import { FileText, ChevronDown, ChevronUp } from 'lucide-react';
import { Badge } from '../common/Badge';

interface SourceCardProps {
  source: SourceReference;
  index?: number;
}

export const SourceCard: React.FC<SourceCardProps> = ({ source }) => {
  const [isExpanded, setIsExpanded] = useState<boolean>(false);

  // Score is cosine distance (0.0 = identical, 1.0 = opposite, lower is better)
  const relevancePercent = Math.max(0, Math.min(100, Math.round((1 - source.score) * 100)));
  const relevanceVariant = relevancePercent > 70 ? 'emerald' : relevancePercent > 45 ? 'blue' : 'amber';

  return (
    <div className="rounded-md border border-slate-800 bg-[#0d1424] text-xs transition-all overflow-hidden">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-2.5 text-left hover:bg-slate-800/40 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-6 h-6 rounded bg-slate-800/80 flex items-center justify-center text-slate-300 shrink-0">
            <FileText className="w-3.5 h-3.5 text-emerald-400" />
          </div>
          <div className="truncate">
            <div className="font-mono text-slate-200 truncate flex items-center gap-1.5 font-medium">
              <span>{source.filename}</span>
              {source.page && (
                <span className="text-[10px] text-slate-400 font-sans px-1 py-0.2 bg-slate-800 rounded">
                  p. {source.page}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Badge variant={relevanceVariant} className="text-[10px]">
            {relevancePercent}% Match
          </Badge>
          {isExpanded ? (
            <ChevronUp className="w-3.5 h-3.5 text-slate-400" />
          ) : (
            <ChevronDown className="w-3.5 h-3.5 text-slate-400" />
          )}
        </div>
      </button>

      {isExpanded && (
        <div className="p-3 border-t border-slate-800/80 bg-[#090d16] font-mono text-[11px] space-y-2">
          <div className="grid grid-cols-2 gap-2 text-slate-400">
            <div>
              <span className="text-slate-500">Document ID:</span>{' '}
              <span className="text-slate-300">{source.document_id}</span>
            </div>
            <div>
              <span className="text-slate-500">Chunk ID:</span>{' '}
              <span className="text-slate-300">{source.chunk_id}</span>
            </div>
            <div>
              <span className="text-slate-500">Chunk Index:</span>{' '}
              <span className="text-slate-300">{source.chunk_index}</span>
            </div>
            <div>
              <span className="text-slate-500">Cosine Distance:</span>{' '}
              <span className="text-slate-300">{source.score.toFixed(4)}</span>
            </div>
          </div>
          <div className="text-[10px] text-slate-500 italic pt-1 border-t border-slate-900">
            Retrieved from persistent ChromaDB collection. Grounded evidence used for answer synthesis.
          </div>
        </div>
      )}
    </div>
  );
};
