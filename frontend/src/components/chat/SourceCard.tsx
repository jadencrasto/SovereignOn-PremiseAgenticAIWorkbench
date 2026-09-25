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
    <div className="rounded-lg border border-slate-200 bg-white shadow-sm text-xs transition-all overflow-hidden font-sans">
      <button
        onClick={() => setIsExpanded(!isExpanded)}
        className="w-full flex items-center justify-between p-2.5 text-left hover:bg-slate-50 transition-colors"
      >
        <div className="flex items-center gap-2 min-w-0">
          <div className="w-7 h-7 rounded-md bg-blue-50 flex items-center justify-center text-blue-500 shrink-0">
            <FileText className="w-4 h-4" />
          </div>
          <div className="truncate">
            <div className="font-mono text-slate-800 truncate flex items-center gap-2 font-semibold">
              <span>{source.filename}</span>
              {source.page ? (
                <span className="text-[10px] text-slate-500 font-sans px-1.5 py-0.5 bg-slate-100 rounded">
                  p. {source.page}
                </span>
              ) : (
                <span className="text-[10px] text-slate-500 font-sans px-1.5 py-0.5 bg-slate-100 rounded">
                  sec. {(source.chunk_index ?? 0) + 1}
                </span>
              )}
            </div>
          </div>
        </div>

        <div className="flex items-center gap-2 shrink-0">
          <Badge variant={relevanceVariant} className="text-[10px] font-medium">
            {relevancePercent}% Match
          </Badge>
          {isExpanded ? (
            <ChevronUp className="w-4 h-4 text-slate-500" />
          ) : (
            <ChevronDown className="w-4 h-4 text-slate-500" />
          )}
        </div>
      </button>

      {isExpanded && (
        <div className="p-3 border-t border-slate-100 bg-slate-50/50 font-mono text-[11px] space-y-2">
          <div className="grid grid-cols-2 gap-2 text-slate-600">
            <div>
              <span className="text-slate-400">Document ID:</span>{' '}
              <span className="text-slate-700 font-medium">{source.document_id}</span>
            </div>
            <div>
              <span className="text-slate-400">Chunk ID:</span>{' '}
              <span className="text-slate-700 font-medium">{source.chunk_id}</span>
            </div>
            <div>
              <span className="text-slate-400">Chunk Index:</span>{' '}
              <span className="text-slate-700 font-medium">{source.chunk_index}</span>
            </div>
            <div>
              <span className="text-slate-400">Cosine Distance:</span>{' '}
              <span className="text-slate-700 font-medium">{source.score.toFixed(4)}</span>
            </div>
          </div>
          <div className="text-[10px] text-slate-400 italic pt-1.5 border-t border-slate-200 mt-2">
            Retrieved from persistent ChromaDB collection. Grounded evidence used for answer synthesis.
          </div>
        </div>
      )}
    </div>
  );
};
