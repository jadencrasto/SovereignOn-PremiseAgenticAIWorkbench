/**
 * frontend/src/components/graph/CompanyKnowledgeGraph.tsx
 * --------------------------------------------------------
 * High-Clarity Sovereign Knowledge Graph & Dual Role-Based Database Matrix.
 * 
 * Features:
 * - Ultra-Crisp Non-Blurred SVG Topology (No blur filters, pixel-perfect centered icons)
 * - Legible High-Contrast Floating Entity Labels with Badge Containers
 * - Role-Based Database Table View (RBAC Matrix with live clearance filtering)
 * - Smooth Physics with Kinetic Cooling (No Juggling / Jitter)
 * - Live Ingested ChromaDB Document Integration
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
  Table,
  Network,
  Eye,
  Key,
  Flame,
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

  // View Mode: 'graph' or 'database'
  const [viewMode, setViewMode] = useState<'graph' | 'database'>('graph');

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
  const [zoom, setZoom] = useState<number>(0.92);
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  // Dragging State
  const [draggedNodeId, setDraggedNodeId] = useState<string | null>(null);
  const [isPanningCanvas, setIsPanningCanvas] = useState<boolean>(false);
  const [panStart, setPanStart] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const canvasRef = useRef<HTMLDivElement>(null);
  const animFrameRef = useRef<number | null>(null);
  const nodesRef = useRef<GraphNode[]>([]);
  const edgesRef = useRef<GraphEdge[]>([]);
  const alphaRef = useRef<number>(1.0);

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
        ring: '#64748b',
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
          ring: '#0284c7',
          text: '#ffffff',
          tagBg: 'bg-sky-950/90 text-sky-300 border-sky-700',
          badge: 'UNIT',
        };
      case 'equipment':
        return {
          fill: '#2563eb',
          stroke: '#60a5fa',
          ring: '#2563eb',
          text: '#ffffff',
          tagBg: 'bg-blue-950/90 text-blue-300 border-blue-700',
          badge: 'EQUIPMENT',
        };
      case 'sensor':
        return {
          fill: '#059669',
          stroke: '#34d399',
          ring: '#059669',
          text: '#ffffff',
          tagBg: 'bg-emerald-950/90 text-emerald-300 border-emerald-700',
          badge: 'SENSOR',
        };
      case 'defect':
        return {
          fill: '#d97706',
          stroke: '#fbbf24',
          ring: '#d97706',
          text: '#ffffff',
          tagBg: 'bg-amber-950/90 text-amber-300 border-amber-700',
          badge: 'DEFECT',
        };
      case 'sop':
        return {
          fill: '#7c3aed',
          stroke: '#c084fc',
          ring: '#7c3aed',
          text: '#ffffff',
          tagBg: 'bg-purple-950/90 text-purple-300 border-purple-700',
          badge: 'SOP',
        };
      case 'document':
        return {
          fill: '#0d9488',
          stroke: '#2dd4bf',
          ring: '#0d9488',
          text: '#ffffff',
          tagBg: 'bg-teal-950/90 text-teal-300 border-teal-600',
          badge: 'LIVE DOC',
        };
      case 'classified':
        return {
          fill: '#dc2626',
          stroke: '#f87171',
          ring: '#dc2626',
          text: '#ffffff',
          tagBg: 'bg-rose-950/90 text-rose-300 border-rose-700',
          badge: 'CLASSIFIED',
        };
      default:
        return {
          fill: '#334155',
          stroke: '#64748b',
          ring: '#334155',
          text: '#ffffff',
          tagBg: 'bg-slate-900 text-slate-300 border-slate-700',
          badge: 'ENTITY',
        };
    }
  }, []);

  // Fetch Graph & Database Data
  const fetchGraph = async (clearance: string) => {
    setIsLoading(true);
    try {
      const res = await fetch(`/api/knowledge-graph?clearance=${clearance}`);
      if (res.ok) {
        const data: GraphResponse = await res.json();
        setGraphMeta(data);

        // Center Coordinate Space
        const width = 940;
        const height = 600;
        const cx = width / 2;
        const cy = height / 2;

        const count = data.nodes.length;
        const initializedNodes: GraphNode[] = data.nodes.map((node, i) => {
          let dist = 180;
          if (node.category === 'unit') dist = 90;
          else if (node.category === 'document') dist = 145;
          else if (node.category === 'equipment') dist = 185;
          else if (node.category === 'sensor') dist = 250;
          else if (node.category === 'defect') dist = 280;
          else if (node.category === 'sop') dist = 220;
          else if (node.category === 'classified' || node.category === 'restricted_stub') dist = 310;

          const angle = (i / count) * 2 * Math.PI - Math.PI / 2;
          return {
            ...node,
            x: cx + dist * Math.cos(angle) + (Math.sin(i * 3) * 12),
            y: cy + dist * Math.sin(angle) + (Math.cos(i * 2) * 12),
            vx: 0,
            vy: 0,
            radius: node.category === 'unit' ? 24 : node.category === 'document' ? 22 : node.category === 'equipment' ? 20 : 18,
          };
        });

        setNodes(initializedNodes);
        setEdges(data.edges);
        alphaRef.current = 1.0;

        if (!selectedNodeId && initializedNodes.length > 0) {
          setSelectedNodeId(initializedNodes[0].id);
        }
      } else {
        addToast('error', 'Failed to load graph data.');
      }
    } catch {
      addToast('error', 'Error connecting to knowledge graph service.');
    } finally {
      setIsLoading(false);
    }
  };

  useEffect(() => {
    fetchGraph(selectedClearance);
  }, [selectedClearance]);

  useEffect(() => {
    if (documents.length > 0) {
      fetchGraph(selectedClearance);
    }
  }, [documents.length]);

  // Smooth Force Simulation with Quick Alpha Cooling
  useEffect(() => {
    let running = true;

    const simulate = () => {
      if (!running) return;

      if (!isPhysicsFrozen && alphaRef.current > 0.003) {
        setNodes((prevNodes) => {
          if (prevNodes.length === 0) return prevNodes;

          const updated = prevNodes.map((n) => ({ ...n }));
          const nodeMap = new Map(updated.map((n) => [n.id, n]));
          const cx = 470;
          const cy = 300;
          const currentAlpha = alphaRef.current;

          // 1. Soft Repulsion
          for (let i = 0; i < updated.length; i++) {
            for (let j = i + 1; j < updated.length; j++) {
              const n1 = updated[i];
              const n2 = updated[j];
              const dx = n2.x - n1.x;
              const dy = n2.y - n1.y;
              const distSq = dx * dx + dy * dy || 1;
              const dist = Math.sqrt(distSq);

              if (dist < 280) {
                const force = ((280 - dist) / (distSq + 500)) * 36 * currentAlpha;
                const fx = Math.max(-3.0, Math.min(3.0, (dx / dist) * force));
                const fy = Math.max(-3.0, Math.min(3.0, (dy / dist) * force));

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
              const targetDist = 130;
              const force = (dist - targetDist) * 0.02 * currentAlpha;
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

          // 3. Center Gravity & Velocity Damping
          for (const node of updated) {
            if (node.id === draggedNodeId) continue;

            const dx = cx - node.x;
            const dy = cy - node.y;
            node.vx += dx * 0.002 * currentAlpha;
            node.vy += dy * 0.002 * currentAlpha;

            node.vx *= 0.76;
            node.vy *= 0.76;

            node.x += node.vx;
            node.y += node.vy;

            node.x = Math.max(50, Math.min(890, node.x));
            node.y = Math.max(50, Math.min(550, node.y));
          }

          return updated;
        });

        alphaRef.current *= 0.982;
      }

      animFrameRef.current = requestAnimationFrame(simulate);
    };

    animFrameRef.current = requestAnimationFrame(simulate);

    return () => {
      running = false;
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    };
  }, [draggedNodeId, isPhysicsFrozen]);

  // Drag Handlers
  const handleNodeMouseDown = (e: React.MouseEvent, nodeId: string) => {
    e.stopPropagation();
    setDraggedNodeId(nodeId);
    setSelectedNodeId(nodeId);
    alphaRef.current = 0.25;
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

  const liveDocCount = useMemo(() => {
    return nodes.filter((n) => n.category === 'document').length;
  }, [nodes]);

  // Render centered icon helper
  const renderNodeIcon = (category: string) => {
    const iconClass = "w-5 h-5 text-white";
    switch (category) {
      case 'unit':
        return <Layers className={iconClass} />;
      case 'equipment':
        return <Flame className={iconClass} />;
      case 'sensor':
        return <Activity className={iconClass} />;
      case 'defect':
        return <AlertTriangle className={iconClass} />;
      case 'sop':
        return <FileCode className={iconClass} />;
      case 'document':
        return <FileText className={iconClass} />;
      case 'classified':
        return <Key className={iconClass} />;
      case 'restricted_stub':
        return <Lock className="w-4 h-4 text-slate-400" />;
      default:
        return <Zap className={iconClass} />;
    }
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#070b14] text-slate-100 select-none font-sans">
      {/* 1. Header Toolbar */}
      <div className="px-5 py-3 bg-[#0d1424] border-b border-slate-800 flex flex-col lg:flex-row lg:items-center justify-between gap-3 shrink-0 shadow-lg z-20">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-xl bg-gradient-to-tr from-sky-500 to-indigo-600 flex items-center justify-center shadow-md shadow-sky-500/25">
            {viewMode === 'graph' ? (
              <Network className="w-5 h-5 text-white" />
            ) : (
              <Database className="w-5 h-5 text-white" />
            )}
          </div>
          <div>
            <div className="flex items-center gap-2">
              <h1 className="text-sm font-bold tracking-tight text-white flex items-center gap-2">
                Sovereign Knowledge Topology &amp; RBAC Database
              </h1>
              {liveDocCount > 0 && (
                <span className="text-[10px] font-semibold px-2 py-0.5 rounded-full bg-teal-500/10 text-teal-300 border border-teal-500/30 flex items-center gap-1">
                  <CheckCircle2 className="w-3 h-3 text-teal-400" />
                  <span>{liveDocCount} LIVE DOCS SYNCED</span>
                </span>
              )}
            </div>
            <p className="text-xs text-slate-400 mt-0.5">
              100% On-Premise Air-Gapped Relational Topology &bull; Role-Based Clearance Gating
            </p>
          </div>
        </div>

        {/* View Switcher & Clearance Controls */}
        <div className="flex items-center gap-2.5">
          {/* Toggle View Mode: Graph vs Database */}
          <div className="flex bg-slate-900 p-1 rounded-lg border border-slate-800">
            <button
              onClick={() => setViewMode('graph')}
              className={`px-3 py-1 text-xs font-semibold rounded-md flex items-center gap-1.5 transition-all ${
                viewMode === 'graph'
                  ? 'bg-sky-600 text-white shadow-sm shadow-sky-600/40'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Network className="w-3.5 h-3.5" />
              <span>Graph View</span>
            </button>
            <button
              onClick={() => setViewMode('database')}
              className={`px-3 py-1 text-xs font-semibold rounded-md flex items-center gap-1.5 transition-all ${
                viewMode === 'database'
                  ? 'bg-sky-600 text-white shadow-sm shadow-sky-600/40'
                  : 'text-slate-400 hover:text-slate-200'
              }`}
            >
              <Table className="w-3.5 h-3.5" />
              <span>RBAC Database View</span>
            </button>
          </div>

          {/* Clearance Level Switcher */}
          <div className="flex items-center gap-1.5 pl-2 border-l border-slate-800">
            <div className="text-xs font-medium text-slate-400 flex items-center gap-1 mr-1">
              <Shield className="w-3.5 h-3.5 text-sky-400" />
              <span>Clearance:</span>
            </div>

            <div className="flex bg-slate-900 p-1 rounded-lg border border-slate-800">
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
                    className={`px-2.5 py-1 text-xs font-semibold rounded-md transition-all ${
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

            {/* Refresh Button */}
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
      </div>

      {/* 2. Filter Bar */}
      <div className="px-5 py-2.5 bg-[#0a101f] border-b border-slate-800 flex flex-wrap items-center justify-between gap-3 shrink-0 text-xs z-10">
        <div className="flex items-center gap-2.5">
          {/* Search Box */}
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-500 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search assets, telemetry, SOPs..."
              className="pl-8 pr-3 py-1.5 bg-slate-900 border border-slate-800 rounded-md text-xs text-slate-100 placeholder:text-slate-500 focus:outline-none focus:border-sky-500 w-60 transition-all font-sans"
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
                className={`px-2.5 py-1 text-[11px] font-semibold rounded-md transition-all ${
                  selectedCategory === c.id
                    ? 'bg-sky-500/20 text-sky-300 border border-sky-500/50'
                    : 'bg-slate-900/80 text-slate-400 border border-slate-800 hover:border-slate-700 hover:text-slate-200'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>

        {/* Telemetry Stats & Physics Control */}
        <div className="flex items-center gap-3 text-[11px] text-slate-400 font-medium">
          <span>Visible Entities: <strong className="text-sky-400 font-semibold">{visibleNodes.length}</strong></span>
          <span>Active Links: <strong className="text-emerald-400 font-semibold">{visibleEdges.length}</strong></span>
          {graphMeta && graphMeta.hidden_nodes > 0 && (
            <span className="text-amber-400 bg-amber-500/10 px-2 py-0.5 rounded border border-amber-500/20 flex items-center gap-1 font-semibold">
              <Lock className="w-3 h-3" />
              <span>{graphMeta.hidden_nodes} RBAC LOCKED</span>
            </span>
          )}

          {viewMode === 'graph' && (
            <button
              onClick={() => {
                setIsPhysicsFrozen(!isPhysicsFrozen);
                alphaRef.current = isPhysicsFrozen ? 0.35 : 0.0;
              }}
              className={`px-2 py-0.5 rounded border text-[10px] font-semibold flex items-center gap-1 transition-all ${
                isPhysicsFrozen
                  ? 'bg-amber-500/20 text-amber-300 border-amber-500/30'
                  : 'bg-slate-800 text-slate-300 border-slate-700 hover:border-slate-600'
              }`}
              title={isPhysicsFrozen ? 'Release layout physics' : 'Freeze node positions'}
            >
              {isPhysicsFrozen ? <Play className="w-2.5 h-2.5" /> : <Pause className="w-2.5 h-2.5" />}
              <span>{isPhysicsFrozen ? 'FROZEN' : 'STABILIZED'}</span>
            </button>
          )}
        </div>
      </div>

      {/* 3. MAIN CONTENT: Switch between Graph View and RBAC Database View */}
      {viewMode === 'graph' ? (
        <div className="flex-1 flex overflow-hidden relative">
          {/* Crisp, Non-Blurred Force-Directed Canvas */}
          <div
            ref={canvasRef}
            className="flex-1 relative bg-[#070b14] overflow-hidden cursor-crosshair select-none"
            onMouseDown={handleCanvasMouseDown}
            onMouseMove={handleMouseMove}
            onMouseUp={handleMouseUp}
          >
            {/* Zoom Controls */}
            <div className="absolute top-4 left-4 z-10 flex flex-col gap-1 bg-slate-900/90 backdrop-blur border border-slate-800 rounded-lg p-1 shadow-xl">
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
                  setZoom(0.92);
                  setPan({ x: 0, y: 0 });
                  alphaRef.current = 0.5;
                }}
                className="p-1.5 hover:bg-slate-800 text-slate-300 hover:text-white rounded transition-colors"
                title="Reset View"
              >
                <RotateCcw className="w-4 h-4" />
              </button>
            </div>

            {/* SVG Force-Directed Rendering (Crisp, High-Clarity, Zero Blur Filters) */}
            <svg
              className="w-full h-full"
              style={{
                transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
                transformOrigin: 'center center',
              }}
            >
              <defs>
                <marker
                  id="marker-arrow-solid"
                  markerWidth="7"
                  markerHeight="7"
                  refX="22"
                  refY="3.5"
                  orient="auto"
                >
                  <polygon points="0 0, 7 3.5, 0 7" fill="#475569" />
                </marker>
                <marker
                  id="marker-arrow-active-solid"
                  markerWidth="8"
                  markerHeight="8"
                  refX="24"
                  refY="4"
                  orient="auto"
                >
                  <polygon points="0 0, 8 4, 0 8" fill="#38bdf8" />
                </marker>
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

                  const strokeColor = isHighlighted ? '#38bdf8' : '#334155';
                  const strokeWidth = isHighlighted ? 2.5 : 1.4;

                  return (
                    <g key={`${edge.source}-${edge.target}-${idx}`}>
                      <line
                        x1={sourceNode.x}
                        y1={sourceNode.y}
                        x2={targetNode.x}
                        y2={targetNode.y}
                        stroke={strokeColor}
                        strokeWidth={strokeWidth}
                        strokeOpacity={isDimmed ? 0.35 : isHighlighted ? 1.0 : 0.75}
                        strokeDasharray={edge.clearance === 'admin' ? '5 3' : 'none'}
                        markerEnd={isHighlighted ? 'url(#marker-arrow-active-solid)' : 'url(#marker-arrow-solid)'}
                      />

                      {/* Edge Label */}
                      <text
                        x={(sourceNode.x + targetNode.x) / 2}
                        y={(sourceNode.y + targetNode.y) / 2 - 4}
                        fill={isHighlighted ? '#38bdf8' : '#94a3b8'}
                        fontSize="9"
                        fontWeight="700"
                        textAnchor="middle"
                        opacity={isDimmed ? 0.35 : 0.95}
                        className="select-none pointer-events-none"
                        style={{
                          paintOrder: 'stroke',
                          stroke: '#070b14',
                          strokeWidth: '3.5px',
                        }}
                      >
                        {edge.label}
                      </text>
                    </g>
                  );
                })}
              </g>

              {/* Physical Interactive Nodes */}
              <g className="nodes">
                {visibleNodes.map((node) => {
                  const isSelected = selectedNodeId === node.id;
                  const isHovered = hoveredNodeId === node.id;
                  const isConnected = connectedNodeIds.has(node.id);
                  const isDimmed = activeFocusId && !isConnected;

                  const colors = getNodeColor(node.category);
                  const radius = node.radius || 20;

                  return (
                    <g
                      key={node.id}
                      transform={`translate(${node.x}, ${node.y})`}
                      onMouseDown={(e) => handleNodeMouseDown(e, node.id)}
                      onMouseEnter={() => setHoveredNodeId(node.id)}
                      onMouseLeave={() => setHoveredNodeId(null)}
                      className="cursor-grab active:cursor-grabbing"
                      opacity={isDimmed ? 0.55 : 1}
                    >
                      {/* Active Ring */}
                      {(isSelected || isHovered) && (
                        <circle
                          r={radius + 6}
                          fill="none"
                          stroke={colors.stroke}
                          strokeWidth="2.5"
                          strokeDasharray="4 2"
                        />
                      )}

                      {/* Main Node Circle */}
                      <circle
                        r={radius}
                        fill={colors.fill}
                        stroke={isSelected || isHovered ? '#ffffff' : colors.stroke}
                        strokeWidth={isSelected || isHovered ? 3 : 2}
                      />

                      {/* Perfectly Centered Icon via ForeignObject */}
                      <foreignObject
                        x={-radius}
                        y={-radius}
                        width={radius * 2}
                        height={radius * 2}
                        className="pointer-events-none"
                      >
                        <div className="flex items-center justify-center w-full h-full">
                          {renderNodeIcon(node.category)}
                        </div>
                      </foreignObject>

                      {/* Crisp, Fully Legible Floating Label Badge */}
                      <foreignObject
                        x={-80}
                        y={radius + 4}
                        width={160}
                        height={32}
                        className="overflow-visible pointer-events-none"
                      >
                        <div className="flex justify-center w-full">
                          <span
                            className={`px-2 py-0.5 rounded-md text-[11px] font-semibold text-center truncate max-w-[150px] shadow-lg border ${
                              isSelected
                                ? 'bg-sky-950 text-white border-sky-400 font-bold ring-1 ring-sky-400'
                                : 'bg-[#0f172a]/95 text-slate-100 border-slate-700'
                            }`}
                          >
                            {node.label}
                          </span>
                        </div>
                      </foreignObject>
                    </g>
                  );
                })}
              </g>
            </svg>
          </div>

          {/* 4. Entity Inspector Sidebar */}
          <div className="w-84 lg:w-96 bg-[#0c1322] border-l border-slate-800 flex flex-col justify-between overflow-hidden shadow-2xl z-20 shrink-0">
            {selectedNode ? (
              <div className="flex-1 flex flex-col h-full overflow-hidden">
                {/* Header */}
                <div className="p-4 border-b border-slate-800 bg-slate-900/70">
                  <div className="flex items-center justify-between gap-2 mb-2">
                    <span className={`text-[10px] font-bold px-2 py-0.5 rounded border uppercase ${getNodeColor(selectedNode.category).tagBg}`}>
                      {getNodeColor(selectedNode.category).badge}
                    </span>
                    <span className="text-[10px] font-mono text-slate-300 uppercase flex items-center gap-1">
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
                  {/* Overview */}
                  <div>
                    <h3 className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1">
                      Entity Specification
                    </h3>
                    <p className="text-slate-200 leading-relaxed bg-slate-900/90 p-3 rounded-lg border border-slate-800">
                      {selectedNode.description}
                    </p>
                  </div>

                  {/* Telemetry Properties */}
                  <div>
                    <h3 className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1.5 flex items-center justify-between">
                      <span>Telemetry &amp; Operating Attributes</span>
                      <span className="text-[10px] text-slate-500 font-mono">
                        {Object.keys(selectedNode.properties).length} ATTRIBUTES
                      </span>
                    </h3>
                    <div className="bg-slate-900/90 rounded-lg border border-slate-800 divide-y divide-slate-800 overflow-hidden">
                      {Object.entries(selectedNode.properties).map(([k, v]) => (
                        <div key={k} className="p-2.5 flex items-center justify-between text-[11px]">
                          <span className="font-mono text-slate-400">{k}</span>
                          <span className="font-semibold text-sky-300 text-right max-w-[55%] truncate font-mono">
                            {v}
                          </span>
                        </div>
                      ))}
                    </div>
                  </div>

                  {/* Connected Pathways */}
                  <div>
                    <h3 className="text-[11px] font-bold text-slate-400 uppercase tracking-wider mb-1.5 flex items-center justify-between">
                      <span>Connected Topology ({selectedNodeEdges.length})</span>
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
                            className="p-2 rounded-lg bg-slate-900 hover:bg-slate-850 border border-slate-800 hover:border-sky-500/50 cursor-pointer flex items-center justify-between gap-2 transition-all"
                          >
                            <div className="min-w-0">
                              <span className="text-[10px] font-mono text-sky-400 block truncate font-semibold">
                                {e.label} {isSource ? '→' : '←'}
                              </span>
                              <span className="text-[11px] font-medium text-slate-200 truncate block">
                                {targetNode ? targetNode.label : targetId}
                              </span>
                            </div>
                            <ArrowRight className="w-3 h-3 text-slate-400 shrink-0" />
                          </div>
                        );
                      })}
                    </div>
                  </div>
                </div>

                {/* Action Bar */}
                <div className="p-3.5 bg-slate-900/95 border-t border-slate-800">
                  <button
                    onClick={() => handleDispatchQuery(selectedNode)}
                    className="w-full py-2.5 px-3 bg-gradient-to-r from-sky-600 to-indigo-600 hover:from-sky-500 hover:to-indigo-500 text-white text-xs font-semibold rounded-lg shadow-md shadow-sky-600/30 flex items-center justify-center gap-2 transition-all"
                  >
                    <Sparkles className="w-3.5 h-3.5 text-sky-200" />
                    <span>Query Sovereign Agent About Entity</span>
                  </button>
                </div>
              </div>
            ) : (
              <div className="flex-1 flex flex-col items-center justify-center p-6 text-center text-slate-500">
                <Database className="w-8 h-8 mb-2 opacity-40 text-slate-400" />
                <p className="text-xs font-medium text-slate-300">Select any entity on the canvas</p>
                <p className="text-[11px] text-slate-500 mt-1 max-w-[200px]">
                  Click or drag any node to view real-time attributes and telemetry.
                </p>
              </div>
            )}
          </div>
        </div>
      ) : (
        /* 5. ROLE-BASED DATABASE VIEW (Enterprise RBAC Matrix Table) */
        <div className="flex-1 overflow-y-auto p-6 bg-[#070b14]">
          <div className="max-w-7xl mx-auto space-y-4">
            {/* Table Header Info */}
            <div className="flex items-center justify-between bg-slate-900/80 p-4 rounded-xl border border-slate-800 shadow-lg">
              <div>
                <h2 className="text-sm font-bold text-white flex items-center gap-2">
                  <Database className="w-4 h-4 text-sky-400" />
                  <span>Enterprise Relational Database Matrix</span>
                </h2>
                <p className="text-xs text-slate-400 mt-0.5">
                  Full tabular record view of company plant units, mechanical assets, telemetry probes, failure modes, and live documents.
                </p>
              </div>
              <div className="flex items-center gap-2 text-xs">
                <span className="text-slate-400">Current Role Filter:</span>
                <span className="px-2.5 py-1 rounded bg-sky-500/20 text-sky-300 font-bold border border-sky-500/40 uppercase">
                  {selectedClearance.toUpperCase()}
                </span>
              </div>
            </div>

            {/* Main Data Table */}
            <div className="rounded-xl border border-slate-800 bg-slate-900/60 overflow-hidden shadow-xl">
              <table className="w-full text-left text-xs border-collapse font-sans">
                <thead className="bg-slate-800/90 text-slate-300 font-semibold border-b border-slate-700">
                  <tr>
                    <th className="py-3 px-4">Entity ID &amp; Tag</th>
                    <th className="py-3 px-4">Name &amp; Description</th>
                    <th className="py-3 px-3">Category</th>
                    <th className="py-3 px-3">RBAC Tier</th>
                    <th className="py-3 px-4">Telemetry / Attributes</th>
                    <th className="py-3 px-3 text-right">Actions</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-slate-800/80 text-slate-200">
                  {visibleNodes.map((n) => {
                    const colors = getNodeColor(n.category);
                    const isRestricted = n.category === 'restricted_stub';

                    return (
                      <tr key={n.id} className="hover:bg-slate-800/40 transition-colors">
                        {/* ID */}
                        <td className="py-3 px-4 font-mono font-bold text-sky-400 whitespace-nowrap">
                          {n.id}
                        </td>

                        {/* Name & Desc */}
                        <td className="py-3 px-4 max-w-xs">
                          <div className="font-semibold text-white truncate">{n.label}</div>
                          <div className="text-[11px] text-slate-400 line-clamp-1 mt-0.5">
                            {n.description}
                          </div>
                        </td>

                        {/* Category */}
                        <td className="py-3 px-3 whitespace-nowrap">
                          <span className={`text-[10px] font-bold px-2 py-0.5 rounded border uppercase ${colors.tagBg}`}>
                            {colors.badge}
                          </span>
                        </td>

                        {/* RBAC Tier */}
                        <td className="py-3 px-3 whitespace-nowrap">
                          <span
                            className={`text-[10px] font-mono font-bold px-2 py-0.5 rounded border uppercase flex items-center gap-1 w-fit ${
                              n.clearance === 'admin'
                                ? 'bg-rose-950/80 text-rose-300 border-rose-800'
                                : n.clearance === 'operator'
                                ? 'bg-amber-950/80 text-amber-300 border-amber-800'
                                : 'bg-emerald-950/80 text-emerald-300 border-emerald-800'
                            }`}
                          >
                            <Shield className="w-2.5 h-2.5" />
                            <span>{n.clearance.toUpperCase()}</span>
                          </span>
                        </td>

                        {/* Telemetry Attributes */}
                        <td className="py-3 px-4">
                          <div className="flex flex-wrap gap-1 max-w-sm">
                            {Object.entries(n.properties).slice(0, 3).map(([k, v]) => (
                              <span
                                key={k}
                                className="px-1.5 py-0.5 rounded bg-slate-950 border border-slate-800 text-[10px] font-mono text-slate-300 truncate"
                              >
                                <strong className="text-slate-400">{k}:</strong> {v}
                              </span>
                            ))}
                            {Object.keys(n.properties).length > 3 && (
                              <span className="text-[10px] text-slate-500 font-mono">
                                +{Object.keys(n.properties).length - 3} more
                              </span>
                            )}
                          </div>
                        </td>

                        {/* Actions */}
                        <td className="py-3 px-3 text-right whitespace-nowrap">
                          {!isRestricted ? (
                            <button
                              onClick={() => handleDispatchQuery(n)}
                              className="px-2.5 py-1 rounded bg-sky-600/20 hover:bg-sky-600 text-sky-300 hover:text-white border border-sky-500/40 text-[11px] font-semibold transition-all inline-flex items-center gap-1"
                            >
                              <Sparkles className="w-3 h-3" />
                              <span>Query Agent</span>
                            </button>
                          ) : (
                            <span className="text-[10px] font-mono text-slate-500 flex items-center justify-end gap-1">
                              <Lock className="w-3 h-3 text-amber-400" />
                              <span>LOCKED</span>
                            </span>
                          )}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
