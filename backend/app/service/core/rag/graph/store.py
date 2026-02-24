"""Knowledge graph storage helpers."""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Tuple
import re

from sqlalchemy import or_
from sqlalchemy.orm import Session

from models.knowledge_graph import KnowledgeGraphEdge, KnowledgeGraphEvidence, KnowledgeGraphNode


def normalize_entity_name(name: str) -> str:
    text = (name or "").strip().lower()
    text = re.sub(r"[\s\-\_]+", " ", text)
    text = re.sub(r"[^\w\s]", "", text)
    return text.strip()


class KnowledgeGraphStore:
    """Persist and query knowledge graph data."""

    def __init__(self, db: Session) -> None:
        self.db = db

    def has_chunk_evidence(self, *, kb_id: int, chunk_id: str) -> bool:
        if not chunk_id:
            return False
        return (
            self.db.query(KnowledgeGraphEvidence)
            .filter(
                KnowledgeGraphEvidence.knowledge_base_id == kb_id,
                KnowledgeGraphEvidence.chunk_id == chunk_id,
            )
            .first()
            is not None
        )

    def upsert_graph(
        self,
        *,
        kb_id: int,
        document_id: int,
        chunk_id: Optional[str],
        entities: List[Dict[str, Any]],
        relations: List[Dict[str, Any]],
        evidence_text: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> None:
        node_map = self._upsert_nodes(kb_id=kb_id, entities=entities)
        edge_map = self._upsert_edges(kb_id=kb_id, node_map=node_map, relations=relations)

        for ent in entities:
            name = str(ent.get("name") or "").strip()
            norm = normalize_entity_name(name)
            node = node_map.get(norm)
            if not node:
                continue
            evidence = KnowledgeGraphEvidence(
                knowledge_base_id=kb_id,
                node_id=node.id,
                edge_id=None,
                document_id=document_id,
                chunk_id=chunk_id,
                score=float(ent.get("score") or 0.0) if ent.get("score") is not None else None,
                evidence_text=evidence_text,
                metadata_=metadata or {},
            )
            self.db.add(evidence)

        for rel in relations:
            head = str(rel.get("head") or "").strip()
            tail = str(rel.get("tail") or "").strip()
            relation = str(rel.get("relation") or "").strip()
            if not head or not tail or not relation:
                continue
            key = (normalize_entity_name(head), normalize_entity_name(tail), relation.lower())
            edge = edge_map.get(key)
            if not edge:
                continue
            evidence = KnowledgeGraphEvidence(
                knowledge_base_id=kb_id,
                node_id=None,
                edge_id=edge.id,
                document_id=document_id,
                chunk_id=chunk_id,
                score=float(rel.get("score") or 0.0) if rel.get("score") is not None else None,
                evidence_text=evidence_text,
                metadata_=metadata or {},
            )
            self.db.add(evidence)

    def query_nodes(
        self, *, kb_id: int, normalized_names: List[str], fuzzy_names: List[str]
    ) -> List[KnowledgeGraphNode]:
        q = self.db.query(KnowledgeGraphNode).filter(
            KnowledgeGraphNode.knowledge_base_id == kb_id
        )
        conditions = []
        if normalized_names:
            conditions.append(KnowledgeGraphNode.normalized.in_(normalized_names))
        for name in fuzzy_names:
            conditions.append(KnowledgeGraphNode.name.ilike(f"%{name}%"))
        if not conditions:
            return []
        return q.filter(or_(*conditions)).all()

    def query_edges_for_nodes(self, *, kb_id: int, node_ids: List[int]) -> List[KnowledgeGraphEdge]:
        if not node_ids:
            return []
        return (
            self.db.query(KnowledgeGraphEdge)
            .filter(
                KnowledgeGraphEdge.knowledge_base_id == kb_id,
                KnowledgeGraphEdge.source_node_id.in_(node_ids) | KnowledgeGraphEdge.target_node_id.in_(node_ids),
            )
            .all()
        )

    def query_evidence_for_nodes(self, *, kb_id: int, node_ids: List[int]) -> List[KnowledgeGraphEvidence]:
        if not node_ids:
            return []
        return (
            self.db.query(KnowledgeGraphEvidence)
            .filter(
                KnowledgeGraphEvidence.knowledge_base_id == kb_id,
                KnowledgeGraphEvidence.node_id.in_(node_ids),
            )
            .all()
        )

    def query_evidence_for_edges(self, *, kb_id: int, edge_ids: List[int]) -> List[KnowledgeGraphEvidence]:
        if not edge_ids:
            return []
        return (
            self.db.query(KnowledgeGraphEvidence)
            .filter(
                KnowledgeGraphEvidence.knowledge_base_id == kb_id,
                KnowledgeGraphEvidence.edge_id.in_(edge_ids),
            )
            .all()
        )

    def cleanup_document(self, *, kb_id: int, document_id: int) -> None:
        self.db.query(KnowledgeGraphEvidence).filter(
            KnowledgeGraphEvidence.knowledge_base_id == kb_id,
            KnowledgeGraphEvidence.document_id == document_id,
        ).delete(synchronize_session=False)
        self.db.flush()

        remaining_edge_ids = [
            row[0]
            for row in self.db.query(KnowledgeGraphEvidence.edge_id)
            .filter(
                KnowledgeGraphEvidence.knowledge_base_id == kb_id,
                KnowledgeGraphEvidence.edge_id.isnot(None),
            )
            .distinct()
            .all()
        ]
        if remaining_edge_ids:
            self.db.query(KnowledgeGraphEdge).filter(
                KnowledgeGraphEdge.knowledge_base_id == kb_id,
                KnowledgeGraphEdge.id.notin_(remaining_edge_ids),
            ).delete(synchronize_session=False)
        else:
            self.db.query(KnowledgeGraphEdge).filter(
                KnowledgeGraphEdge.knowledge_base_id == kb_id
            ).delete(synchronize_session=False)

        remaining_node_ids = [
            row[0]
            for row in self.db.query(KnowledgeGraphEvidence.node_id)
            .filter(
                KnowledgeGraphEvidence.knowledge_base_id == kb_id,
                KnowledgeGraphEvidence.node_id.isnot(None),
            )
            .distinct()
            .all()
        ]
        if remaining_node_ids:
            self.db.query(KnowledgeGraphNode).filter(
                KnowledgeGraphNode.knowledge_base_id == kb_id,
                KnowledgeGraphNode.id.notin_(remaining_node_ids),
            ).delete(synchronize_session=False)
        else:
            self.db.query(KnowledgeGraphNode).filter(
                KnowledgeGraphNode.knowledge_base_id == kb_id
            ).delete(synchronize_session=False)

    def _upsert_nodes(
        self, *, kb_id: int, entities: List[Dict[str, Any]]
    ) -> Dict[str, KnowledgeGraphNode]:
        names = [normalize_entity_name(str(ent.get("name") or "")) for ent in entities]
        names = [n for n in names if n]
        existing = (
            self.db.query(KnowledgeGraphNode)
            .filter(
                KnowledgeGraphNode.knowledge_base_id == kb_id,
                KnowledgeGraphNode.normalized.in_(names),
            )
            .all()
        )
        node_map = {node.normalized: node for node in existing}

        for ent in entities:
            name = str(ent.get("name") or "").strip()
            norm = normalize_entity_name(name)
            if not norm:
                continue
            raw_aliases = ent.get("aliases")
            if isinstance(raw_aliases, list):
                alias_list = [str(a).strip() for a in raw_aliases if str(a).strip()]
            elif isinstance(raw_aliases, str):
                alias_list = [raw_aliases.strip()] if raw_aliases.strip() else []
            else:
                alias_list = []
            if norm not in node_map:
                node = KnowledgeGraphNode(
                    knowledge_base_id=kb_id,
                    name=name,
                    normalized=norm,
                    entity_type=ent.get("type"),
                    aliases=alias_list,
                    description=ent.get("description"),
                )
                self.db.add(node)
                node_map[norm] = node
            else:
                node = node_map[norm]
                aliases = set(node.aliases or [])
                for alias in alias_list:
                    alias_str = str(alias).strip()
                    if alias_str:
                        aliases.add(alias_str)
                if aliases:
                    node.aliases = sorted(aliases)
                if not node.entity_type and ent.get("type"):
                    node.entity_type = ent.get("type")
                if not node.description and ent.get("description"):
                    node.description = ent.get("description")

        self.db.flush()
        return node_map

    def _upsert_edges(
        self,
        *,
        kb_id: int,
        node_map: Dict[str, KnowledgeGraphNode],
        relations: List[Dict[str, Any]],
    ) -> Dict[Tuple[str, str, str], KnowledgeGraphEdge]:
        keys: List[Tuple[str, str, str]] = []
        for rel in relations:
            head = str(rel.get("head") or "").strip()
            tail = str(rel.get("tail") or "").strip()
            relation = str(rel.get("relation") or "").strip().lower()
            if not head or not tail or not relation:
                continue
            keys.append((normalize_entity_name(head), normalize_entity_name(tail), relation))

        existing_edges: List[KnowledgeGraphEdge] = []
        if keys:
            node_ids = [node.id for node in node_map.values() if node.id is not None]
            if node_ids:
                existing_edges = (
                    self.db.query(KnowledgeGraphEdge)
                    .filter(
                        KnowledgeGraphEdge.knowledge_base_id == kb_id,
                        or_(
                            KnowledgeGraphEdge.source_node_id.in_(node_ids),
                            KnowledgeGraphEdge.target_node_id.in_(node_ids),
                        ),
                    )
                    .all()
                )

        edge_map = {}
        for edge in existing_edges:
            edge_map[(normalize_entity_name(edge.source_node.name), normalize_entity_name(edge.target_node.name), edge.relation.lower())] = edge

        for rel in relations:
            head = str(rel.get("head") or "").strip()
            tail = str(rel.get("tail") or "").strip()
            relation = str(rel.get("relation") or "").strip().lower()
            if not head or not tail or not relation:
                continue
            key = (normalize_entity_name(head), normalize_entity_name(tail), relation)
            if key in edge_map:
                continue
            source_node = node_map.get(key[0])
            target_node = node_map.get(key[1])
            if not source_node or not target_node:
                continue
            edge = KnowledgeGraphEdge(
                knowledge_base_id=kb_id,
                source_node_id=source_node.id,
                target_node_id=target_node.id,
                relation=relation,
                weight=float(rel.get("weight") or 0.0) if rel.get("weight") is not None else None,
            )
            self.db.add(edge)
            edge_map[key] = edge

        self.db.flush()
        return edge_map


