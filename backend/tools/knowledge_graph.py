"""
backend/tools/knowledge_graph.py
---------------------------------
Knowledge Graph query tool for the Sovereign Agentic Workbench.

Features:
  - Strictly read-only tool.
  - Query bounded by depth (max 2) and results (max 25).
  - RBAC role and clearance aware.
  - Clearance-filtered provenance citations: sensitive snippets redacted for lower clearance roles.
  - Markdown-formatted output for easy agent reasoning with explicit grounding citations.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Union
from pydantic import BaseModel, ConfigDict, Field

from backend.graph.schemas import GraphQueryResult

logger = logging.getLogger(__name__)


class KnowledgeGraphQueryInput(BaseModel):
    """Input schema for the knowledge_graph_query tool."""
    model_config = ConfigDict(extra="ignore")

    query: str = Field(
        ...,
        min_length=1,
        max_length=200,
        description=(
            "Entity tag, unit name, or equipment to inspect in the Knowledge Graph "
            "(e.g. 'P-204', 'V-401', 'Hydrocracker Unit 04', 'cavitation')."
        ),
    )
    max_depth: int = Field(
        default=1,
        ge=1,
        le=2,
        description="Traversal depth: 1 for direct neighbors, 2 for explicitly linked 2-hop relationships.",
    )


def create_knowledge_graph_query(graph_service: Optional[Any] = None) -> Callable:
    """
    Factory creating the execute function for the knowledge_graph_query tool.
    """

    async def execute_knowledge_graph_query(
        query_or_args: Union[KnowledgeGraphQueryInput, str, Dict[str, Any], None] = None,
        max_depth: Optional[int] = None,
        _user: Optional[Any] = None,
        query: Optional[str] = None,
        args: Optional[Union[KnowledgeGraphQueryInput, str, Dict[str, Any]]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """
        Execute knowledge graph query with RBAC clearance gating.
        Supports both validated KnowledgeGraphQueryInput (from ToolRegistry.execute)
        and direct keyword arguments (from tests and standalone callers).

        SECURITY: Authenticated user context is sourced ONLY from trusted
        server-injected parameters (_user or authenticated server kwargs).
        LLM-supplied tool arguments cannot specify, spoof, or elevate clearance.
        """
        if graph_service is None:
            return {
                "success": False,
                "found": False,
                "error": "Knowledge Graph service is unavailable.",
                "message": "Knowledge Graph service is not initialized or unavailable.",
                "relationships": [],
            }

        input_obj = args if args is not None else query_or_args

        # Resolve query and depth strictly from tool input
        if isinstance(input_obj, KnowledgeGraphQueryInput):
            actual_query = input_obj.query
            actual_depth = input_obj.max_depth
        elif isinstance(input_obj, dict):
            actual_query = input_obj.get("query") or query or ""
            actual_depth = input_obj.get("max_depth", max_depth if max_depth is not None else 1)
        elif query is not None:
            actual_query = query
            actual_depth = max_depth if max_depth is not None else 1
        elif isinstance(input_obj, str) and input_obj:
            actual_query = input_obj
            actual_depth = max_depth if max_depth is not None else 1
        else:
            actual_query = str(input_obj or "")
            actual_depth = max_depth if max_depth is not None else 1

        # SEC-01: Authenticated user context MUST come only from server-injected
        # keyword arguments (_user or kwargs['_user'] / kwargs['authenticated_user']),
        # NEVER from LLM-provided arguments (input_obj, __pydantic_extra__, etc.).
        caller_user = _user or kwargs.get("_user") or kwargs.get("authenticated_user")

        # Determine caller clearance
        user_clearance = "viewer"
        if caller_user:
            if hasattr(caller_user, "role"):
                user_clearance = str(getattr(caller_user.role, "value", caller_user.role)).lower()
            elif isinstance(caller_user, dict) and "role" in caller_user:
                user_clearance = str(caller_user["role"]).lower()
            elif isinstance(caller_user, str):
                user_clearance = caller_user.lower()

        # Execute bounded query via service
        bounded_depth = min(max(1, int(actual_depth)), 2)
        result: GraphQueryResult = graph_service.query(
            query_term=actual_query,
            user_clearance=user_clearance,
            max_depth=bounded_depth,
            limit=25,
        )

        if not result.nodes:
            return {
                "success": True,
                "found": False,
                "query": actual_query,
                "message": f"No entity matching '{actual_query}' found in the Knowledge Graph.",
                "relationships": [],
            }

        # Build grounded markdown summary
        target_node = None
        target_id_candidates = {actual_query.lower(), f"unit_{actual_query.lower()}", f"eq_{actual_query.lower()}"}
        for n in result.nodes:
            if n.id.lower() in target_id_candidates or n.label.lower() == actual_query.lower():
                target_node = n
                break
        if not target_node:
            target_node = result.nodes[0]
        md_lines = [
            f"### Knowledge Graph Inspection: {target_node.label}",
            f"- **Canonical ID**: `{target_node.id}`",
            f"- **Category**: {target_node.category.title()}",
            f"- **Clearance**: {target_node.clearance.upper()}",
            f"- **Description**: {target_node.description}",
        ]

        if target_node.properties:
            prop_str = ", ".join(f"{k}: {v}" for k, v in target_node.properties.items() if not isinstance(v, (dict, list)))
            if prop_str:
                md_lines.append(f"- **Key Attributes**: {prop_str}")

        # Grounding & Provenance for the target node
        if target_node.provenance:
            for prov in target_node.provenance:
                if prov.provenance_type.value == "STATIC_TOPOLOGY":
                    md_lines.append(f"- **Origin**: [STATIC PLANT TOPOLOGY] {prov.source_label or 'MRPL Asset Registry'}")
                else:
                    page_str = f" (Page {prov.page_number})" if prov.page_number else ""
                    md_lines.append(
                        f"- **Evidence Source**: [DOC] `{prov.filename}`{page_str} (Chunk `{prov.chunk_id}`)"
                    )
                    if prov.source_snippet:
                        md_lines.append(f"  > *\"{prov.source_snippet}\"*")

        # Explicitly supported relationships
        md_lines.append("\n#### Connected Relationships:")
        if not result.edges:
            md_lines.append("- *(No connected relationships discovered within traversal limit)*")
        else:
            # Map node IDs to display labels
            node_label_map = {n.id: n.label for n in result.nodes}

            for edge in result.edges:
                src_name = node_label_map.get(edge.source, edge.source)
                tgt_name = node_label_map.get(edge.target, edge.target)
                rel_str = f"`{src_name}` -[{edge.relationship}]-> `{tgt_name}`"

                prov_snippets = []
                for p in edge.provenance:
                    if p.provenance_type.value == "STATIC_TOPOLOGY":
                        prov_snippets.append("[Base Plant Topology]")
                    else:
                        page_s = f", Page {p.page_number}" if p.page_number else ""
                        prov_snippets.append(f"[Source: {p.filename}{page_s}, Chunk: {p.chunk_id}]")

                citation_str = f" ({'; '.join(prov_snippets)})" if prov_snippets else ""
                md_lines.append(f"- {rel_str}{citation_str}")

        formatted_content = "\n".join(md_lines)

        return {
            "success": True,
            "found": True,
            "query": actual_query,
            "target_entity": target_node.id,
            "node_count": result.total_nodes,
            "edge_count": result.total_edges,
            "content": formatted_content,
            "nodes": [n.model_dump() for n in result.nodes],
            "edges": [e.model_dump() for e in result.edges],
        }

    return execute_knowledge_graph_query
