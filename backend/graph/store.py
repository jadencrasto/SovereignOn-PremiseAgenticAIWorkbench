"""
backend/graph/store.py
-----------------------
Persistent SQLite graph store with WAL mode and atomic transaction isolation.
Stores nodes, edges, and provenance records for the Sovereign Knowledge Graph.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.graph.schemas import (
    GraphEdge,
    GraphNode,
    ProvenanceRecord,
    ProvenanceType,
)
from backend.graph.seed_data import STATIC_EDGES, STATIC_NODES

logger = logging.getLogger(__name__)


class KnowledgeGraphStore:
    """
    Thread-safe SQLite storage for the Knowledge Graph.
    Enforces WAL journal mode, canonical entity keys, and provenance tracking.
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30.0,
            check_same_thread=False,
        )
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        conn.execute("PRAGMA busy_timeout=5000;")
        return conn

    def _init_db(self) -> None:
        with self._lock, self._get_connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS kg_nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    category TEXT NOT NULL,
                    clearance TEXT NOT NULL,
                    description TEXT,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    is_static BOOLEAN NOT NULL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_kg_nodes_category ON kg_nodes(category);
                CREATE INDEX IF NOT EXISTS idx_kg_nodes_clearance ON kg_nodes(clearance);

                CREATE TABLE IF NOT EXISTS kg_edges (
                    id TEXT PRIMARY KEY,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relationship TEXT NOT NULL,
                    clearance TEXT NOT NULL,
                    properties_json TEXT NOT NULL DEFAULT '{}',
                    is_inferred BOOLEAN NOT NULL DEFAULT 0,
                    is_static BOOLEAN NOT NULL DEFAULT 0,
                    weight REAL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(source_id) REFERENCES kg_nodes(id) ON DELETE CASCADE,
                    FOREIGN KEY(target_id) REFERENCES kg_nodes(id) ON DELETE CASCADE
                );

                CREATE INDEX IF NOT EXISTS idx_kg_edges_source ON kg_edges(source_id);
                CREATE INDEX IF NOT EXISTS idx_kg_edges_target ON kg_edges(target_id);
                CREATE INDEX IF NOT EXISTS idx_kg_edges_rel ON kg_edges(relationship);

                CREATE TABLE IF NOT EXISTS kg_provenance (
                    id TEXT PRIMARY KEY,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    provenance_type TEXT NOT NULL,
                    clearance TEXT NOT NULL DEFAULT 'viewer',
                    document_id TEXT,
                    filename TEXT,
                    chunk_id TEXT,
                    page_number INTEGER,
                    section_header TEXT,
                    source_snippet TEXT,
                    source_label TEXT,
                    extraction_method TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 1.0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );

                CREATE INDEX IF NOT EXISTS idx_kg_prov_target ON kg_provenance(target_type, target_id);
                CREATE INDEX IF NOT EXISTS idx_kg_prov_doc ON kg_provenance(document_id);
            """)

            # Migration: add is_static to kg_edges if it doesn't exist (pre-Step 11 DBs)
            try:
                conn.execute("SELECT is_static FROM kg_edges LIMIT 1")
            except sqlite3.OperationalError as exc:
                if "no such column" in str(exc).lower():
                    conn.execute("ALTER TABLE kg_edges ADD COLUMN is_static BOOLEAN NOT NULL DEFAULT 0")
                    logger.info("Migrated kg_edges: added is_static column")
                else:
                    raise

    def seed_static_topology(self) -> Tuple[int, int]:
        """
        Idempotently seeds baseline company topology and assets into SQLite.
        Returns (nodes_seeded, edges_seeded).
        """
        with self._lock, self._get_connection() as conn:
            # 1. Seed static nodes
            for node in STATIC_NODES:
                nid = node["id"]
                label = node["label"]
                category = node["category"]
                clearance = node["clearance"]
                description = node.get("description", "")
                props = json.dumps(node.get("properties", {}))

                conn.execute(
                    """
                    INSERT INTO kg_nodes (id, label, category, clearance, description, properties_json, is_static)
                    VALUES (?, ?, ?, ?, ?, ?, 1)
                    ON CONFLICT(id) DO UPDATE SET
                        label=excluded.label,
                        category=excluded.category,
                        clearance=excluded.clearance,
                        description=excluded.description,
                        properties_json=excluded.properties_json,
                        is_static=1,
                        updated_at=CURRENT_TIMESTAMP;
                    """,
                    (nid, label, category, clearance, description, props),
                )

                # Attach static provenance record
                prov_id = f"prov_static_{nid}"
                conn.execute(
                    """
                    INSERT INTO kg_provenance (
                        id, target_type, target_id, provenance_type, clearance,
                        source_label, extraction_method, confidence
                    )
                    VALUES (?, 'node', ?, 'STATIC_TOPOLOGY', ?, 'MRPL Refinery Base Asset Registry', 'static_seed', 1.0)
                    ON CONFLICT(id) DO UPDATE SET
                        clearance=excluded.clearance;
                    """,
                    (prov_id, nid, clearance),
                )

            # 2. Seed static edges
            for edge in STATIC_EDGES:
                src = edge["source"]
                tgt = edge["target"]
                rel = edge["relationship"]
                clr = edge["clearance"]
                eid = f"{src}:{rel}:{tgt}"

                conn.execute(
                    """
                    INSERT INTO kg_edges (id, source_id, target_id, relationship, clearance, properties_json, is_inferred, is_static, weight)
                    VALUES (?, ?, ?, ?, ?, '{}', 0, 1, 1.0)
                    ON CONFLICT(id) DO UPDATE SET
                        clearance=excluded.clearance,
                        properties_json=excluded.properties_json,
                        is_inferred=0,
                        is_static=1,
                        weight=1.0;
                    """,
                    (eid, src, tgt, rel, clr),
                )

                prov_id = f"prov_static_{eid}"
                conn.execute(
                    """
                    INSERT INTO kg_provenance (
                        id, target_type, target_id, provenance_type, clearance,
                        source_label, extraction_method, confidence
                    )
                    VALUES (?, 'edge', ?, 'STATIC_TOPOLOGY', ?, 'MRPL Refinery Base Asset Registry', 'static_seed', 1.0)
                    ON CONFLICT(id) DO UPDATE SET
                        clearance=excluded.clearance;
                    """,
                    (prov_id, eid, clr),
                )

            conn.commit()

            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM kg_nodes WHERE is_static = 1")
            n_count = cursor.fetchone()[0]
            cursor.execute("SELECT COUNT(*) FROM kg_edges WHERE is_static = 1")
            e_count = cursor.fetchone()[0]

            logger.info("Static topology seeded | static_nodes=%d static_edges=%d", n_count, e_count)
            return n_count, e_count

    # ----------------------------------------------------------------------
    # Node & Edge Upserts (Atomic)
    # ----------------------------------------------------------------------

    def upsert_node(
        self,
        node: GraphNode,
        provenance: Optional[ProvenanceRecord] = None,
    ) -> None:
        """Upsert an entity node and attach supporting provenance.
        Static topology nodes (is_static=1) are NEVER overwritten by non-static upserts.
        """
        with self._lock, self._get_connection() as conn:
            # Protect static topology: if existing node is_static=1 and incoming is not, skip update
            if not node.is_static:
                existing = conn.execute(
                    "SELECT is_static FROM kg_nodes WHERE id = ?", (node.id,)
                ).fetchone()
                if existing and existing["is_static"]:
                    logger.debug("Skipping upsert for static node %s (protected)", node.id)
                    # Still attach provenance if provided
                    if provenance:
                        conn.execute(
                            """
                            INSERT INTO kg_provenance (
                                id, target_type, target_id, provenance_type, clearance,
                                document_id, filename, chunk_id, page_number,
                                section_header, source_snippet, source_label,
                                extraction_method, confidence
                            )
                            VALUES (?, 'node', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                source_snippet=excluded.source_snippet,
                                clearance=excluded.clearance,
                                confidence=excluded.confidence;
                            """,
                            (
                                provenance.id, node.id,
                                provenance.provenance_type.value, provenance.clearance,
                                provenance.document_id, provenance.filename,
                                provenance.chunk_id, provenance.page_number,
                                provenance.section_header, provenance.source_snippet,
                                provenance.source_label, provenance.extraction_method,
                                provenance.confidence,
                            ),
                        )
                        conn.commit()
                    return

            props = json.dumps(node.properties)
            conn.execute(
                """
                INSERT INTO kg_nodes (id, label, category, clearance, description, properties_json, is_static)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    label=excluded.label,
                    category=excluded.category,
                    clearance=excluded.clearance,
                    description=excluded.description,
                    properties_json=excluded.properties_json,
                    is_static=excluded.is_static,
                    updated_at=CURRENT_TIMESTAMP;
                """,
                (node.id, node.label, node.category, node.clearance, node.description, props, int(node.is_static)),
            )

            if provenance:
                conn.execute(
                    """
                    INSERT INTO kg_provenance (
                        id, target_type, target_id, provenance_type, clearance,
                        document_id, filename, chunk_id, page_number,
                        section_header, source_snippet, source_label,
                        extraction_method, confidence
                    )
                    VALUES (?, 'node', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_snippet=excluded.source_snippet,
                        clearance=excluded.clearance,
                        confidence=excluded.confidence;
                    """,
                    (
                        provenance.id,
                        node.id,
                        provenance.provenance_type.value,
                        provenance.clearance,
                        provenance.document_id,
                        provenance.filename,
                        provenance.chunk_id,
                        provenance.page_number,
                        provenance.section_header,
                        provenance.source_snippet,
                        provenance.source_label,
                        provenance.extraction_method,
                        provenance.confidence,
                    ),
                )
            conn.commit()

    def upsert_edge(
        self,
        edge: GraphEdge,
        provenance: Optional[ProvenanceRecord] = None,
    ) -> None:
        """Upsert a relationship edge and attach supporting provenance.
        Static topology edges (is_static=1) are NEVER overwritten by non-static upserts.
        """
        with self._lock, self._get_connection() as conn:
            eid = edge.id or f"{edge.source}:{edge.relationship}:{edge.target}"

            # Protect static topology edges
            if not edge.is_static:
                existing = conn.execute(
                    "SELECT is_static FROM kg_edges WHERE id = ?", (eid,)
                ).fetchone()
                if existing and existing["is_static"]:
                    logger.debug("Skipping upsert for static edge %s (protected)", eid)
                    if provenance:
                        conn.execute(
                            """
                            INSERT INTO kg_provenance (
                                id, target_type, target_id, provenance_type, clearance,
                                document_id, filename, chunk_id, page_number,
                                section_header, source_snippet, source_label,
                                extraction_method, confidence
                            )
                            VALUES (?, 'edge', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(id) DO UPDATE SET
                                source_snippet=excluded.source_snippet,
                                clearance=excluded.clearance,
                                confidence=excluded.confidence;
                            """,
                            (
                                provenance.id, eid,
                                provenance.provenance_type.value, provenance.clearance,
                                provenance.document_id, provenance.filename,
                                provenance.chunk_id, provenance.page_number,
                                provenance.section_header, provenance.source_snippet,
                                provenance.source_label, provenance.extraction_method,
                                provenance.confidence,
                            ),
                        )
                        conn.commit()
                    return

            props = json.dumps(edge.properties)
            conn.execute(
                """
                INSERT INTO kg_edges (id, source_id, target_id, relationship, clearance, properties_json, is_inferred, is_static, weight)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    clearance=excluded.clearance,
                    is_inferred=excluded.is_inferred,
                    is_static=excluded.is_static,
                    weight=excluded.weight;
                """,
                (eid, edge.source, edge.target, edge.relationship, edge.clearance, props, int(edge.is_inferred), int(edge.is_static), edge.weight),
            )

            if provenance:
                conn.execute(
                    """
                    INSERT INTO kg_provenance (
                        id, target_type, target_id, provenance_type, clearance,
                        document_id, filename, chunk_id, page_number,
                        section_header, source_snippet, source_label,
                        extraction_method, confidence
                    )
                    VALUES (?, 'edge', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(id) DO UPDATE SET
                        source_snippet=excluded.source_snippet,
                        clearance=excluded.clearance,
                        confidence=excluded.confidence;
                    """,
                    (
                        provenance.id,
                        eid,
                        provenance.provenance_type.value,
                        provenance.clearance,
                        provenance.document_id,
                        provenance.filename,
                        provenance.chunk_id,
                        provenance.page_number,
                        provenance.section_header,
                        provenance.source_snippet,
                        provenance.source_label,
                        provenance.extraction_method,
                        provenance.confidence,
                    ),
                )
            conn.commit()

    # ----------------------------------------------------------------------
    # Query & Traversal
    # ----------------------------------------------------------------------

    def get_node(self, node_id: str) -> Optional[GraphNode]:
        with self._lock, self._get_connection() as conn:
            row = conn.execute("SELECT * FROM kg_nodes WHERE id = ?", (node_id,)).fetchone()
            if not row:
                return None
            return self._row_to_node(row, conn)

    def get_all_nodes(self) -> List[GraphNode]:
        with self._lock, self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM kg_nodes").fetchall()
            return [self._row_to_node(r, conn) for r in rows]

    def get_all_edges(self) -> List[GraphEdge]:
        with self._lock, self._get_connection() as conn:
            rows = conn.execute("SELECT * FROM kg_edges").fetchall()
            return [self._row_to_edge(r, conn) for r in rows]

    def get_provenance_for_target(self, target_type: str, target_id: str) -> List[ProvenanceRecord]:
        with self._lock, self._get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM kg_provenance WHERE target_type = ? AND target_id = ?",
                (target_type, target_id),
            ).fetchall()
            return [self._row_to_provenance(r) for r in rows]

    def query_neighbors(
        self,
        node_id: str,
        max_depth: int = 1,
        limit: int = 25,
    ) -> Tuple[List[GraphNode], List[GraphEdge]]:
        """
        Traverse graph up to max_depth (1 or 2) starting from node_id.
        Bounded by limit (default 25) to prevent context flooding.
        Returns strictly explicitly supported or recorded relationships.
        """
        depth = min(max(1, max_depth), 2)
        visited_nodes: Set[str] = {node_id}
        frontier: Set[str] = {node_id}
        collected_edges: Dict[str, GraphEdge] = {}

        with self._lock, self._get_connection() as conn:
            # Check starting node
            start_row = conn.execute("SELECT * FROM kg_nodes WHERE id = ?", (node_id,)).fetchone()
            if not start_row:
                # Check alias or lowercase lookup
                start_row = conn.execute(
                    "SELECT * FROM kg_nodes WHERE LOWER(id) = LOWER(?) OR LOWER(label) LIKE LOWER(?)",
                    (node_id, f"%{node_id}%"),
                ).fetchone()
                if not start_row:
                    return [], []
                node_id = start_row["id"]
                visited_nodes = {node_id}
                frontier = {node_id}

            for _ in range(depth):
                if not frontier or len(visited_nodes) >= limit:
                    break

                placeholders = ",".join("?" for _ in frontier)
                query = f"""
                    SELECT * FROM kg_edges
                    WHERE source_id IN ({placeholders}) OR target_id IN ({placeholders})
                """
                params = list(frontier) + list(frontier)
                edge_rows = conn.execute(query, params).fetchall()

                next_frontier: Set[str] = set()
                for er in edge_rows:
                    edge = self._row_to_edge(er, conn)
                    collected_edges[edge.id] = edge

                    other = edge.target if edge.source in visited_nodes else edge.source
                    if other not in visited_nodes and len(visited_nodes) < limit:
                        visited_nodes.add(other)
                        next_frontier.add(other)

                frontier = next_frontier

            # Fetch all visited node objects
            if not visited_nodes:
                return [], []

            node_placeholders = ",".join("?" for _ in visited_nodes)
            node_rows = conn.execute(
                f"SELECT * FROM kg_nodes WHERE id IN ({node_placeholders})",
                list(visited_nodes),
            ).fetchall()
            nodes = [self._row_to_node(nr, conn) for nr in node_rows]

            # Ensure root node is first in the returned list
            node_map = {n.id: n for n in nodes}
            ordered_nodes = []
            if node_id in node_map:
                ordered_nodes.append(node_map.pop(node_id))
            ordered_nodes.extend(node_map.values())

            return ordered_nodes, list(collected_edges.values())

    # ----------------------------------------------------------------------
    # Safe Document Deletion & Orphan Management
    # ----------------------------------------------------------------------

    def delete_document_records(self, document_id: str) -> Dict[str, int]:
        """
        Safely deletes all records originating from a deleted document:
          1. Deletes all kg_provenance records with matching document_id.
          2. Preserves edges supported by other documents.
          3. Deletes edges only when NO supporting provenance remains.
          4. Deletes document-derived entity nodes (is_static = 0) with zero remaining provenance.
          5. Never deletes static topology nodes or edges (is_static = 1).
        Runs inside an atomic transaction.
        """
        with self._lock, self._get_connection() as conn:
            cursor = conn.cursor()

            # Identify edges and nodes referenced by this document's provenance
            cursor.execute(
                "SELECT target_type, target_id FROM kg_provenance WHERE document_id = ?",
                (document_id,),
            )
            affected = cursor.fetchall()
            affected_edges = {r[1] for r in affected if r[0] == "edge"}
            affected_nodes = {r[1] for r in affected if r[0] == "node"}

            # Step 1: Delete provenance records for this document
            cursor.execute("DELETE FROM kg_provenance WHERE document_id = ?", (document_id,))
            deleted_prov_count = cursor.rowcount

            # Step 2: Clean up edges that have NO supporting provenance remaining (and are not static)
            deleted_edges_count = 0
            for eid in affected_edges:
                cursor.execute(
                    "SELECT COUNT(*) FROM kg_provenance WHERE target_type = 'edge' AND target_id = ?",
                    (eid,),
                )
                remaining_prov = cursor.fetchone()[0]
                if remaining_prov == 0:
                    # Never delete static topology edges
                    cursor.execute("DELETE FROM kg_edges WHERE id = ? AND is_static = 0", (eid,))
                    deleted_edges_count += cursor.rowcount

            # Step 3: Clean up document-derived nodes (is_static = 0) that have no remaining provenance
            deleted_nodes_count = 0
            for nid in affected_nodes:
                cursor.execute(
                    "SELECT is_static FROM kg_nodes WHERE id = ?",
                    (nid,),
                )
                row = cursor.fetchone()
                if not row or row[0] == 1:
                    # Static node: never delete
                    continue

                cursor.execute(
                    "SELECT COUNT(*) FROM kg_provenance WHERE target_type = 'node' AND target_id = ?",
                    (nid,),
                )
                remaining_prov = cursor.fetchone()[0]

                # Also verify node is not part of any remaining edges
                cursor.execute(
                    "SELECT COUNT(*) FROM kg_edges WHERE source_id = ? OR target_id = ?",
                    (nid, nid),
                )
                remaining_edges = cursor.fetchone()[0]

                if remaining_prov == 0 and remaining_edges == 0:
                    cursor.execute("DELETE FROM kg_nodes WHERE id = ? AND is_static = 0", (nid,))
                    deleted_nodes_count += cursor.rowcount

            conn.commit()

            logger.info(
                "Deleted document graph records for doc_id=%s | prov=%d edges=%d nodes=%d",
                document_id, deleted_prov_count, deleted_edges_count, deleted_nodes_count,
            )

            return {
                "deleted_provenance": deleted_prov_count,
                "deleted_edges": deleted_edges_count,
                "deleted_nodes": deleted_nodes_count,
            }

    # ----------------------------------------------------------------------
    # Row Deserialization Helpers
    # ----------------------------------------------------------------------

    def _row_to_provenance(self, r: sqlite3.Row) -> ProvenanceRecord:
        return ProvenanceRecord(
            id=r["id"],
            target_type=r["target_type"],
            target_id=r["target_id"],
            provenance_type=ProvenanceType(r["provenance_type"]),
            clearance=r["clearance"],
            document_id=r["document_id"],
            filename=r["filename"],
            chunk_id=r["chunk_id"],
            page_number=r["page_number"],
            section_header=r["section_header"],
            source_snippet=r["source_snippet"],
            source_label=r["source_label"],
            extraction_method=r["extraction_method"],
            confidence=r["confidence"],
            created_at=str(r["created_at"]),
        )

    def _row_to_node(self, r: sqlite3.Row, conn: sqlite3.Connection) -> GraphNode:
        nid = r["id"]
        props = {}
        if r["properties_json"]:
            try:
                props = json.loads(r["properties_json"])
            except Exception:
                props = {}

        prov_rows = conn.execute(
            "SELECT * FROM kg_provenance WHERE target_type = 'node' AND target_id = ?",
            (nid,),
        ).fetchall()
        provs = [self._row_to_provenance(pr) for pr in prov_rows]

        return GraphNode(
            id=nid,
            label=r["label"],
            category=r["category"],
            clearance=r["clearance"],
            description=r["description"] or "",
            properties=props,
            is_static=bool(r["is_static"]),
            created_at=str(r["created_at"]),
            updated_at=str(r["updated_at"]),
            provenance=provs,
        )

    def _row_to_edge(self, r: sqlite3.Row, conn: sqlite3.Connection) -> GraphEdge:
        eid = r["id"]
        props = {}
        if r["properties_json"]:
            try:
                props = json.loads(r["properties_json"])
            except Exception:
                props = {}

        prov_rows = conn.execute(
            "SELECT * FROM kg_provenance WHERE target_type = 'edge' AND target_id = ?",
            (eid,),
        ).fetchall()
        provs = [self._row_to_provenance(pr) for pr in prov_rows]

        # Handle missing is_static column for pre-migration databases
        try:
            is_static = bool(r["is_static"])
        except (IndexError, KeyError):
            is_static = False

        return GraphEdge(
            id=eid,
            source=r["source_id"],
            target=r["target_id"],
            relationship=r["relationship"],
            clearance=r["clearance"],
            properties=props,
            is_static=is_static,
            is_inferred=bool(r["is_inferred"]),
            weight=r["weight"],
            created_at=str(r["created_at"]),
            provenance=provs,
        )
