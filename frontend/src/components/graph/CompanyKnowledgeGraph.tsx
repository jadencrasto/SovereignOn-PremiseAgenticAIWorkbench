/**
 * frontend/src/components/graph/CompanyKnowledgeGraph.tsx
 * --------------------------------------------------------
 * High-Performance Interactive Force-Directed Draggable Knowledge Graph.
 * Supports smooth node dragging with continuous physics, hover path illumination,
 * pan & zoom canvas, and RBAC authorization filtering.
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
  Maximize2,
  Send,
  ChevronRight,
  Move,
  Play,
  RotateCcw,
} from 'lucide-react';

interface GraphNode {
  id: string;
  label: string;
  category: 'unit' | 'equipment' | 'sensor' | 'defect' | 'sop' | 'classified' | 'restricted_stub';
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
  const { setActiveTab, addToast } = useWorkbench();

  const [selectedClearance, setSelectedClearance] = useState<string>(userAuthRole || 'viewer');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null);
  const [hoveredNodeId, setHoveredNodeId] = useState<string | null>(null);

  const [nodes, setNodes] = useState<GraphNode[]>([]);
  const [edges, setEdges] = useState<GraphEdge[]>([]);
  const [graphMeta, setGraphMeta] = useState<GraphResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  // Pan & Zoom
  const [zoom, setZoom] = useState<number>(1.0);
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  // Dragging State
  const [draggedNodeId, setDraggedNodeId] = useState<string | null>(null);
  const [isPanningCanvas, setIsPanningCanvas] = useState<boolean>(false);
  const [panStart, setPanStart] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const canvasRef = useRef<HTMLDivElement>(null);
  const animFrameRef = useRef<number | null>(null);
  const nodesRef = useRef<GraphNode[]>([]);
  const edgesRef = useRef<GraphEdge[]>([]);

  nodesRef.current = nodes;
  edgesRef.current = edges;

  // Sync clearance if user logs in
  useEffect(() => {
    if (userAuthRole) setSelectedClearance(userAuthRole);
  }, [userAuthRole]);

  // Color Mapping
  const getNodeColor = useCallback((cat: string) => {
    if (cat === 'restricted_stub') {
      return { fill: '#f1f5f9', stroke: '#94a3b8', text: '#475569', badge: 'bg-[#f1f5f9] text-[#64748b] border-[#cbd5e1]' };
    }
    switch (cat) {
      case 'unit':
        return { fill: '#0284c7', stroke: '#0369a1', text: '#ffffff', badge: 'bg-[#e0f2fe] text-[#0369a1] border-[#bae6fd]' };
      case 'equipment':
        return { fill: '#0ea5e9', stroke: '#0284c7', text: '#ffffff', badge: 'bg-[#e0f7ff] text-[#0284c7] border-[#bae6fd]' };
      case 'sensor':
        return { fill: '#059669', stroke: '#047857', text: '#ffffff', badge: 'bg-[#d1fae5] text-[#047857] border-[#a7f3d0]' };
      case 'defect':
        return { fill: '#d97706', stroke: '#b45309', text: '#ffffff', badge: 'bg-[#fef3c7] text-[#b45309] border-[#fde68a]' };
      case 'sop':
        return { fill: '#7c3aed', stroke: '#6d28d9', text: '#ffffff', badge: 'bg-[#ede9fe] text-[#6d28d9] border-[#ddd6fe]' };
      case 'classified':
        return { fill: '#b45309', stroke: '#78350f', text: '#ffffff', badge: 'bg-[#fee2e2] text-[#991b1b] border-[#fca5a5]' };
      default:
        return { fill: '#475569', stroke: '#334155', text: '#ffffff', badge: 'bg-[#f1f5f9] text-[#334155] border-[#cbd5e1]' };
    }
  }, []);

  // Fetch Graph Data
  const fetchGraph = async (clearance: string) => {
    setIsLoading(true);
    try {
      const res = await fetch(`/api/knowledge-graph?clearance=${clearance}`);
      if (res.ok) {
        const data: GraphResponse = await res.json();
        setGraphMeta(data);

        // Calculate initial cluster positions
        const width = 850;
        const height = 550;
        const cx = width / 2;
        const cy = height / 2;

        const count = data.nodes.length;
        const initializedNodes: GraphNode[] = data.nodes.map((node, i) => {
          let dist = 180;
          if (node.category === 'unit') dist = 70;
          else if (node.category === 'equipment') dist = 160;
          else if (node.category === 'sensor') dist = 240;
          else if (node.category === 'defect') dist = 260;
          else if (node.category === 'sop') dist = 220;
          else if (node.category === 'classified' || node.category === 'restricted_stub') dist = 290;

          const angle = (i / count) * 2 * Math.PI - Math.PI / 2;
          return {
            ...node,
            x: cx + dist * Math.cos(angle) + (Math.sin(i * 3) * 25),
            y: cy + dist * Math.sin(angle) + (Math.cos(i * 2) * 25),
            vx: 0,
            vy: 0,
            radius: node.category === 'unit' ? 24 : node.category === 'equipment' ? 20 : 18,
          };
        });

        setNodes(initializedNodes);
        setEdges(data.edges);

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

  // Continuous Force Simulation Physics Loop
  useEffect(() => {
    let running = true;

    const simulate = () => {
      if (!running) return;

      setNodes((prevNodes) => {
        if (prevNodes.length === 0) return prevNodes;

        const updated = prevNodes.map((n) => ({ ...n }));
        const nodeMap = new Map(updated.map((n) => [n.id, n]));
        const cx = 425;
        const cy = 275;

        // 1. Repulsion between all node pairs
        for (let i = 0; i < updated.length; i++) {
          for (let j = i + 1; j < updated.length; j++) {
            const n1 = updated[i];
            const n2 = updated[j];
            const dx = n2.x - n1.x;
            const dy = n2.y - n1.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const minDist = (n1.radius || 18) + (n2.radius || 18) + 40;

            if (dist < 320) {
              const force = (320 - dist) / dist * 0.18;
              const fx = dx * force;
              const fy = dy * force;

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

        // 2. Link Spring Attraction between connected nodes
        for (const edge of edgesRef.current) {
          const source = nodeMap.get(edge.source);
          const target = nodeMap.get(edge.target);
          if (source && target) {
            const dx = target.x - source.x;
            const dy = target.y - source.y;
            const dist = Math.sqrt(dx * dx + dy * dy) || 1;
            const targetDist = 130;
            const force = (dist - targetDist) * 0.035;
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

        // 3. Center Gravity & Damping
        for (const node of updated) {
          if (node.id === draggedNodeId) continue;

          // Gentle pull toward center
          const dx = cx - node.x;
          const dy = cy - node.y;
          node.vx += dx * 0.004;
          node.vy += dy * 0.004;

          // Apply friction / damping
          node.vx *= 0.82;
          node.vy *= 0.82;

          // Update position
          node.x += node.vx;
          node.y += node.vy;

          // Keep in bounds
          node.x = Math.max(40, Math.min(810, node.x));
          node.y = Math.max(40, Math.min(510, node.y));
        }

        return updated;
      });

      animFrameRef.current = requestAnimationFrame(simulate);
    };

    animFrameRef.current = requestAnimationFrame(simulate);

    return () => {
      running = false;
      if (animFrameRef.current) cancelAnimationFrame(animFrameRef.current);
    };
  }, [draggedNodeId]);

  // Mouse / Drag Handlers
  const handleNodeMouseDown = (e: React.MouseEvent, nodeId: string) => {
    e.stopPropagation();
    setDraggedNodeId(nodeId);
    setSelectedNodeId(nodeId);
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

  // Filtered nodes
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
    addToast('info', `Querying agent for ${node.label}...`);
    setActiveTab('chat');
    window.dispatchEvent(
      new CustomEvent('workbench:preload-demo', {
        detail: {
          prompt: `Inspect technical specifications, active telemetry, and standard operating procedures for ${node.label} (${node.id}). Cross-reference safety limits.`,
          isMultimodal: false,
        },
      })
    );
  };

  return (
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#f0f7ff] text-[#0f172a] font-mono select-none">
      {/* Header Banner */}
      <div className="p-4 bg-white border-b-2 border-[#cbd5e1] flex flex-col lg:flex-row lg:items-center justify-between gap-4 shrink-0 brutal-shadow-sky">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="w-3 h-3 bg-[#0284c7] inline-block" />
            <h1 className="text-lg font-black font-display tracking-tight text-[#0f172a] uppercase">
              Interactive Sovereign Knowledge Graph &bull; Draggable Topology
            </h1>
            <span className="text-[10px] font-bold px-2 py-0.5 bg-[#e0f2fe] text-[#0369a1] border border-[#bae6fd] uppercase">
              PHYSICS SIMULATION ACTIVE
            </span>
          </div>
          <p className="text-xs text-slate-600 font-sans mt-0.5">
            Drag any node to explore relational graph physics. Hover over an entity to illuminate connected pathways.
          </p>
        </div>

        {/* Clearance Switcher */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-xs font-bold text-slate-500 uppercase flex items-center gap-1.5 mr-1">
            <Shield className="w-3.5 h-3.5 text-[#0284c7]" />
            <span>Clearance:</span>
          </div>

          {[
            { key: 'viewer', label: 'L1: VIEWER' },
            { key: 'operator', label: 'L2: OPERATOR' },
            { key: 'admin', label: 'L3: ADMIN' },
          ].map((lvl) => {
            const isCurrent = selectedClearance === lvl.key;
            return (
              <button
                key={lvl.key}
                onClick={() => setSelectedClearance(lvl.key)}
                className={`px-3 py-1.5 text-xs font-black uppercase border-2 transition-all brutal-btn ${
                  isCurrent
                    ? 'bg-[#0284c7] text-white border-black brutal-shadow-dark'
                    : 'bg-[#f8fafc] text-slate-700 border-[#cbd5e1] hover:border-[#0284c7]'
                }`}
              >
                <span>{lvl.label}</span>
              </button>
            );
          })}
        </div>
      </div>

      {/* Filter Strip */}
      <div className="px-5 py-2.5 bg-[#f8fafc] border-b-2 border-[#cbd5e1] flex flex-wrap items-center justify-between gap-3 shrink-0 text-xs">
        <div className="flex items-center gap-2">
          <div className="relative">
            <Search className="w-3.5 h-3.5 text-slate-400 absolute left-2.5 top-1/2 -translate-y-1/2" />
            <input
              type="text"
              value={searchQuery}
              onChange={(e) => setSearchQuery(e.target.value)}
              placeholder="Search graph entities..."
              className="pl-8 pr-3 py-1 bg-white border-2 border-[#cbd5e1] text-xs font-mono placeholder:text-slate-400 focus:outline-none focus:border-[#0284c7] w-52"
            />
          </div>

          {/* Category Filter Pills */}
          <div className="flex items-center gap-1">
            {[
              { id: 'all', label: 'ALL' },
              { id: 'unit', label: 'UNITS' },
              { id: 'equipment', label: 'EQUIPMENT' },
              { id: 'sensor', label: 'SENSORS' },
              { id: 'defect', label: 'DEFECTS' },
              { id: 'sop', label: 'SOPS' },
              { id: 'classified', label: 'SECRET' },
            ].map((c) => (
              <button
                key={c.id}
                onClick={() => setSelectedCategory(c.id)}
                className={`px-2 py-0.5 text-[10px] font-bold uppercase border transition-all ${
                  selectedCategory === c.id
                    ? 'bg-[#0284c7] text-white border-black'
                    : 'bg-white text-slate-600 border-[#cbd5e1] hover:border-[#0284c7]'
                }`}
              >
                {c.label}
              </button>
            ))}
          </div>
        </div>

        {/* Stats */}
        <div className="flex items-center gap-3 text-[11px] text-slate-600 font-bold">
          <span>ENTITIES: <strong className="text-[#0284c7]">{visibleNodes.length}</strong></span>
          <span>LINKS: <strong className="text-[#059669]">{visibleEdges.length}</strong></span>
          {graphMeta && graphMeta.hidden_nodes > 0 && (
            <span className="text-[#d97706] bg-[#fef3c7] px-2 py-0.5 border border-[#fde68a] uppercase flex items-center gap-1">
              <Lock className="w-3 h-3" />
              <span>{graphMeta.hidden_nodes} RBAC LOCKED</span>
            </span>
          )}
        </div>
      </div>

      {/* Main Canvas & Inspector Drawer */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* Force-Directed Canvas */}
        <div
          ref={canvasRef}
          className="flex-1 relative bg-[#f0f7ff] overflow-hidden cursor-crosshair select-none"
          onMouseDown={handleCanvasMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
        >
          {/* Zoom Overlay */}
          <div className="absolute top-4 left-4 z-10 flex flex-col gap-1 bg-white border-2 border-[#cbd5e1] p-1 brutal-shadow-dark">
            <button
              onClick={() => setZoom((z) => Math.min(z + 0.2, 2.5))}
              className="p-1.5 hover:bg-[#e0f2fe] text-slate-700"
              title="Zoom In"
            >
              <ZoomIn className="w-4 h-4" />
            </button>
            <button
              onClick={() => setZoom((z) => Math.max(z - 0.2, 0.4))}
              className="p-1.5 hover:bg-[#e0f2fe] text-slate-700"
              title="Zoom Out"
            >
              <ZoomOut className="w-4 h-4" />
            </button>
            <button
              onClick={() => {
                setZoom(1);
                setPan({ x: 0, y: 0 });
              }}
              className="p-1.5 hover:bg-[#e0f2fe] text-slate-700"
              title="Reset View"
            >
              <RotateCcw className="w-4 h-4" />
            </button>
          </div>

          <svg
            className="w-full h-full"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: 'center center',
            }}
          >
            {/* Grid Pattern & Marker Arrows */}
            <defs>
              <pattern id="graph-grid-pattern" width="30" height="30" patternUnits="userSpaceOnUse">
                <path d="M 30 0 L 0 0 0 30" fill="none" stroke="#e2e8f0" strokeWidth="1" />
              </pattern>
              <marker
                id="marker-arrow"
                markerWidth="8"
                markerHeight="6"
                refX="22"
                refY="3"
                orient="auto"
              >
                <polygon points="0 0, 8 3, 0 6" fill="#94a3b8" />
              </marker>
              <marker
                id="marker-arrow-active"
                markerWidth="8"
                markerHeight="6"
                refX="22"
                refY="3"
                orient="auto"
              >
                <polygon points="0 0, 8 3, 0 6" fill="#0284c7" />
              </marker>
            </defs>

            <rect width="100%" height="100%" fill="url(#graph-grid-pattern)" />

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

                return (
                  <g key={`${edge.source}-${edge.target}-${idx}`}>
                    <line
                      x1={sourceNode.x}
                      y1={sourceNode.y}
                      x2={targetNode.x}
                      y2={targetNode.y}
                      stroke={isHighlighted ? '#0284c7' : '#cbd5e1'}
                      strokeWidth={isHighlighted ? 2.5 : 1.5}
                      strokeOpacity={isDimmed ? 0.25 : 1}
                      strokeDasharray={edge.clearance === 'admin' ? '4 2' : 'none'}
                      markerEnd={isHighlighted ? 'url(#marker-arrow-active)' : 'url(#marker-arrow)'}
                      className="transition-all duration-150"
                    />
                    {/* Relationship Badge */}
                    <text
                      x={(sourceNode.x + targetNode.x) / 2}
                      y={(sourceNode.y + targetNode.y) / 2 - 4}
                      fill={isHighlighted ? '#0284c7' : '#64748b'}
                      fontSize="9"
                      fontWeight="bold"
                      textAnchor="middle"
                      opacity={isDimmed ? 0.3 : 1}
                      className="select-none pointer-events-none transition-all duration-150"
                      style={{
                        paintOrder: 'stroke',
                        stroke: '#ffffff',
                        strokeWidth: '2px',
                      }}
                    >
                      {edge.label}
                    </text>
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
                    className="cursor-grab active:cursor-grabbing"
                    opacity={isDimmed ? 0.35 : 1}
                  >
                    {/* Pulsing Selection Ring */}
                    {isSelected && (
                      <circle
                        r={radius + 8}
                        fill="none"
                        stroke="#0284c7"
                        strokeWidth="3"
                        strokeDasharray="4 2"
                        className="animate-spin"
                        style={{ animationDuration: '6s' }}
                      />
                    )}

                    {/* Main Node Body */}
                    <circle
                      r={radius}
                      fill={colors.fill}
                      stroke="#0f172a"
                      strokeWidth={isSelected || isHovered ? 3 : 2}
                      className="transition-transform duration-100"
                    />

                    {/* Center Category Glyph */}
                    {isStub ? (
                      <text
                        textAnchor="middle"
                        dy="4"
                        fill="#64748b"
                        fontSize="12"
                        fontWeight="bold"
                      >
                        🔒
                      </text>
                    ) : (
                      <text
                        textAnchor="middle"
                        dy="4"
                        fill={colors.text}
                        fontSize="10"
                        fontWeight="black"
                        className="pointer-events-none select-none"
                      >
                        {node.category.substring(0, 2).toUpperCase()}
                      </text>
                    )}

                    {/* Entity Name Label */}
                    <text
                      y={radius + 14}
                      textAnchor="middle"
                      fill="#0f172a"
                      fontSize="10"
                      fontWeight="bold"
                      className="select-none pointer-events-none"
                      style={{
                        paintOrder: 'stroke',
                        stroke: '#ffffff',
                        strokeWidth: '3px',
                        strokeLinejoin: 'round',
                      }}
                    >
                      {node.label.length > 20 ? `${node.label.substring(0, 18)}...` : node.label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>

        {/* Node Details Inspector Sidebar */}
        {selectedNode && (
          <aside className="w-80 lg:w-96 bg-white border-l-2 border-[#cbd5e1] p-5 flex flex-col justify-between overflow-y-auto shrink-0 brutal-shadow-dark z-10">
            <div className="space-y-4">
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-bold text-slate-500 uppercase">
                    ENTITY ID: {selectedNode.id}
                  </span>
                  <span className={`text-[9px] font-bold px-2 py-0.5 uppercase border ${getNodeColor(selectedNode.category).badge}`}>
                    {selectedNode.clearance.toUpperCase()} CLEARANCE
                  </span>
                </div>
                <h2 className="text-base font-black font-display text-[#0f172a] uppercase mt-1">
                  {selectedNode.label}
                </h2>
                <div className="text-[11px] font-bold text-[#0284c7] uppercase mt-0.5">
                  CATEGORY: {selectedNode.category.toUpperCase()}
                </div>
              </div>

              {/* Description */}
              <div className="p-3 bg-[#f8fafc] border border-[#cbd5e1] text-xs text-slate-700 leading-relaxed font-sans">
                {selectedNode.description}
              </div>

              {/* Properties */}
              <div className="space-y-2">
                <div className="text-xs font-bold text-[#0f172a] uppercase tracking-wider">
                  Entity Metadata Properties
                </div>
                <div className="border border-[#cbd5e1] bg-white divide-y divide-[#f1f5f9] text-xs">
                  {Object.entries(selectedNode.properties).map(([k, v]) => (
                    <div key={k} className="p-2 flex justify-between gap-2">
                      <span className="text-slate-500 uppercase font-bold text-[10px]">{k}:</span>
                      <span className="text-[#0f172a] font-bold text-right truncate">{v}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Connected Relationships */}
              <div className="space-y-2">
                <div className="text-xs font-bold text-[#0f172a] uppercase tracking-wider">
                  Connected Topology ({selectedNodeEdges.length})
                </div>
                <div className="space-y-1.5 max-h-40 overflow-y-auto">
                  {selectedNodeEdges.map((edge, i) => {
                    const otherId = edge.source === selectedNode.id ? edge.target : edge.source;
                    const otherNode = nodes.find((n) => n.id === otherId);
                    const isOutgoing = edge.source === selectedNode.id;

                    return (
                      <div
                        key={i}
                        onClick={() => otherNode && setSelectedNodeId(otherNode.id)}
                        className="p-2 bg-[#f0f9ff] border border-[#bae6fd] hover:border-[#0284c7] cursor-pointer text-[11px] flex items-center justify-between"
                      >
                        <div className="flex items-center gap-1.5 truncate">
                          <span className="text-[#0284c7] font-bold">
                            {isOutgoing ? '→' : '←'} {edge.label}
                          </span>
                          <span className="text-slate-700 font-bold truncate">
                            {otherNode?.label || otherId}
                          </span>
                        </div>
                        <ChevronRight className="w-3.5 h-3.5 text-slate-400 shrink-0" />
                      </div>
                    );
                  })}
                </div>
              </div>
            </div>

            {/* Actions */}
            <div className="pt-4 border-t-2 border-[#f1f5f9] space-y-2">
              <button
                onClick={() => handleDispatchQuery(selectedNode)}
                className="w-full flex items-center justify-center gap-2 px-4 py-2.5 bg-[#0284c7] hover:bg-[#0369a1] text-white font-bold text-xs uppercase border-2 border-black brutal-shadow-dark brutal-btn"
              >
                <Send className="w-3.5 h-3.5" />
                <span>Query Agent About Entity</span>
              </button>
            </div>
          </aside>
        )}
      </div>
    </div>
  );
};
