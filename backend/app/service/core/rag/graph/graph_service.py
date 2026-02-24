"""Knowledge graph build and retrieval service."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
import logging
import time

from sqlalchemy.orm import Session

from core.config import settings
from service.core.rag.graph.extractor import GraphExtractor
from service.core.rag.graph.store import KnowledgeGraphStore, normalize_entity_name
from service.core.rag.providers.registry import resolve_provider


class KnowledgeGraphService:
    """Build and query knowledge graph evidence."""

    def __init__(self, *, db: Session) -> None:
        self.db = db
        self.store = KnowledgeGraphStore(db)
        self.extractor = GraphExtractor()
        self.logger = logging.getLogger("rag.graph")

    def build_from_records(
        self,
        *,
        kb_id: int,
        document_id: int,
        records: List[Dict[str, Any]],
        provider: Optional[str] = None,
        rag_config: Optional[Dict[str, Any]] = None,
    ) -> int:
        if not self._graph_enabled(provider, rag_config):
            return 0
        candidates = self._select_candidates(records, rag_config=rag_config)
        max_entities = int(getattr(settings, "SM_GRAPH_MAX_ENTITIES_PER_CHUNK", 8) or 8)
        max_relations = int(getattr(settings, "SM_GRAPH_MAX_RELATIONS_PER_CHUNK", 10) or 10)
        created = 0

        for record in candidates:
            text = (record.get("text") or "").strip()
            if not text:
                continue
            metadata = record.get("metadata") or {}
            chunk_id = metadata.get("chunk_id") or metadata.get("id")
            if chunk_id and self.store.has_chunk_evidence(kb_id=kb_id, chunk_id=str(chunk_id)):
                continue
            extraction = self.extractor.extract_from_text(
                text=text,
                max_entities=max_entities,
                max_relations=max_relations,
                context_hint=self._build_context_hint(metadata),
            )
            if not extraction.entities and not extraction.relations:
                continue
            self.store.upsert_graph(
                kb_id=kb_id,
                document_id=document_id,
                chunk_id=str(chunk_id) if chunk_id else None,
                entities=extraction.entities,
                relations=extraction.relations,
                evidence_text=text[:1000],
                metadata={"logical_type": metadata.get("logical_type"), "element_type": metadata.get("element_type")},
            )
            created += 1
        return created

    def suggest_boost_doc_ids(
        self,
        *,
        kb_id: int,
        query: str,
        provider: Optional[str] = None,
        rag_config: Optional[Dict[str, Any]] = None,
        max_docs: int = 10,
    ) -> Tuple[List[int], List[str], List[Dict[str, Any]], Dict[str, Any]]:
        if not self._graph_enabled(provider, rag_config):
            return [], [], [], {}
        try:
            t0 = time.perf_counter()
            max_entities = int(getattr(settings, "SM_GRAPH_QUERY_MAX_ENTITIES", 6) or 6)
            entities = self.extractor.extract_query_entities(query, max_entities=max_entities)
            if not entities:
                debug = {
                    "entities": [],
                    "nodes": [],
                    "edges": [],
                    "boost_doc_ids": [],
                    "boost_chunk_ids": [],
                    "boost_chunk_count": 0,
                    "query_variants": [],
                    "graph_status": "no_entities",
                    "fallback_entity_variants": False,
                    "elapsed_ms": int((time.perf_counter() - t0) * 1000),
                }
                try:
                    self.logger.info(f"[GRAPH_BOOST] kb={kb_id} status=no_entities elapsed_ms={debug['elapsed_ms']}")
                except Exception:
                    pass
                return [], [], [], debug
            normalized = [normalize_entity_name(name) for name in entities if name]
            normalized = [name for name in normalized if name]
            nodes = self.store.query_nodes(
                kb_id=kb_id,
                normalized_names=normalized,
                fuzzy_names=entities,
            )
            node_ids = [node.id for node in nodes if node.id is not None]
            edge_list = self.store.query_edges_for_nodes(kb_id=kb_id, node_ids=node_ids)
            edge_ids = [edge.id for edge in edge_list if edge.id is not None]

            doc_scores: Dict[int, float] = {}
            chunk_scores: Dict[str, float] = {}
            evidence_nodes = self.store.query_evidence_for_nodes(kb_id=kb_id, node_ids=node_ids)
            evidence_edges = self.store.query_evidence_for_edges(kb_id=kb_id, edge_ids=edge_ids)
            for ev in evidence_nodes + evidence_edges:
                doc_id = ev.document_id
                if doc_id is None:
                    continue
                doc_scores[doc_id] = doc_scores.get(doc_id, 0.0) + float(ev.score or 1.0)
                if ev.chunk_id:
                    chunk_scores[str(ev.chunk_id)] = chunk_scores.get(str(ev.chunk_id), 0.0) + float(
                        ev.score or 1.0
                    )

            ranked = sorted(doc_scores.items(), key=lambda item: item[1], reverse=True)
            boost_doc_ids = [doc_id for doc_id, _ in ranked[:max_docs]]
            max_chunks = int(getattr(settings, "SM_GRAPH_MAX_BOOST_CHUNKS", 30) or 30)
            ranked_chunks = sorted(chunk_scores.items(), key=lambda item: item[1], reverse=True)
            boost_chunk_ids = [chunk_id for chunk_id, _ in ranked_chunks[:max_chunks]]
            query_variants: List[Dict[str, Any]] = []
            if getattr(settings, "SM_GRAPH_QUERY_EXPANSION_ENABLED", True):
                max_variants = int(getattr(settings, "SM_GRAPH_QUERY_MAX_VARIANTS", 6) or 6)
                query_variants = self._build_query_variants(
                    nodes=nodes,
                    edges=edge_list,
                    max_variants=max_variants,
                )
            fallback_entity_variants = False
            if (
                not query_variants
                and bool(getattr(settings, "SM_GRAPH_ENTITY_VARIANT_FALLBACK", True))
            ):
                max_variants = int(getattr(settings, "SM_GRAPH_QUERY_MAX_VARIANTS", 6) or 6)
                query_variants = self._build_entity_only_variants(
                    entities=entities,
                    max_variants=max_variants,
                )
                fallback_entity_variants = bool(query_variants)

            if not nodes:
                graph_status = "no_nodes_matched"
            elif not edge_list:
                graph_status = "no_edges_matched"
            elif not boost_doc_ids and not boost_chunk_ids:
                graph_status = "no_evidence_matched"
            else:
                graph_status = "ok"

            elapsed_ms = int((time.perf_counter() - t0) * 1000)
            debug = {
                "entities": entities,
                "nodes": [node.name for node in nodes],
                "edges": [edge.relation for edge in edge_list],
                "boost_doc_ids": boost_doc_ids,
                "boost_chunk_ids": boost_chunk_ids[: min(len(boost_chunk_ids), 10)],
                "boost_chunk_count": len(boost_chunk_ids),
                "query_variants": [v.get("text") for v in query_variants[:10]],
                "graph_status": graph_status,
                "fallback_entity_variants": fallback_entity_variants,
                "elapsed_ms": elapsed_ms,
            }
            try:
                self.logger.info(
                    f"[GRAPH_BOOST] kb={kb_id} status={graph_status} entities={len(entities)} "
                    f"nodes={len(nodes)} edges={len(edge_list)} docs={len(boost_doc_ids)} chunks={len(boost_chunk_ids)} "
                    f"variants={len(query_variants)} fallback_variants={fallback_entity_variants} elapsed_ms={elapsed_ms}"
                )
            except Exception:
                pass
            return boost_doc_ids, boost_chunk_ids, query_variants, debug
        except Exception as exc:
            try:
                self.logger.warning("Graph boost failed: %s", exc)
            except Exception:
                pass
            return [], [], [], {"error": str(exc)}

    def _graph_enabled(self, provider: Optional[str], rag_config: Optional[Dict[str, Any]]) -> bool:
        if not getattr(settings, "SM_GRAPH_ENABLED", False):
            return False
        provider_name = resolve_provider(provider)
        if provider_name not in {"graph", "multimodal_graph"}:
            return False
        if rag_config and isinstance(rag_config, dict):
            if rag_config.get("graph_enabled") is False:
                return False
        return True

    def _select_candidates(
        self, records: List[Dict[str, Any]], *, rag_config: Optional[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        min_chars = int(getattr(settings, "SM_GRAPH_MIN_CHARS", 200) or 200)
        max_chunks = int(getattr(settings, "SM_GRAPH_MAX_CHUNKS_PER_DOC", 40) or 40)
        if rag_config and isinstance(rag_config, dict):
            max_chunks = int(rag_config.get("graph_max_chunks") or max_chunks)
            min_chars = int(rag_config.get("graph_min_chars") or min_chars)
        candidates = []
        for rec in records:
            text = (rec.get("text") or "").strip()
            if len(text) < min_chars:
                continue
            metadata = rec.get("metadata") or {}
            logical_type = (metadata.get("logical_type") or "").lower()
            element_type = (metadata.get("element_type") or "").lower()
            score = len(text)
            if logical_type in {"abstract", "introduction", "method", "conclusion", "related_work"}:
                score += 500
            if element_type in {"table_json", "equation_latex"}:
                score += 200
            if element_type == "figure_summary":
                score += 150
            candidates.append((score, rec))
        candidates.sort(key=lambda item: item[0], reverse=True)
        return [rec for _, rec in candidates[:max_chunks]]

    def _build_context_hint(self, metadata: Dict[str, Any]) -> Optional[str]:
        logical_type = metadata.get("logical_type")
        structure_title = metadata.get("structure_title")
        if logical_type or structure_title:
            return f"Section: {logical_type or ''} {structure_title or ''}".strip()
        return None

    @staticmethod
    def _build_query_variants(
        *, nodes: List[Any], edges: List[Any], max_variants: int
    ) -> List[Dict[str, Any]]:
        variants: List[Dict[str, Any]] = []
        seen: set[str] = set()
        node_name_map: Dict[int, str] = {}
        for node in nodes:
            if node.id is not None:
                node_name_map[int(node.id)] = str(node.name or "").strip()

        for node in nodes:
            name = str(node.name or "").strip()
            key = name.casefold()
            if not name or key in seen:
                continue
            seen.add(key)
            variants.append(
                {"text": name, "tag": "graph_entity", "synthetic": True}
            )
            if len(variants) >= max_variants:
                return variants

        for edge in edges:
            head = node_name_map.get(int(edge.source_node_id or 0), "")
            tail = node_name_map.get(int(edge.target_node_id or 0), "")
            relation = str(edge.relation or "").strip()
            phrase = " ".join(seg for seg in (head, relation, tail) if seg)
            key = phrase.casefold()
            if not phrase or key in seen:
                continue
            seen.add(key)
            variants.append(
                {"text": phrase, "tag": "graph_relation", "synthetic": True}
            )
            if len(variants) >= max_variants:
                break
        return variants

    @staticmethod
    def _build_entity_only_variants(*, entities: List[str], max_variants: int) -> List[Dict[str, Any]]:
        variants: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for entity in entities:
            text = str(entity or "").strip()
            key = text.casefold()
            if not text or key in seen:
                continue
            seen.add(key)
            variants.append(
                {"text": text, "tag": "graph_entity_fallback", "synthetic": True}
            )
            if len(variants) >= max_variants:
                break
        return variants
