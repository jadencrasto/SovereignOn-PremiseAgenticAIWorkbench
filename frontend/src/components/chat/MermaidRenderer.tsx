/**
 * frontend/src/components/chat/MermaidRenderer.tsx
 * -------------------------------------------------
 * Interactive, high-fidelity Mermaid Diagram Renderer for Visual AI Explanations.
 * Supports flowcharts, sequence diagrams, state diagrams, class diagrams, Gantt charts,
 * entity-relationship diagrams, and git graphs with modern dark aesthetics.
 */

import React, { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';
import {
  Maximize2,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Copy,
  Check,
  Code2,
  Eye,
  AlertCircle,
} from 'lucide-react';

interface MermaidRendererProps {
  chart: string;
}

// Configure mermaid with modern sleek theme
mermaid.initialize({
  startOnLoad: false,
  theme: 'dark',
  themeVariables: {
    darkMode: true,
    background: '#070b14',
    primaryColor: '#0284c7',
    primaryTextColor: '#f8fafc',
    primaryBorderColor: '#38bdf8',
    lineColor: '#38bdf8',
    secondaryColor: '#1e293b',
    tertiaryColor: '#0f172a',
    mainBkg: '#0f172a',
    nodeBorder: '#38bdf8',
    clusterBkg: '#0b1329',
    clusterBorder: '#1e293b',
    edgeLabelBackground: '#0b1329',
    fontFamily: 'ui-sans-serif, system-ui, sans-serif',
    fontSize: '13px',
  },
  securityLevel: 'loose',
  flowchart: {
    useMaxWidth: true,
    htmlLabels: true,
    curve: 'basis',
  },
});

export const MermaidRenderer: React.FC<MermaidRendererProps> = ({ chart }) => {
  const containerRef = useRef<HTMLDivElement>(null);
  const [svgContent, setSvgContent] = useState<string>('');
  const [hasError, setHasError] = useState<boolean>(false);
  const [errorMessage, setErrorMessage] = useState<string>('');
  const [showCode, setShowCode] = useState<boolean>(false);
  const [copied, setCopied] = useState<boolean>(false);
  const [zoom, setZoom] = useState<number>(1.0);

  const cleanChart = chart.trim();

  useEffect(() => {
    let isMounted = true;
    const renderId = `mermaid_${Math.random().toString(36).substring(2, 9)}`;

    const renderChart = async () => {
      if (!cleanChart) return;
      try {
        setHasError(false);
        const { svg } = await mermaid.render(renderId, cleanChart);
        if (isMounted) {
          setSvgContent(svg);
        }
      } catch (err: any) {
        if (isMounted) {
          console.warn('Mermaid render issue:', err);
          setHasError(true);
          setErrorMessage(err?.message || 'Syntax error in diagram definition.');
        }
      }
    };

    renderChart();

    return () => {
      isMounted = false;
      // Clean up temporary DOM element created by mermaid if any
      const tempEl = document.getElementById(renderId);
      if (tempEl) tempEl.remove();
    };
  }, [cleanChart]);

  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(cleanChart);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // ignore
    }
  };

  return (
    <div className="my-4 rounded-xl border border-sky-500/30 bg-[#070b14] overflow-hidden shadow-xl shadow-black/60">
      {/* Top Diagram Toolbar */}
      <div className="px-3.5 py-2 bg-slate-900/90 border-b border-slate-800 flex items-center justify-between gap-2 text-xs">
        <div className="flex items-center gap-2">
          <span className="w-2 h-2 rounded-full bg-sky-400 animate-pulse" />
          <span className="font-semibold text-slate-200 tracking-tight flex items-center gap-1.5">
            <span>Visual Architecture Diagram</span>
            <span className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-sky-500/10 text-sky-400 border border-sky-500/20 uppercase">
              MERMAID
            </span>
          </span>
        </div>

        <div className="flex items-center gap-1.5">
          {/* Zoom Controls (when in diagram mode) */}
          {!showCode && !hasError && (
            <div className="flex items-center bg-slate-800/80 rounded border border-slate-700/80 p-0.5 mr-1">
              <button
                onClick={() => setZoom((z) => Math.min(z + 0.15, 2.0))}
                className="p-1 text-slate-400 hover:text-white rounded transition-colors"
                title="Zoom In"
              >
                <ZoomIn className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setZoom((z) => Math.max(z - 0.15, 0.5))}
                className="p-1 text-slate-400 hover:text-white rounded transition-colors"
                title="Zoom Out"
              >
                <ZoomOut className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setZoom(1.0)}
                className="p-1 text-slate-400 hover:text-white rounded transition-colors"
                title="Reset Zoom"
              >
                <RotateCcw className="w-3.5 h-3.5" />
              </button>
            </div>
          )}

          {/* Toggle Code/Diagram */}
          <button
            onClick={() => setShowCode(!showCode)}
            className="flex items-center gap-1 px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded transition-colors text-[11px] font-medium"
            title={showCode ? 'View rendered diagram' : 'View diagram source code'}
          >
            {showCode ? <Eye className="w-3 h-3 text-sky-400" /> : <Code2 className="w-3 h-3 text-sky-400" />}
            <span>{showCode ? 'Diagram' : 'Source'}</span>
          </button>

          {/* Copy Button */}
          <button
            onClick={handleCopy}
            className="flex items-center gap-1 px-2 py-1 bg-slate-800 hover:bg-slate-700 text-slate-300 hover:text-white rounded transition-colors text-[11px] font-medium"
            title="Copy Mermaid Code"
          >
            {copied ? <Check className="w-3 h-3 text-emerald-400" /> : <Copy className="w-3 h-3" />}
            <span>{copied ? 'Copied' : 'Copy'}</span>
          </button>
        </div>
      </div>

      {/* Main Render Area */}
      {showCode ? (
        <div className="p-3 bg-[#0a0f1d] overflow-x-auto">
          <pre className="text-xs font-mono text-sky-300 leading-relaxed">
            <code>{cleanChart}</code>
          </pre>
        </div>
      ) : hasError ? (
        <div className="p-4 bg-amber-950/20 border-t border-amber-900/30 flex flex-col gap-2 text-xs">
          <div className="flex items-center gap-2 text-amber-400 font-semibold">
            <AlertCircle className="w-4 h-4 shrink-0" />
            <span>Diagram Definition</span>
          </div>
          <p className="text-slate-400 font-mono text-[11px]">{errorMessage}</p>
          <pre className="p-2.5 rounded bg-slate-900 border border-slate-800 text-slate-300 font-mono text-[11px] overflow-x-auto">
            <code>{cleanChart}</code>
          </pre>
        </div>
      ) : (
        <div
          ref={containerRef}
          className="p-5 overflow-x-auto overflow-y-hidden flex items-center justify-center min-h-[140px] bg-gradient-to-b from-[#070b14] to-[#0a101f]"
          style={{
            transform: `scale(${zoom})`,
            transformOrigin: 'center center',
            transition: 'transform 0.15s ease-out',
          }}
          dangerouslySetInnerHTML={{ __html: svgContent }}
        />
      )}
    </div>
  );
};
