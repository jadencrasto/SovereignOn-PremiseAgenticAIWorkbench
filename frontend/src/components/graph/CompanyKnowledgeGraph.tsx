/**
 * frontend/src/components/graph/CompanyKnowledgeGraph.tsx
 * --------------------------------------------------------
 * Sleek, High-Performance Sovereign Knowledge Graph & Live Topology Visualizer.
 * 
 * Features:
 * - Ultra-Smooth Damped Force Simulation with Kinetic Alpha Cooling (Zero Juggling / Jitter)
 * - Plain Minimalist Dark Matte Canvas (No Cluttered Grid Lines)
 * - Real-Time Live Document Reflection from Local ChromaDB Ingestion
 * - Dual-Boundary RBAC Clearance Filtering (Viewer / Operator / Admin)
 * - Interactive Entity Inspector Drawer with Instant Agent Query Dispatch
 */

import React, { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { useAuth } from '../../context/AuthContext';
import {
  Shield,
  Lock,
  Search,
  RefreshCw,
  ZoomIn,
  ZoomOut,
  RotateCcw,
  Sparkles,
  Layers,
  Activity,
  FileText,
  AlertTriangle,
  FileCode,
  Zap,
  CheckCircle2,
  Pause,
  Play,
  ArrowRight,
  Database,
} from 'lucide-react';

interface GraphNode {
  id: string;
  label: string;
  category: 'unit' | 'equipment' | 'sensor' | 'defect' | 'sop' | 'document' | 'classified' | 'restricted_stub';
  clearance: 'viewer' | 'operator' | 'admin';
  description: string;
  properties: Record<string, string>;
  x: number;
  y: number;
  vx: number;
  vy: number;
  radius?: number;
  pinned?: boolean;
}

interface GraphEdge {
  source: string;
  target: string;
  label: string;
  clearance: 'viewer' | 'operator' | 'admin';
}

interface GraphResponse {
  user_role: string;
  effective_clearance: string;
  clearance_level: number;
  nodes_count: number;
  edges_count: number;
  hidden_nodes: number;
  nodes: GraphNode[];
  edges: GraphEdge[];
  categories: Record<string, string>;
}

export const CompanyKnowledgeGraph: React.FC = () => {
  const { role: userAuthRole } = useAuth();
  const { setActiveTab, addToast, documents, refreshDocuments } = useWorkbench();

  const [selectedClearance, setSelectedClearance] = useState<string>(userAuthRole || 'viewer');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [graphMeta, setGraphMeta] = useState<GraphResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);
  const [isPhysicsFrozen, setIsPhysicsFrozen] = useState<boolean>(false);

  // Pan & Zoom
  const [zoom, setZoom] = useState<number>(0.95);
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  // Dragging & Interaction State
  const [draggedNodeId, setDraggedNodeId] = useState<string | null>(null);
  const [isPanningCanvas, setIsPanningCanvas] = useState<boolean>(false);
  const [panStart, setPanStart] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const canvasRef = useRef<HTMLDivElement>(null);
  const animFrameRef = useRef<number | null>(null);
  const nodesRef = useRef<GraphNode[]>([]);
  const edgesRef = useRef<GraphEdge[]>([]);
  const alphaRef = useRef<number>(1.0); // Kinetic energy cooling factor

  nodesRef.current = nodes;
  edgesRef.current = edges;

  // Sync clearance if user logs in
  useEffect(() => {
    if (userAuthRole) setSelectedClearance(userAuthRole);
  }, [userAuthRole]);

  // Color Mapping & Visual Tokens
  const getNodeColor = useCallback((cat: string) => {
    if (cat === 'restricted_stub') {
      return {
        fill: '#1e293b',
        stroke: '#475569',
        glow: 'rgba(100, 116, 139, 0.2)',
        text: '#94a3b8',
        tagBg: 'bg-slate-800 text-slate-400 border-slate-700',
        badge: 'RESTRICTED',
      };
    }
    switch (cat) {
      case 'unit':
        return {
          fill: '#0284c7',
          stroke: '#38bdf8',
          glow: 'rgba(56, 189, 248, 0.35)',
          text: '#ffffff',
          tagBg: 'bg-sky-950/80 text-sky-300 border-sky-800',
          badge: 'UNIT',
        };
      case 'equipment':
        return {
          fill: '#2563eb',
          stroke: '#60a5fa',
          glow: 'rgba(96, 165, 250, 0.35)',
          text: '#ffffff',
          tagBg: 'bg-blue-950/80 text-blue-300 border-blue-800',
          badge: 'EQUIPMENT',
        };
      case 'sensor':
        return {
          fill: '#059669',
          stroke: '#34d399',
          glow: 'rgba(52, 211, 153, 0.35)',
          text: '#ffffff',
          tagBg: 'bg-emerald-950/80 text-emerald-300 border-emerald-800',
          badge: 'SENSOR',
        };
      case 'defect':
        return {
          fill: '#d97706',
          stroke: '#fbbf24',
          glow: 'rgba(251, 191, 36, 0.35)',
          text: '#ffffff',
          tagBg: 'bg-amber-950/80 text-amber-300 border-amber-800',
          badge: 'DEFECT',
        };
      case 'sop':
        return {
          fill: '#7c3aed',
          stroke: '#a78bfa',
          glow: 'rgba(167, 139, 250, 0.35)',
          text: '#ffffff',
          tagBg: 'bg-purple-950/80 text-purple-300 border-purple-800',
          badge: 'SOP',
        };
      case 'document':
        return {
          fill: '#0d9488',
          stroke: '#2dd4bf',
          glow: 'rgba(45, 212, 191, 0.45)',
          text: '#ffffff',
          tagBg: 'bg-teal-950/80 text-teal-300 border-teal-700',
          badge: 'RAG DOC',
        };
      case 'classified':
        return {
          fill: '#dc2626',
          stroke: '#f87171',
          glow: 'rgba(248, 113, 113, 0.45)',
          text: '#ffffff',
          tagBg: 'bg-rose-950/80 text-rose-300 border-rose-800',
          badge: 'CLASSIFIED',
        };
      default:
        return {
          fill: '#334155',
          stroke: '#64748b',
          glow: 'rgba(100, 116, 139, 0.25)',
          text: '#ffffff',
          tagBg: 'bg-slate-900 text-slate-300 border-slate-700',
          badge: 'ENTITY',
        };
    }
  }, []);

  // Fetch Graph Data (Includes Live ChromaDB Documents)
  const fetchGraph = async (clearance: string) => {
    setIsLoading(true);
    try {
      const res = await fetch(`/api/knowledge-graph?clearance=${clearance}`);
      if (res.ok) {
        const data: GraphResponse = await res.json();
        setGraphMeta(data);

        // Center Coordinate Space
        const width = 900;
        const height = 580;
        const cx = width / 2;
        const cy = height / 2;

        const count = data.nodes.length;
        const initializedNodes: GraphNode[] = data.nodes.map((node, i) => {
          // Stable layered orbit positioning based on entity category
          let dist = 180;
          if (node.category === 'unit') dist = 80;
          else if (node.category === 'equipment') dist = 170;
          else if (node.category === 'sensor') dist = 240;
          else if (node.category === 'defect') dist = 270;
          else if (node.category === 'sop') dist = 210;
          else if (node.category === 'document') dist = 140;
          else if (node.category === 'classified' || node.category === 'restricted_stub') dist = 300;

          const angle = (i / count) * 2 * Math.PI - Math.PI / 2;
          return {
            ...node,
            x: cx + dist * Math.cos(angle) + (Math.sin(i * 3) * 15),
            y: cy + dist * Math.sin(angle) + (Math.cos(i * 2) * 15),
            vx: 0,
            vy: 0,
            radius: node.category === 'unit' ? 22 : node.category === 'document' ? 20 : node.category === 'equipment' ? 18 : 16,
          };
        });

        setNodes(initializedNodes);
        setEdges(data.edges);
        alphaRef.current = 1.0; // Trigger smooth initial cooling

        if (!selectedNodeId && initializedNodes.length > 0) {
          setSelectedNodeId(initializedNodes[0].id);
        }
      } else {
        addToast('error', 'Failed to load graph data.');
      }
    } catch {
      addToast('error', 'Error connecting to sovereign knowledge graph service.');
    } finally {
      setIsLoading(false);
    }
  };

  // Initial & Clearance-change Load
  useEffect(() => {
    fetchGraph(selectedClearance);
  }, [selectedClearance]);

  // Re-fetch when live documents change in the workbench
  useEffect(() => {
    if (documents.length > 0) {
      fetchGraph(selectedClearance);
    }
  }, [documents.length]);

  // Smooth Damped Force Physics Simulation with Kinetic Alpha Cooling (No Juggling!)
  useEffect(() => {
    let running = true;

    const simulate = () => {
      if (!running) return;

      // If physics is frozen or settled below threshold, sleep to prevent jitter
      if (!isPhysicsFrozen && alphaRef.current > 0.002) {
        setNodes((prevNodes) => {
          if (prevNodes.length === 0) return prevNodes;

          const updated = prevNodes.map((n) => ({ ...n }));
          const nodeMap = new Map(updated.map((n) => [n.id, n]));
          const cx = 450;
          const cy = 290;
          const currentAlpha = alphaRef.current;

          // 1. Soft Pairwise Repulsion with Distance Softening
          for (let i = 0; i < updated.length; i++) {
            for (let j = i + 1; j < updated.length; j++) {
              const n1 = updated[i];
              const n2 = updated[j];
              const dx = n2.x - n1.x;
              const dy = n2.y - n1.y;
              const distSq = dx * dx + dy * dy || 1;
              const dist = Math.sqrt(distSq);

              if (dist < 260) {
                // Soft spring repulsion formula with safe damping
                const force = ((260 - dist) / (distSq + 400)) * 40 * currentAlpha;
                const fx = Math.max(-3.5, Math.min(3.5, (dx / dist) * force));
                const fy = Math.max(-3.5, Math.min(3.5, (dy / dist) * force));

                if (n1.id !== draggedNodeId && !n1.pinned) {
                  n1.vx -= fx;
                  n1.vy -= fy;
                }
                if (n2.id !== draggedNodeId && !n2.pinned) {
                  n2.vx += fx;
                  n2.vy += fy;
                }
              }
            }
          }

          // 2. Link Spring Attraction
          for (const edge of edgesRef.current) {
            const source = nodeMap.get(edge.source);
            const target = nodeMap.get(edge.target);
            if (source && target) {
              const dx = target.x - source.x;
              const dy = target.y - source.y;
              const dist = Math.sqrt(dx * dx + dy * dy) || 1;
              const targetDist = 120;
              const force = (dist - targetDist) * 0.022 * currentAlpha;
              const fx = (dx / dist) * force;
              const fy = (dy / dist) * force;

              if (source.id !== draggedNodeId && !source.pinned) {
                source.vx += fx;
                source.vy += fy;
              }
              if (target.id !== draggedNodeId && !target.pinned) {
                target.vx -= fx;
                target.vy -= fy;
              }
            }
          }

          // 3. Center Gravity, Friction Damping & Bounds Clamping
          for (const node of updated) {
            if (node.id === draggedNodeId) continue;

            // Gentle center gravity
            const dx = cx - node.x;
            const dy = cy - node.y;
            node.vx += dx * 0.0025 * currentAlpha;
            node.vy += dy * 0.0025 * currentAlpha;

            // Strong velocity friction damping (78% retention = rapid smooth settling)
            node.vx *= 0.78;
            node.vy *= 0.78;

            // Update position
            node.x += node.vx;
            node.y += node.vy;

            // Keep within visible canvas bounds
            node.x = Math.max(50, Math.min(850, node.x));
            node.y = Math.max(50, Math.min(530, node.y));
          }

          return updated;
        });

        // Decay alpha smoothly towards equilibrium
        alphaRef.current *= 0.985;
      }

      animFrameRef.current = requestAnimationFrame(simulate);
    };

    animFrameRef.current = requestAnimationFrame(simulate);

    return () => {
      running = false;
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    };
  }, [draggedNodeId, isPhysicsFrozen]);

  // Mouse / Drag Handlers with direct responsive positioning
  const handleNodeMouseDown = (e: React.MouseEvent, nodeId: string) => {
    e.stopPropagation();
    setDraggedNodeId(nodeId);
    setSelectedNodeId(nodeId);
    alphaRef.current = 0.25; // Awaken neighbors smoothly without jitter
  };

  const handleCanvasMouseDown = (e: React.MouseEvent) => {
    setIsPanningCanvas(true);
    setPanStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (draggedNodeId && canvasRef.current) {
      const rect = canvasRef.current.getBoundingClientRect();
      const clientX = (e.clientX - rect.left - pan.x) / zoom;
      const clientY = (e.clientY - rect.top - pan.y) / zoom;

      setNodes((prev) =>
        prev.map((n) =>
          n.id === draggedNodeId
            ? { ...n, x: clientX, y: clientY, vx: 0, vy: 0 }
            : n
        )
      );
    } else if (isPanningCanvas) {
      setPan({
        x: e.clientX - panStart.x,
        y: e.clientY - panStart.y,
      });
    }
  };

  const handleMouseUp = () => {
    setDraggedNodeId(null);
    setIsPanningCanvas(false);
  };

  // Connected node IDs for highlighted path
  const activeFocusId = hoveredNodeId || selectedNodeId;
  const connectedNodeIds = useMemo(() => {
    if (!activeFocusId) return new Set<string>();
    const set = new Set<string>([activeFocusId]);
    for (const e of edges) {
      if (e.source === activeFocusId) set.add(e.target);
      if (e.target === activeFocusId) set.add(e.source);
    }
    return set;
  }, [activeFocusId, edges]);

  // Selected Node Details
  const selectedNode = useMemo(
    () => nodes.find((n) => n.id === selectedNodeId) || null,
    [nodes, selectedNodeId]
  );

  const selectedNodeEdges = useMemo(() => {
    if (!selectedNodeId) return [];
    return edges.filter((e) => e.source === selectedNodeId || e.target === selectedNodeId);
  }, [selectedNodeId, edges]);

  // Filtered nodes based on Search & Category
  const visibleNodes = useMemo(() => {
    return nodes.filter((n) => {
      const matchCat = selectedCategory === 'all' || n.category === selectedCategory;
      const matchSearch =
        searchQuery === '' ||
        n.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
        n.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
        n.description.toLowerCase().includes(searchQuery.toLowerCase());
      return matchCat && matchSearch;
    });
  }, [nodes, selectedCategory, searchQuery]);

  const visibleNodeIdSet = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);

  const visibleEdges = useMemo(() => {
    return edges.filter((e) => visibleNodeIdSet.has(e.source) && visibleNodeIdSet.has(e.target));
  }, [edges, visibleNodeIdSet]);

  // Dispatch Quick Query into Sovereign Agent Chat
  const handleDispatchQuery = (node: GraphNode) => {
    addToast('info', `Routing query for ${node.label} to Sovereign Agent...`);
    setActiveTab('chat');
    window.dispatchEvent(
      new CustomEvent('workbench:preload-demo', {
        detail: {
          prompt: `Inspect technical specifications, telemetry thresholds, active failure modes, and compliance SOPs for ${node.label} (${node.id}). Cross-reference local ChromaDB manuals and generate recommendations.`,
          isMultimodal: false,
        },
      })
    );
  };

  // Count live documents
  const liveDocCount = useMemo(() => {
    return nodes.filter((n) => n.category === 'document').length;
  }, [nodes]);

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#0a0f1d] text-slate-100 select-none font-sans">
      {/* 1. Sleek Top Bar */}
      <div className="px-5 py-3.5 bg-[#0f172a]/95 backdrop-blur-md border-b border-slate-800 flex flex-col lg:flex-row lg:items-center justify-between gap-3 shrink-0 shadow-lg shadow-black/40 z-20">
        <div className="flex items-center gap-3">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-tr from-sky-500 to-indigo-600 flex items-center justify-center shadow-md shadow-sky-500/20">
            <Activity className="w-4 h-4 text-white" />
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-sm font-bold tracking-tight text-white flex items-center gap-2">
                Sovereign Knowledge Topology
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-sky-500/10 text-sky-400 border border-sky-500/20">
                  AIR-GAPPED
                </span>
              </h1>
              {liveDocCount > 0 && (
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-teal-500/10 text-teal-300 border border-teal-500/30 flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3 text-teal-400" />
                  <span>{liveDocCount} LIVE DOCS SYNCED</span>
                </span>
              )}
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              Force-directed industrial entity graph with RBAC gating &amp; live ChromaDB vector attachments.
            </p>
          </div>
        </div>

        {/* Clearance Level Switcher */}
        <div className="flex items-center gap-2">
          <div className="text-xs font-medium text-slate-400 flex items-center gap-1.5 mr-1">
            <Shield className="w-3.5 h-3.5 text-sky-400" />
            <span>Clearance:</span>
          </div>

          <div className="flex bg-slate-900/90 p-1 rounded-lg border border-slate-800">
            {[
              { key: 'viewer', label: 'L1: Viewer' },
              { key: 'operator', label: 'L2: Operator' },
              { key: 'admin', label: 'L3: Admin' },
            ].map((lvl) => {
              const isCurrent = selectedClearance === lvl.key;
              return (
                <button
                  key={lvl.key}
                  onClick={() => setSelectedClearance(lvl.key)}
                  className={`px-3 py-1 text-xs font-semibold rounded-md transition-all ${
                    isCurrent
                      ? 'bg-sky-600 text-white shadow-sm shadow-sky-600/50'
                      : 'text-slate-400 hover:text-slate-200 hover:bg-slate-800/50'
                  }`}
                >
                  {lvl.label}
                </button>
              );
            })}
          </div>

          {/* Sync Button */}
          <button
            onClick={() => {
              refreshDocuments();
              fetchGraph(selectedClearance);
              addToast('info', 'Synced graph with local ChromaDB repository.');
            }}
            className="p-1.5 rounded-lg bg-slate-900 border border-slate-800 text-slate-400 hover:text-white hover:border-slate-700 transition-all ml-1"
            title="Refresh & Sync Documents"
          >
            <RefreshCw className={`w-4 h-4 ${isLoading ? 'animate-spin text-sky-400' : ''}`} />
          </button>
        </div>
      </div>

      {/* 2. Sleek Filter Bar */}
      <div className="px-5 py-2 bg-[#0c1322] border-b border-slate-800/80 flex flex-wrap items-center justify-between gap-3 shrink-0 text-xs z-10">
        <div className="flex items-center gap-2.5">
          {/* Search Box */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search units, valves, sensors, SOPs..."
              className="pl-8 pr-3 py-1 bg-slate-900/80 border border-slate-800 rounded-md text-xs text-slate-200 placeholder:text-slate-500 focus:outline-none focus:border-sky-500 w-56 transition-all"
            />
          </div>

          {/* Category Filter Pills */}
          <div className="flex items-center gap-1 overflow-x-auto py-0.5">
            {[
              { id: 'all', label: 'All' },
              { id: 'unit', label: 'Units' },
              { id: 'equipment', label: 'Equipment' },
              { id: 'sensor', label: 'Sensors' },
              { id: 'defect', label: 'Defects' },
              { id: 'sop', label: 'SOPs' },
              { id: 'document', label: 'Live Docs' },
              { id: 'classified', label: 'Secret' },
            ].map((c) => (
              <button
                key={c.id}
                onClick={() => setSelectedCategory(c.id)}
                className={`px-2.5 py-1 text-[11px] font-medium rounded-md transition-all ${
                  selectedCategory === c.id
                    ? 'bg-sky-500/20 text-sky-300 border border-sky-500/40'
                    : 'bg-slate-900/60 text-slate-400 border border-transparent hover:border-slate-800 hover:text-slate-300'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>

        {/* Telemetry Stats & Physics Control */}
        <div className="flex items-center gap-3 text-[11px] text-slate-400">
          <span>Nodes: <strong className="text-sky-400 font-semibold">{visibleNodes.length}</strong></span>
          <span>Links: <strong className="text-emerald-400 font-semibold">{visibleEdges.length}</strong></span>
          {graphMeta && graphMeta.hidden_nodes > 0 && (
            <span className="text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20 flex items-center gap-1 font-semibold">
              <Lock className="w-3 h-3" />
              <span>{graphMeta.hidden_nodes} RBAC LOCKED</span>
            </span>
          )}

          {/* Freeze Layout Toggle */}
          <button
            onClick={() => {
              setIsPhysicsFrozen(!isPhysicsFrozen);
              alphaRef.current = isPhysicsFrozen ? 0.3 : 0.0;
            }}
            className={`px-2 py-0.5 rounded border text-[10px] font-semibold flex items-center gap-1 transition-all ${
              isPhysicsFrozen
                ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                : 'bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-600'
            }`}
            title={isPhysicsFrozen ? 'Release dynamic layout' : 'Freeze node positions'}
          >
            {isPhysicsFrozen ? <Play className="w-2.5 h-2.5" /> : <Pause className="w-2.5 h-2.5" />}
            <span>{isPhysicsFrozen ? 'FROZEN' : 'STABILIZED'}</span>
          </button>
        </div>
      </div>

      {/* 3. Main Interactive Canvas & Inspector Drawer */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Plain Minimalist Force Canvas (No Cluttered Grid!) */}
        <div
          ref={canvasRef}
          className="flex-1 relative bg-[#070b14] overflow-hidden cursor-crosshair select-none"
          onMouseDown={handleCanvasMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
        >
          {/* Floating Zoom Controls */}
          <div className="absolute top-4 left-4 z-10 flex flex-col gap-1 bg-slate-900/90 backdrop-blur border border-slate-800 rounded-lg p-1 shadow-lg shadow-black/50">
            <button
              onClick={() => setZoom((z) => Math.min(z + 0.15, 2.2))}
              className="p-1.5 hover:bg-slate-800 text-slate-300 hover:text-white rounded transition-colors"
              title="Zoom In"
            >
              <ZoomIn className="w-4 h-4" />
            </button>
            <button
              onClick={() => setZoom((z) => Math.max(z - 0.15, 0.4))}
              className="p-1.5 hover:bg-slate-800 text-slate-300 hover:text-white rounded transition-colors"
              title="Zoom Out"
            >
              <ZoomOut className="w-4 h-4" />
            </button>
            <button
              onClick={() => {
                setZoom(0.95);
                setPan({ x: 0, y: 0 });
                alphaRef.current = 0.5; // Re-center layout
              }}
              className="p-1.5 hover:bg-slate-800 text-slate-300 hover:text-white rounded transition-colors"
              title="Reset View"
            >
              <RotateCcw className="w-4 h-4" />
            </button>
          </div>

          {/* SVG Force-Directed Rendering */}
          <svg
            className="w-full h-full"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: 'center center',
            }}
          >
            <defs>
              {/* Arrowhead Markers */}
              <marker
                id="marker-arrow-clean"
                markerWidth="6"
                markerHeight="6"
                refX="18"
                refY="3"
                orient="auto"
              >
                <polygon points="0 0, 6 3, 0 6" fill="#334155" />
              </marker>
              <marker
                id="marker-arrow-active-clean"
                markerWidth="7"
                markerHeight="7"
                refX="20"
                refY="3.5"
                orient="auto"
              >
                <polygon points="0 0, 7 3.5, 0 7" fill="#38bdf8" />
              </marker>

              {/* Node Glow Filters */}
              <filter id="glow-highlight" x="-30%" y="-30%" width="160%" height="160%">
                <feGaussianBlur stdDeviation="6" result="blur" />
                <feComposite in="SourceGraphic" in2="blur" operator="over" />
              </filter>
            </defs>

            {/* Connecting Edges */}
            <g className="edges">
              {visibleEdges.map((edge, idx) => {
                const sourceNode = nodes.find((n) => n.id === edge.source);
                const targetNode = nodes.find((n) => n.id === edge.target);
                if (!sourceNode || !targetNode) return null;

                const isHighlighted =
                  activeFocusId &&
                  (edge.source === activeFocusId || edge.target === activeFocusId);
                const isDimmed = activeFocusId && !isHighlighted;

                const strokeColor = isHighlighted ? '#38bdf8' : '#1e293b';
                const strokeWidth = isHighlighted ? 2.2 : 1.2;

                return (
                  <g key={`${edge.source}-${edge.target}-${idx}`}>
                    <line
                      x1={sourceNode.x}
                      y1={sourceNode.y}
                      x2={targetNode.x}
                      y2={targetNode.y}
                      stroke={strokeColor}
                      strokeWidth={strokeWidth}
                      strokeOpacity={isDimmed ? 0.15 : isHighlighted ? 0.9 : 0.6}
                      strokeDasharray={edge.clearance === 'admin' ? '4 3' : 'none'}
                      markerEnd={isHighlighted ? 'url(#marker-arrow-active-clean)' : 'url(#marker-arrow-clean)'}
                    />

                    {/* Edge Label Pill */}
                    {(isHighlighted || zoom > 0.85) && (
                      <text
                        x={(sourceNode.x + targetNode.x) / 2}
                        y={(sourceNode.y + targetNode.y) / 2 - 3}
                        fill={isHighlighted ? '#38bdf8' : '#64748b'}
                        fontSize="8.5"
                        fontWeight="600"
                        textAnchor="middle"
                        opacity={isDimmed ? 0.15 : 0.85}
                        className="select-none pointer-events-none"
                        style={{
                          paintOrder: 'stroke',
                          stroke: '#070b14',
                          strokeWidth: '3px',
                        }}
                      >
                        {edge.label}
                      </text>
                    )}
                  </g>
                );
              })}
            </g>

            {/* Draggable Physical Nodes */}
            <g className="nodes">
              {visibleNodes.map((node) => {
                const isSelected = selectedNodeId === node.id;
                const isHovered = hoveredNodeId === node.id;
                const isConnected = connectedNodeIds.has(node.id);
                const isDimmed = activeFocusId && !isConnected;

                const colors = getNodeColor(node.category);
                const isStub = node.category === 'restricted_stub';
                const radius = node.radius || 18;

                return (
                  <g
                    key={node.id}
                    transform={`translate(${node.x}, ${node.y})`}
                    onMouseDown={(e) => handleNodeMouseDown(e, node.id)}
                    onMouseEnter={() => setHoveredNodeId(node.id)}
                    onMouseLeave={() => setHoveredNodeId(null)}
                    className="cursor-grab active:cursor-grabbing transition-opacity duration-150"
                    opacity={isDimmed ? 0.2 : 1}
                  >
                    {/* Soft Halo Glow */}
                    {(isSelected || isHovered) && (
                      <circle
                        r={radius + 10}
                        fill={colors.glow}
                        className="animate-pulse"
                      />
                    )}

                    {/* Pulsing Selection Ring */}
                    {isSelected && (
                      <circle
                        r={radius + 5}
                        fill="none"
                        stroke="#38bdf8"
                        strokeWidth="2"
                        strokeDasharray="3 2"
                      />
                    )}

                    {/* Main Node Body */}
                    <circle
                      r={radius}
                      fill={colors.fill}
                      stroke={isSelected || isHovered ? colors.stroke : '#0f172a'}
                      strokeWidth={isSelected || isHovered ? 2.5 : 1.5}
                      filter={isSelected ? 'url(#glow-highlight)' : undefined}
                    />

                    {/* Category Glyph / Icon */}
                    {isStub ? (
                      <Lock className="w-3 h-3 text-slate-400" transform="translate(-6, -6)" />
                    ) : node.category === 'document' ? (
                      <FileText className="w-3 h-3 text-white" transform="translate(-6, -6)" />
                    ) : node.category === 'unit' ? (
                      <Layers className="w-3 h-3 text-white" transform="translate(-6, -6)" />
                    ) : node.category === 'sensor' ? (
                      <Activity className="w-3 h-3 text-white" transform="translate(-6, -6)" />
                    ) : node.category === 'defect' ? (
                      <AlertTriangle className="w-3 h-3 text-white" transform="translate(-6, -6)" />
                    ) : node.category === 'sop' ? (
                      <FileCode className="w-3 h-3 text-white" transform="translate(-6, -6)" />
                    ) : node.category === 'classified' ? (
                      <Shield className="w-3 h-3 text-white" transform="translate(-6, -6)" />
                    ) : (
                      <Zap className="w-3 h-3 text-white" transform="translate(-6, -6)" />
                    )}

                    {/* Node Text Label */}
                    <text
                      x="0"
                      y={radius + 14}
                      fill={isSelected || isHovered ? '#ffffff' : '#94a3b8'}
                      fontSize={isSelected ? '10.5' : '9.5'}
                      fontWeight={isSelected ? '700' : '500'}
                      textAnchor="middle"
                      className="select-none pointer-events-none"
                      style={{
                        paintOrder: 'stroke',
                        stroke: '#070b14',
                        strokeWidth: '3px',
                      }}
                    >
                      {node.label.length > 26 ? `${node.label.slice(0, 24)}…` : node.label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>

        {/* 4. Sleek Entity Inspector Sidebar */}
        <div className="w-84 lg:w-96 bg-[#0f172a]/95 backdrop-blur-md border-l border-slate-800 flex flex-col justify-between overflow-hidden shadow-2xl z-20 shrink-0">
          {selectedNode ? (
            <div className="flex-1 flex flex-col h-full overflow-hidden">
              {/* Header */}
              <div className="p-4 border-b border-slate-800/80 bg-slate-900/50">
                <div className="flex items-center justify-between gap-2 mb-2">
                  <span className={`text-[10px] font-bold px-2 py-0.5 rounded border uppercase ${getNodeColor(selectedNode.category).tagBg}`}>
                    {getNodeColor(selectedNode.category).badge}
                  </span>
                  <span className="text-[10px] font-mono text-slate-400 uppercase flex items-center gap-1">
                    <Shield className="w-3 h-3 text-sky-400" />
                    <span>{selectedNode.clearance.toUpperCase()} CLEARANCE</span>
                  </span>
                </div>

                <h2 className="text-sm font-bold text-white tracking-tight leading-snug">
                  {selectedNode.label}
                </h2>
                <span className="text-[11px] font-mono text-sky-400 block mt-0.5">
                  ID: {selectedNode.id}
                </span>
              </div>

              {/* Scrollable Details */}
              <div className="flex-1 overflow-y-auto p-4 space-y-4 text-xs">
                {/* Description */}
                <div>
                  <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1">
                    Entity Overview
                  </h3>
                  <p className="text-slate-300 leading-relaxed bg-slate-900/80 p-2.5 rounded-lg border border-slate-800/80">
                    {selectedNode.description}
                  </p>
                </div>

                {/* Properties Table */}
                <div>
                  <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 flex items-center justify-between">
                    <span>Telemetry &amp; Attributes</span>
                    <span className="text-[10px] text-slate-500 font-mono">
                      {Object.keys(selectedNode.properties).length} KEYS
                    </span>
                  </h3>
                  <div className="bg-slate-900/80 rounded-lg border border-slate-800 divide-y divide-slate-800/60 overflow-hidden">
                    {Object.entries(selectedNode.properties).map(([k, v]) => (
                      <div key={k} className="p-2 flex items-center justify-between text-[11px]">
                        <span className="font-mono text-slate-400">{k}</span>
                        <span className="font-semibold text-slate-200 text-right max-w-[55%] truncate font-mono">
                          {v}
                        </span>
                      </div>
                    ))}
                  </div>
                </div>

                {/* Connected Relationships */}
                <div>
                  <h3 className="text-[11px] font-semibold text-slate-400 uppercase tracking-wider mb-1.5 flex items-center justify-between">
                    <span>Relational Pathways</span>
                    <span className="text-[10px] text-slate-500 font-mono">
                      {selectedNodeEdges.length} LINKS
                    </span>
                  </h3>

                  <div className="space-y-1.5">
                    {selectedNodeEdges.map((e, idx) => {
                      const isSource = e.source === selectedNode.id;
                      const targetId = isSource ? e.target : e.source;
                      const targetNode = nodes.find((n) => n.id === targetId);

                      return (
                        <div
                          key={idx}
                          onClick={() => targetNode && setSelectedNodeId(targetNode.id)}
                          className="p-2 rounded-lg bg-slate-900/60 hover:bg-slate-850 border border-slate-800/70 hover:border-sky-500/40 cursor-pointer flex items-center justify-between gap-2 transition-all"
                        >
                          <div className="min-w-0">
                            <span className="text-[10px] font-mono text-sky-400 block truncate">
                              {e.label} {isSource ? '→' : '←'}
                            </span>
                            <span className="text-[11px] font-medium text-slate-200 truncate block">
                              {targetNode ? targetNode.label : targetId}
                            </span>
                          </div>
                          <ArrowRight className="w-3 h-3 text-slate-500 shrink-0" />
                        </div>
                      );
                    })}
                  </div>
                </div>
              </div>

              {/* Action Bar */}
              <div className="p-3.5 bg-slate-900/90 border-t border-slate-800">
                <button
                  onClick={() => handleDispatchQuery(selectedNode)}
                  className="w-full py-2 px-3 bg-gradient-to-r from-sky-600 to-indigo-600 hover:from-sky-500 hover:to-indigo-500 text-white text-xs font-semibold rounded-lg shadow-md shadow-sky-600/30 flex items-center justify-center gap-2 transition-all"
                >
                  <Sparkles className="w-3.5 h-3.5 text-sky-200" />
                  <span>Ask Sovereign Agent About Entity</span>
                </button>
              </div>
            </div>
          ) : (
            <div className="flex-1 flex flex-col items-center justify-center p-6 text-center text-slate-500">
              <Database className="w-8 h-8 mb-2 opacity-40 text-slate-400" />
              <p className="text-xs font-medium text-slate-400">Select any node on the canvas</p>
              <p className="text-[11px] text-slate-600 mt-1 max-w-[200px]">
                Click or drag any unit, equipment, or live document node to view properties.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
