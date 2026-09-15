/**
 * frontend/src/components/chat/MermaidRenderer.tsx
 * -------------------------------------------------
 * Interactive, high-fidelity Mermaid Diagram Renderer for Visual AI Explanations.
 * Supports flowcharts, sequence diagrams, state diagrams, class diagrams, Gantt charts,
 * entity-relationship diagrams, and git graphs with modern dark aesthetics.
 *
 * Improvements:
 *   - Pre-render sanitization of LLM-generated Mermaid source (safe node IDs, escaped labels)
 *   - Pre-render validation via mermaid.parse() before attempting render
 *   - Graceful fallback for invalid diagrams (no repeated error spam)
 *   - Deterministic render IDs based on chart content hash
 *   - Proper DOM cleanup on unmount
 */

import React, { useEffect, useRef, useState } from 'react';
import mermaid from 'mermaid';
import {
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Copy,
  Check,
  Code2,
  Eye,
  AlertCircle,
} from 'lucide-react';
import { sanitizeMermaidSource, generateDeterministicId } from './mermaidUtils';

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

  // Sanitize the Mermaid source for safe rendering
  const sanitizedChart = sanitizeMermaidSource(cleanChart);

  // Generate a deterministic render ID from chart content
  const renderId = generateDeterministicId(sanitizedChart);

  useEffect(() => {
    let isMounted = true;
    // Use a unique suffix per render cycle to avoid stale DOM element collisions
    const renderElId = `${renderId}_${Date.now()}`;

    const renderChart = async () => {
      if (!sanitizedChart) return;

      try {
        setHasError(false);
        setErrorMessage('');

        // Step 1: Pre-validate with mermaid.parse()
        // This catches syntax errors before creating DOM elements
        try {
          await mermaid.parse(sanitizedChart);
        } catch (parseErr: unknown) {
          if (isMounted) {
            const msg = parseErr instanceof Error ? parseErr.message : String(parseErr);
            console.warn('Mermaid parse validation failed:', msg);
            setHasError(true);
            setErrorMessage('Workflow diagram unavailable — diagram syntax could not be validated.');
          }
          return;
        }

        // Step 2: Render the validated chart
        const { svg } = await mermaid.render(renderElId, sanitizedChart);
        if (isMounted) {
          setSvgContent(svg);
        }
      } catch (err: unknown) {
        if (isMounted) {
          const msg = err instanceof Error ? err.message : String(err);
          // Log once, don't spam
          console.warn('Mermaid render issue:', msg);
          setHasError(true);
          setErrorMessage('Workflow diagram unavailable — view source for details.');
        }
      }
    };

    renderChart();

    return () => {
      isMounted = false;
      // Clean up temporary DOM elements created by mermaid
      const tempEl = document.getElementById(renderElId);
      if (tempEl) tempEl.remove();
      // Also clean up any element with a 'd' prefix that mermaid creates
      const dEl = document.getElementById(`d${renderElId}`);
      if (dEl) dEl.remove();
    };
  }, [sanitizedChart, renderId]);

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
            <span>{errorMessage || 'Workflow diagram unavailable'}</span>
          </div>
          <p className="text-slate-500 text-[11px]">
            Click &quot;Source&quot; above to view the raw diagram definition.
          </p>
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
