/**
 * frontend/src/components/graph/CompanyKnowledgeGraph.tsx
 * --------------------------------------------------------
 * Interactive Company Knowledge Graph with RBAC Authorization Level Filtering.
 * Visualizes Plant Units, Equipment Assets, Sensors, Defects, SOPs, and Classified Records.
 */

import React, { useState, useEffect, useRef, useMemo } from 'react';
import { useWorkbench } from '../../context/WorkbenchContext';
import { useAuth } from '../../context/AuthContext';
import {
  Network,
  Shield,
  Lock,
  Unlock,
  Eye,
  Search,
  Filter,
  RefreshCw,
  Zap,
  Info,
  ChevronRight,
  ZoomIn,
  ZoomOut,
  Maximize2,
  Send,
  Layers,
  CheckCircle2,
  AlertTriangle,
} from 'lucide-react';

interface GraphNode {
  id: string;
  label: string;
  category: 'unit' | 'equipment' | 'sensor' | 'defect' | 'sop' | 'classified' | 'restricted_stub';
  clearance: 'viewer' | 'operator' | 'admin';
  description: string;
  properties: Record<string, string>;
  x?: number;
  y?: number;
  vx?: number;
  vy?: number;
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

  // Clearance filter state: defaults to user's auth role or allows override for exploration
  const [selectedClearance, setSelectedClearance] = useState<string>(userAuthRole || 'viewer');
  const [searchQuery, setSearchQuery] = useState<string>('');
  const [selectedCategory, setSelectedCategory] = useState<string>('all');
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);

  const [graphData, setGraphData] = useState<GraphResponse | null>(null);
  const [isLoading, setIsLoading] = useState<boolean>(true);

  // Pan & Zoom
  const [zoom, setZoom] = useState<number>(1);
  const [pan, setPan] = useState<{ x: number; y: number }>({ x: 0, y: 0 });
  const [isDraggingCanvas, setIsDraggingCanvas] = useState<boolean>(false);
  const [dragStart, setDragStart] = useState<{ x: number; y: number }>({ x: 0, y: 0 });

  const svgRef = useRef<SVGSVGElement>(null);

  // Sync clearance if user logs in
  useEffect(() => {
    if (userAuthRole) {
      setSelectedClearance(userAuthRole);
    }
  }, [userAuthRole]);

  // Fetch Graph Data
  const fetchGraph = async (clearance: string) => {
    setIsLoading(true);
    try {
      const res = await fetch(`/api/knowledge-graph?clearance=${clearance}`);
      if (res.ok) {
        const data: GraphResponse = await res.json();
        
        // Compute circular & layered layout positions
        const width = 800;
        const height = 550;
        const cx = width / 2;
        const cy = height / 2;

        const count = data.nodes.length;
        const nodesWithCoords: GraphNode[] = data.nodes.map((node, i) => {
          let radius = 180;
          if (node.category === 'unit') radius = 80;
          else if (node.category === 'equipment') radius = 160;
          else if (node.category === 'sensor') radius = 230;
          else if (node.category === 'defect') radius = 250;
          else if (node.category === 'sop') radius = 220;
          else if (node.category === 'classified' || node.category === 'restricted_stub') radius = 270;

          const angle = (i / count) * 2 * Math.PI - Math.PI / 2;
          return {
            ...node,
            x: cx + radius * Math.cos(angle) + (Math.sin(i * 3) * 20),
            y: cy + radius * Math.sin(angle) + (Math.cos(i * 2) * 20),
          };
        });

        data.nodes = nodesWithCoords;
        setGraphData(data);

        // Auto select first node if none selected
        if (!selectedNode && data.nodes.length > 0) {
          setSelectedNode(data.nodes[0]);
        }
      } else {
        addToast('error', 'Failed to fetch knowledge graph.');
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

  // Color Mapping
  const getNodeColor = (cat: string, clearance: string) => {
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
  };

  // Filtered nodes
  const visibleNodes = useMemo(() => {
    if (!graphData) return [];
    return graphData.nodes.filter((node) => {
      const matchCat = selectedCategory === 'all' || node.category === selectedCategory;
      const matchSearch =
        searchQuery === '' ||
        node.label.toLowerCase().includes(searchQuery.toLowerCase()) ||
        node.id.toLowerCase().includes(searchQuery.toLowerCase()) ||
        node.description.toLowerCase().includes(searchQuery.toLowerCase());
      return matchCat && matchSearch;
    });
  }, [graphData, selectedCategory, searchQuery]);

  const visibleNodeIds = useMemo(() => new Set(visibleNodes.map((n) => n.id)), [visibleNodes]);

  const visibleEdges = useMemo(() => {
    if (!graphData) return [];
    return graphData.edges.filter(
      (e) => visibleNodeIds.has(e.source) && visibleNodeIds.has(e.target)
    );
  }, [graphData, visibleNodeIds]);

  // Connected nodes for Inspector
  const connectedEdges = useMemo(() => {
    if (!selectedNode || !graphData) return [];
    return graphData.edges.filter(
      (e) => e.source === selectedNode.id || e.target === selectedNode.id
    );
  }, [selectedNode, graphData]);

  // Mouse pan handlers
  const handleMouseDown = (e: React.MouseEvent) => {
    if (e.target === svgRef.current || (e.target as HTMLElement).tagName === 'svg') {
      setIsDraggingCanvas(true);
      setDragStart({ x: e.clientX - pan.x, y: e.clientY - pan.y });
    }
  };

  const handleMouseMove = (e: React.MouseEvent) => {
    if (isDraggingCanvas) {
      setPan({ x: e.clientX - dragStart.x, y: e.clientY - dragStart.y });
    }
  };

  const handleMouseUp = () => {
    setIsDraggingCanvas(false);
  };

  const handleDispatchQuery = (node: GraphNode) => {
    addToast('info', `Dispatched query for ${node.label}`);
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
    <div className="flex-1 flex flex-col h-full overflow-hidden bg-[#f0f7ff] text-[#0f172a] font-mono">
      {/* Top Station Ribbon */}
      <div className="p-4 bg-white border-b-2 border-[#cbd5e1] flex flex-col lg:flex-row lg:items-center justify-between gap-4 shrink-0 brutal-shadow-sky">
        <div>
          <div className="flex items-center gap-2.5">
            <span className="w-3 h-3 bg-[#0284c7] inline-block" />
            <h1 className="text-lg font-black font-display tracking-tight text-[#0f172a] uppercase">
              Company Knowledge Graph &bull; RBAC Authorization Engine
            </h1>
            <span className="text-[10px] font-bold px-2 py-0.5 bg-[#e0f2fe] text-[#0369a1] border border-[#bae6fd] uppercase">
              AIR-GAPPED ENTITY TOPOLOGY
            </span>
          </div>
          <p className="text-xs text-slate-600 font-sans mt-0.5">
            Multi-hop relational knowledge network across Units, Equipment, Defects, and Compliance SOPs filtered by operational authorization level.
          </p>
        </div>

        {/* Authorization Level Switcher */}
        <div className="flex flex-wrap items-center gap-2">
          <div className="text-xs font-bold text-slate-500 uppercase flex items-center gap-1.5 mr-1">
            <Shield className="w-3.5 h-3.5 text-[#0284c7]" />
            <span>Clearance Level:</span>
          </div>

          {[
            { key: 'viewer', label: 'L1: VIEWER', tag: 'OPERATIONAL' },
            { key: 'operator', label: 'L2: OPERATOR', tag: 'TECHNICAL + NDT' },
            { key: 'admin', label: 'L3: ADMIN', tag: 'SECRET SOVEREIGN' },
          ].map((lvl) => {
            const isCurrent = selectedClearance === lvl.key;
            return (
              <button
                key={lvl.key}
                onClick={() => {
                  setSelectedClearance(lvl.key);
                  addToast('info', `Authorization set to ${lvl.label}`);
                }}
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

      {/* Filter & Search Bar */}
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

        {/* Graph Stats */}
        <div className="flex items-center gap-3 text-[11px] text-slate-600 font-bold">
          <span>NODES: <strong className="text-[#0284c7]">{visibleNodes.length}</strong></span>
          <span>RELATIONSHIPS: <strong className="text-[#059669]">{visibleEdges.length}</strong></span>
          {graphData && graphData.hidden_nodes > 0 && (
            <span className="text-[#d97706] bg-[#fef3c7] px-2 py-0.5 border border-[#fde68a] uppercase flex items-center gap-1">
              <Lock className="w-3 h-3" />
              <span>{graphData.hidden_nodes} RESTRICTED BY RBAC</span>
            </span>
          )}
        </div>
      </div>

      {/* Main Graph Canvas Area + Inspector Side Panel */}
      <div className="flex-1 flex overflow-hidden relative">
        {/* SVG Interactive Canvas */}
        <div
          className="flex-1 relative bg-[#f0f7ff] overflow-hidden cursor-grab active:cursor-grabbing select-none"
          onMouseDown={handleMouseDown}
          onMouseMove={handleMouseMove}
          onMouseUp={handleMouseUp}
        >
          {/* Zoom Controls Overlay */}
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
              <Maximize2 className="w-4 h-4" />
            </button>
          </div>

          <svg
            ref={svgRef}
            className="w-full h-full"
            style={{
              transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
              transformOrigin: 'center center',
            }}
          >
            {/* Background Grid Pattern */}
            <defs>
              <pattern id="graph-grid" width="30" height="30" patternUnits="userSpaceOnUse">
                <path d="M 30 0 L 0 0 0 30" fill="none" stroke="#e2e8f0" strokeWidth="1" />
              </pattern>
              <marker
                id="arrowhead"
                markerWidth="8"
                markerHeight="6"
                refX="22"
                refY="3"
                orient="auto"
              >
                <polygon points="0 0, 8 3, 0 6" fill="#94a3b8" />
              </marker>
              <marker
                id="arrowhead-active"
                markerWidth="8"
                markerHeight="6"
                refX="22"
                refY="3"
                orient="auto"
              >
                <polygon points="0 0, 8 3, 0 6" fill="#0284c7" />
              </marker>
            </defs>

            <rect width="100%" height="100%" fill="url(#graph-grid)" />

            {/* Edges */}
            <g className="edges">
              {visibleEdges.map((edge, idx) => {
                const sourceNode = visibleNodes.find((n) => n.id === edge.source);
                const targetNode = visibleNodes.find((n) => n.id === edge.target);
                if (!sourceNode || !targetNode || sourceNode.x === undefined || targetNode.x === undefined) {
                  return null;
                }

                const isConnectedToSelected =
                  selectedNode && (edge.source === selectedNode.id || edge.target === selectedNode.id);

                return (
                  <g key={`${edge.source}-${edge.target}-${idx}`}>
                    <line
                      x1={sourceNode.x}
                      y1={sourceNode.y}
                      x2={targetNode.x}
                      y2={targetNode.y}
                      stroke={isConnectedToSelected ? '#0284c7' : '#cbd5e1'}
                      strokeWidth={isConnectedToSelected ? 2.5 : 1.5}
                      strokeDasharray={edge.clearance === 'admin' ? '4 2' : 'none'}
                      markerEnd={isConnectedToSelected ? 'url(#arrowhead-active)' : 'url(#arrowhead)'}
                    />
                    {/* Edge Label */}
                    <text
                      x={(sourceNode.x + targetNode.x) / 2}
                      y={(sourceNode.y + targetNode.y) / 2 - 4}
                      fill={isConnectedToSelected ? '#0369a1' : '#94a3b8'}
                      fontSize="9"
                      fontWeight="bold"
                      textAnchor="middle"
                      className="select-none pointer-events-none"
                    >
                      {edge.label}
                    </text>
                  </g>
                );
              })}
            </g>

            {/* Nodes */}
            <g className="nodes">
              {visibleNodes.map((node) => {
                const isSelected = selectedNode?.id === node.id;
                const colors = getNodeColor(node.category, node.clearance);
                const isStub = node.category === 'restricted_stub';

                return (
                  <g
                    key={node.id}
                    transform={`translate(${node.x || 0}, ${node.y || 0})`}
                    onClick={() => setSelectedNode(node)}
                    className="cursor-pointer"
                  >
                    {/* Outer ring for selected */}
                    {isSelected && (
                      <circle
                        r="26"
                        fill="none"
                        stroke="#0284c7"
                        strokeWidth="3"
                        strokeDasharray="4 2"
                        className="animate-spin"
                        style={{ animationDuration: '8s' }}
                      />
                    )}

                    {/* Main Node Circle */}
                    <circle
                      r="18"
                      fill={colors.fill}
                      stroke="#0f172a"
                      strokeWidth="2"
                      className="transition-transform duration-150 hover:scale-110"
                    />

                    {/* Node Center Icon / Text */}
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
                      >
                        {node.category.substring(0, 2).toUpperCase()}
                      </text>
                    )}

                    {/* Node Label Below */}
                    <text
                      y="30"
                      textAnchor="middle"
                      fill="#0f172a"
                      fontSize="10"
                      fontWeight="bold"
                      className="select-none"
                      style={{
                        paintOrder: 'stroke',
                        stroke: '#ffffff',
                        strokeWidth: '3px',
                        strokeLinejoin: 'round',
                      }}
                    >
                      {node.label.length > 22 ? `${node.label.substring(0, 20)}...` : node.label}
                    </text>
                  </g>
                );
              })}
            </g>
          </svg>
        </div>

        {/* Node Inspector Side Panel */}
        {selectedNode && (
          <aside className="w-80 lg:w-96 bg-white border-l-2 border-[#cbd5e1] p-5 flex flex-col justify-between overflow-y-auto shrink-0 brutal-shadow-dark z-10">
            <div className="space-y-4">
              {/* Header */}
              <div>
                <div className="flex items-center justify-between">
                  <span className="text-[10px] font-bold text-slate-500 uppercase">
                    ENTITY ID: {selectedNode.id}
                  </span>
                  <span className={`text-[9px] font-bold px-2 py-0.5 uppercase border ${getNodeColor(selectedNode.category, selectedNode.clearance).badge}`}>
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

              {/* Properties Table */}
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
                  Connected Topology ({connectedEdges.length})
                </div>
                <div className="space-y-1.5 max-h-40 overflow-y-auto">
                  {connectedEdges.map((edge, i) => {
                    const otherId = edge.source === selectedNode.id ? edge.target : edge.source;
                    const otherNode = graphData?.nodes.find((n) => n.id === otherId);
                    const isOutgoing = edge.source === selectedNode.id;

                    return (
                      <div
                        key={i}
                        onClick={() => otherNode && setSelectedNode(otherNode)}
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

            {/* Action Footer */}
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
