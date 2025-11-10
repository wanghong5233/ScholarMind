from __future__ import annotations
from typing import List, Dict, Any, Generator, Optional
from dataclasses import dataclass
import json
import re
import math
import logging
import time
from collections import defaultdict
from pathlib import Path

from elasticsearch import NotFoundError

from core.config import settings
from service.core.rag.retrieval.vector_store import ESVectoreStore, RetrieveQuery, RetrievedChunk
from service.core.rag.prompt.builder import PromptBuilder
from service.core.rag.llm.client import LLMClient


@dataclass
class RAGResult:
    chunks: List[Dict[str, Any]]
    answer: str


class RAGService:
    def __init__(self) -> None:
        self.store = ESVectoreStore(default_index=settings.ES_DEFAULT_INDEX)
        self.prompt = PromptBuilder(
            language=settings.SM_DEFAULT_LANGUAGE,
            enable_citations=settings.SM_ENABLE_CITATIONS,
            max_context_chars=400000,  # 支持现代大模型的长上下文窗口（约100k tokens）
        )
        self.llm = LLMClient()
        self.logger = logging.getLogger("rag.service")
        self._last_usage: Dict[str, Any] | None = None
        self._last_retrieval_debug: Dict[str, Any] | None = None
        self._last_history_debug: Dict[str, Any] | None = None
        self._last_history_summary: str | None = None
        self._last_variant_meta: Dict[str, Any] | None = None

    def retrieve(
        self,
        *,
        query: str,
        kb_id: int,
        top_k: int = 5,
        focus_doc_ids: Optional[List[int]] = None,
        use_vector: bool = True,
        index_override: Optional[str] = None,
        boost_doc_ids: Optional[List[int]] = None,
        session_index: Optional[str] = None,
        index_mode: str = "auto",
    ) -> List[Dict[str, Any]]:
        # 统一使用多阶段检索流程
        return self._retrieve_multi_stage(
            query=query,
            kb_id=kb_id,
            top_k=top_k,
            focus_doc_ids=focus_doc_ids,
            boost_doc_ids=boost_doc_ids,
            session_index=session_index or index_override,
            index_mode=index_mode,
        )

    def _retrieve_multi_stage(
        self,
        *,
        query: str,
        kb_id: int,
        top_k: int,
        focus_doc_ids: Optional[List[int]],
        boost_doc_ids: Optional[List[int]],
        session_index: Optional[str],
        index_mode: str,
    ) -> List[Dict[str, Any]]:
        variants = self._generate_query_variants(query)
        if not variants:
            try:
                self.logger.warning("RAG.retrieve[multi_stage] no query variants generated")
            except Exception:
                pass
            return []

        try:
            variant_snapshot = [(item["tag"], len(item["text"])) for item in variants]
            self.logger.debug(
                "RAG.retrieve[multi_stage] query='%s' variants=%s",
                (query or "")[:80],
                variant_snapshot,
            )
        except Exception:
            pass

        mode_alias = {
            "session": "session_only",
            "session_only": "session_only",
            "session-only": "session_only",
            "global": "global_only",
            "global_only": "global_only",
            "global-only": "global_only",
            "both": "hybrid",
            "hybrid": "hybrid",
        }
        normalized_mode = mode_alias.get((index_mode or "auto").strip().lower(), "auto")
        default_index_name = self.store.default_index or settings.ES_DEFAULT_INDEX

        index_plan: List[Dict[str, Optional[str]]] = []
        if normalized_mode == "session_only":
            if session_index:
                index_plan.append({"label": "session", "index": session_index, "fallback": None})
        elif normalized_mode == "global_only":
            index_plan.append({"label": "global", "index": None, "fallback": None})
        elif normalized_mode == "hybrid":
            if session_index:
                index_plan.append({"label": "session", "index": session_index, "fallback": None})
            index_plan.append({"label": "global", "index": None, "fallback": None})
        else:  # auto / legacy
            if session_index:
                index_plan.append({"label": "session", "index": session_index, "fallback": default_index_name})
            else:
                index_plan.append({"label": "global", "index": None, "fallback": None})

        if not index_plan:
            index_plan.append({"label": "global", "index": None, "fallback": None})

        channels = None  # use store defaults
        path_hits: Dict[str, List[RetrievedChunk]] = {}
        total_candidates = 0
        index_stats: Dict[str, int] = defaultdict(int)
        indices_used: set[str] = set()

        for plan in index_plan:
            plan_label = plan.get("label", "global") or "global"
            plan_index = plan.get("index")
            fallback_index = plan.get("fallback")

            for variant in variants:
                rq = RetrieveQuery(
                    text=variant["text"],
                    kb_id=kb_id,
                    top_k=max(top_k, 5),
                    focus_doc_ids=focus_doc_ids,
                    index_override=plan_index,
                    use_vector=True,
                    channels=channels,
                    query_tag=variant["tag"],
                    synthetic=variant["synthetic"],
                    boost_doc_ids=boost_doc_ids,
                    fallback_index=fallback_index,
                )
                try:
                    hits = self.store.search_multi_path(query=rq)
                except NotFoundError:
                    try:
                        self.logger.warning(
                            "RAG.retrieve[multi_stage] index '%s' (label=%s) not found, skip.",
                            plan_index,
                            plan_label,
                        )
                    except Exception:
                        pass
                    continue

                total_candidates += len(hits)
                index_stats[plan_label] += len(hits)
                for hit in hits:
                    hit.metadata.setdefault("index_label", plan_label)
                    idx_name = hit.metadata.get("index_name")
                    if idx_name:
                        indices_used.add(str(idx_name))
                    path_id = f"{plan_label}:{variant['tag']}::{hit.source}"
                    path_hits.setdefault(path_id, []).append(hit)

                try:
                    self.logger.debug(
                        "RAG.retrieve[multi_stage] label=%s tag=%s hits=%s sources=%s",
                        plan_label,
                        variant["tag"],
                        len(hits),
                        list({hit.source for hit in hits}),
                    )
                except Exception:
                    pass

        ordered_chunks, fused_scores, path_summary = self._rrf_fuse(path_hits)
        if not ordered_chunks:
            try:
                self.logger.warning("RAG.retrieve[multi_stage] fusion produced 0 candidates")
            except Exception:
                pass
            return []

        try:
            self.logger.debug(
                "RAG.retrieve[multi_stage] rrf_path_summary=%s",
                {path: len(ids) for path, ids in path_summary.items()},
            )
        except Exception:
            pass

        mmr_selected = self._apply_mmr(
            ordered_chunks,
            fused_scores,
            top_k=top_k,
        )

        try:
            mmr_preview = [chunk.chunk_id for chunk in mmr_selected[: min(10, len(mmr_selected))]]
            self.logger.debug(
                "RAG.retrieve[multi_stage] mmr_selected_preview=%s",
                mmr_preview,
            )
        except Exception:
            pass

        if len(index_plan) == 1:
            primary_index_hint = index_plan[0].get("index") or default_index_name
        else:
            primary_index_hint = None
        if getattr(settings, "SM_EQUATION_CONTEXT_EXPANSION", True):
            context_augmented = self.store._expand_equation_context(  # type: ignore[attr-defined]
                chunks=mmr_selected,
                index_name=primary_index_hint,
                kb_id=kb_id,
            )
            # `_expand_equation_context` 会返回原始块 + 上下文块，需剔除重复
            base_ids = {chunk.chunk_id for chunk in mmr_selected}
            augmented: List[RetrievedChunk] = []
            seen: set[str] = set()
            for chunk in context_augmented:
                if chunk.chunk_id in seen:
                    continue
                seen.add(chunk.chunk_id)
                # 对上下文块若不存在 RRF 分数，使用自身得分
                if chunk.chunk_id not in fused_scores:
                    fused_scores[chunk.chunk_id] = float(chunk.score or 0.0)
                augmented.append(chunk)
            mmr_selected = [c for c in augmented if c.chunk_id in base_ids]
            context_only = [c for c in augmented if c.chunk_id not in base_ids]
        else:
            context_only = []

        final_payloads = self._apply_metadata_stage(mmr_selected, fused_scores)

        try:
            metadata_preview = [item.get("chunk_id") for item in final_payloads[: min(10, len(final_payloads))]]
            self.logger.debug(
                "RAG.retrieve[multi_stage] metadata_stage_preview=%s context_added=%s",
                metadata_preview,
                len(context_only),
            )
        except Exception:
            pass

        # 追加上下文块（保持原序，降低权重）
        for ctx in context_only:
            ctx_payload = {
                "text": ctx.text,
                "metadata": ctx.metadata,
                "score": float(ctx.score or fused_scores.get(ctx.chunk_id, 0.0)),
                "chunk_id": ctx.chunk_id,
            }
            if "retrieval_source" not in ctx_payload["metadata"]:
                ctx_payload["metadata"]["retrieval_source"] = ctx.source
            ctx_payload["metadata"].setdefault("is_context", True)
            final_payloads.append(ctx_payload)

        final_payloads = self._apply_rl_stage(question=query, payloads=final_payloads)

        try:
            self.logger.info(
                "RAG.retrieve[multi_stage] kb=%s variants=%s paths=%s candidates=%s final=%s index_mode=%s",
                kb_id,
                len(variants),
                len(path_hits),
                total_candidates,
                len(final_payloads),
                normalized_mode,
            )
        except Exception:
            pass

        try:
            top_doc_id = None
            if final_payloads:
                md0 = final_payloads[0].get("metadata") or {}
                top_doc_id = md0.get("document_id")
            memory_debug = {
                "boost_doc_ids": boost_doc_ids or [],
                "top_doc_id": top_doc_id,
                "top_hit": bool(top_doc_id is not None and boost_doc_ids and str(top_doc_id) in {str(doc) for doc in boost_doc_ids}),
            }
            preview = [
                {
                    "chunk_id": item["chunk_id"],
                    "score": round(float(item["score"]), 4),
                    "source": item["metadata"].get("retrieval_source"),
                }
                for item in final_payloads[: min(10, len(final_payloads))]
            ]
            meta = self._last_variant_meta or {}
            self._last_retrieval_debug = {
                "strategy": "multi_stage",
                "variants": variants,
                "path_stats": {pth: len(hits) for pth, hits in path_hits.items()},
                "rrf_candidates": preview,
                "top_k": top_k,
                "index_mode": normalized_mode,
                "index_plan": index_plan,
                "indices_used": sorted(indices_used),
                "index_stats": dict(index_stats),
                "memory": memory_debug,
                "query_meta": meta,
            }
        except Exception:
            self._last_retrieval_debug = None

        return final_payloads

    # --- query generation helpers -------------------------------------------------
    def _generate_query_variants(self, query: str) -> List[Dict[str, Any]]:
        original_query = (query or "").strip()
        if not original_query:
            return []

        contains_cjk = self._contains_cjk(original_query)
        effective_query = original_query
        translation_used = False

        if contains_cjk and getattr(settings, "SM_AUTO_TRANSLATE_TO_EN", True):
            translated = self._translate_to_english(original_query)
            cleaned = self._clean_query_text(translated)
            if cleaned and cleaned != original_query:
                effective_query = cleaned
                translation_used = True
                try:
                    self.logger.debug(
                        "Auto translated query zh->en: '%s' -> '%s'",
                        original_query,
                        effective_query,
                    )
                except Exception:
                    pass

        target_language = "en" if translation_used or not contains_cjk else settings.SM_DEFAULT_LANGUAGE

        variants: List[Dict[str, Any]] = [
            {"text": effective_query, "tag": "original", "synthetic": False, "language": target_language},
        ]

        mq_num = max(int(getattr(settings, "SM_MULTI_QUERY_NUM", 1) or 1), 1)
        if mq_num > 1:
            rewrites = self._rewrite_queries(effective_query, mq_num - 1)
            for idx, text in enumerate(rewrites, start=1):
                variants.append(
                    {
                        "text": text,
                        "tag": f"mq_{idx}",
                        "synthetic": False,
                        "language": target_language,
                    }
                )

        if getattr(settings, "SM_HYDE_ENABLED", True):
            hyde_text = self._generate_hyde_document(
                effective_query,
                language=target_language,
            )
            if hyde_text:
                variants.append({"text": hyde_text, "tag": "hyde", "synthetic": True, "language": target_language})

        dedup: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for item in variants:
            key = item["text"].strip().casefold()
            if not key or key in seen:
                continue
            seen.add(key)
            dedup.append(item)
        self._last_variant_meta = {
            "original_query": original_query,
            "effective_query": effective_query,
            "translation_used": translation_used,
            "target_language": target_language,
        }
        return dedup

    def _rewrite_queries(self, query: str, extra: int) -> List[str]:
        if extra <= 0:
            return []
        prompts = [
            {
                "role": "system",
                "content": "You rewrite the question into diverse academic search intents. Output one query per line, no numbering.",
            },
            {
                "role": "user",
                "content": f"Rewrite the question into {extra} diverse, concise search queries.\nQuestion: {query}",
            },
        ]
        try:
            resp = self.llm.generate(prompts, temperature=0.2, max_tokens=128, stream=False) or ""
        except Exception:
            resp = ""
        results: List[str] = []
        for line in resp.splitlines():
            stripped = line.strip(" -•\t").strip()
            if stripped:
                results.append(stripped)
        return results[:extra]

    def _generate_hyde_document(self, query: str, *, language: Optional[str] = None) -> str | None:
        target_language = (language or settings.SM_DEFAULT_LANGUAGE or "zh").lower()
        prompts = [
            {
                "role": "system",
                "content": (
                    "Generate a concise hypothetical abstract that would likely answer the research question. The abstract should be factual-sounding, 4-6 sentences."
                    if target_language == "en"
                    else "请生成一段 4-6 句的假设性摘要，仿佛已有论文可完整回答该问题。保持学术语气，避免露骨虚构提示。"
                ),
            },
            {
                "role": "user",
                "content": query,
            },
        ]
        try:
            hyde = self.llm.generate(
                prompts,
                temperature=float(getattr(settings, "SM_HYDE_TEMPERATURE", 0.2) or 0.2),
                max_tokens=int(getattr(settings, "SM_HYDE_MAX_TOKENS", 256) or 256),
                stream=False,
            )
        except Exception:
            hyde = None
        hyde = (hyde or "").strip()
        return hyde or None

    def _contains_cjk(self, text: str) -> bool:
        return bool(re.search(r"[\u4e00-\u9fff]", text or ""))

    def _translate_to_english(self, text: str) -> str:
        prompts = [
            {
                "role": "system",
                "content": "You are a professional translator. Translate the user's question into fluent academic English. Output only the translation without explanations.",
            },
            {"role": "user", "content": text},
        ]
        try:
            translated = self.llm.generate(
                prompts,
                temperature=0.0,
                max_tokens=256,
                stream=False,
            )
            return translated or text
        except Exception as exc:
            try:
                self.logger.warning("Auto translation failed: %s", exc)
            except Exception:
                pass
            return text

    def _clean_query_text(self, text: str) -> str:
        cleaned = (text or "").strip()
        if not cleaned:
            return ""
        cleaned = re.sub(r"^[-•\s]+", "", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned

    # --- fusion & ranking --------------------------------------------------------
    def _rrf_fuse(
        self,
        path_hits: Dict[str, List[RetrievedChunk]],
    ) -> tuple[List[RetrievedChunk], Dict[str, float], Dict[str, List[str]]]:
        if not path_hits:
            return [], {}, {}
        k_val = max(int(getattr(settings, "SM_RRF_K", 60) or 60), 1)
        scores: Dict[str, float] = defaultdict(float)
        best_chunk: Dict[str, RetrievedChunk] = {}
        summary: Dict[str, List[str]] = {}

        for path_id, hits in path_hits.items():
            hits_sorted = sorted(hits, key=lambda h: h.rank or 10**6)
            summary[path_id] = [h.chunk_id for h in hits_sorted[: min(10, len(hits_sorted))]]
            for idx, hit in enumerate(hits_sorted, start=1):
                scores[hit.chunk_id] += 1.0 / (k_val + idx)
                if hit.chunk_id not in best_chunk or hit.score > best_chunk[hit.chunk_id].score:
                    best_chunk[hit.chunk_id] = hit

        ordered_ids = sorted(best_chunk.keys(), key=lambda cid: scores[cid], reverse=True)
        ordered_chunks = [best_chunk[cid] for cid in ordered_ids]
        return ordered_chunks, scores, summary

    def _apply_mmr(
        self,
        candidates: List[RetrievedChunk],
        fused_scores: Dict[str, float],
        *,
        top_k: int,
    ) -> List[RetrievedChunk]:
        if not candidates:
            return []
        if not getattr(settings, "SM_MMR_ENABLED", True):
            return candidates[:top_k]

        lambda_val = float(getattr(settings, "SM_MMR_LAMBDA", 0.65) or 0.65)
        max_candidates = max(int(getattr(settings, "SM_MMR_MAX_CANDIDATES", 60) or 60), top_k)
        pool = candidates[:max_candidates]
        selected: List[RetrievedChunk] = []

        while pool and len(selected) < top_k:
            best_candidate = None
            best_score = float("-inf")
            for cand in pool:
                relevance = fused_scores.get(cand.chunk_id, float(cand.score or 0.0))
                if not selected:
                    mmr_score = relevance
                else:
                    max_sim = max(self._chunk_similarity(cand, s) for s in selected)
                    mmr_score = lambda_val * relevance - (1 - lambda_val) * max_sim
                if mmr_score > best_score:
                    best_candidate = cand
                    best_score = mmr_score
            if best_candidate is None:
                break
            selected.append(best_candidate)
            pool = [c for c in pool if c.chunk_id != best_candidate.chunk_id]

        if len(selected) < top_k:
            remaining = [c for c in candidates if all(c.chunk_id != s.chunk_id for s in selected)]
            selected.extend(remaining[: max(0, top_k - len(selected))])
        return selected

    def _chunk_similarity(self, a: RetrievedChunk, b: RetrievedChunk) -> float:
        ta = self._token_set(a.text)
        tb = self._token_set(b.text)
        if not ta or not tb:
            return 0.0
        intersection = len(ta & tb)
        union = len(ta | tb)
        if union == 0:
            return 0.0
        return intersection / union

    def _token_set(self, text: str) -> set[str]:
        tokens = re.split(r"[^\w]+", text.lower())
        return {t for t in tokens if t}

    def _apply_metadata_stage(
        self,
        chunks: List[RetrievedChunk],
        fused_scores: Dict[str, float],
    ) -> List[Dict[str, Any]]:
        if not chunks:
            return []

        current_year = time.localtime().tm_year
        recency_weight = float(getattr(settings, "SM_METADATA_WEIGHT_RECENCY", 0.0) or 0.0)
        citation_weight = float(getattr(settings, "SM_METADATA_WEIGHT_CITATIONS", 0.0) or 0.0)
        section_bonus = float(getattr(settings, "SM_METADATA_SECTION_BONUS", 0.0) or 0.0)
        priority_raw = getattr(settings, "SM_METADATA_SECTION_PRIORITY", "") or ""
        priority_order = [seg.strip().casefold() for seg in priority_raw.split(":") if seg.strip()]
        priority_map = {name: (len(priority_order) - idx) / max(len(priority_order), 1) for idx, name in enumerate(priority_order)}

        enriched: List[tuple[float, RetrievedChunk]] = []
        for chunk in chunks:
            md = chunk.metadata or {}
            score = fused_scores.get(chunk.chunk_id, float(chunk.score or 0.0))

            year = md.get("publication_year")
            if isinstance(year, str) and year.isdigit():
                year = int(year)
            if isinstance(year, int):
                span = max(current_year - 1970, 1)
                score += recency_weight * max(0.0, (year - 1970) / span)

            citations = md.get("citation_count")
            if isinstance(citations, str) and citations.isdigit():
                citations = int(citations)
            if isinstance(citations, int) and citations > 0:
                score += citation_weight * math.log1p(citations)

            section = (md.get("section") or md.get("section_type") or "").casefold()
            if section_bonus and section in priority_map:
                score += section_bonus * priority_map.get(section, 0.0)

            md["fused_score"] = fused_scores.get(chunk.chunk_id, float(chunk.score or 0.0))
            md["retrieval_score"] = score
            md["retrieval_source"] = md.get("retrieval_source") or chunk.source
            enriched.append((score, chunk))

        enriched.sort(key=lambda item: item[0], reverse=True)
        payloads: List[Dict[str, Any]] = []
        for score, chunk in enriched:
            payloads.append(self._chunk_to_payload(chunk=chunk, score=score))
        return payloads

    def _chunk_to_payload(self, *, chunk: RetrievedChunk, score: float) -> Dict[str, Any]:
        return {
            "text": chunk.text,
            "metadata": chunk.metadata,
            "score": float(score),
            "chunk_id": chunk.chunk_id,
        }

    def _apply_rl_stage(self, *, question: str, payloads: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        if not getattr(settings, "SM_L3_RL_ENABLED", False):
            return payloads
        try:
            self._record_rl_event(question=question, payloads=payloads)
        except Exception:
            pass
        return payloads

    def _record_rl_event(self, *, question: str, payloads: List[Dict[str, Any]]) -> None:
        buffer_path = Path(getattr(settings, "SM_RL_EVENT_BUFFER", "storage/rl_events.jsonl"))
        buffer_path.parent.mkdir(parents=True, exist_ok=True)
        event = {
            "ts": int(time.time()),
            "question": question,
            "candidates": [
                {
                    "chunk_id": item.get("chunk_id"),
                    "score": item.get("score"),
                    "document_id": (item.get("metadata") or {}).get("document_id"),
                    "page": (item.get("metadata") or {}).get("page"),
                    "source": (item.get("metadata") or {}).get("retrieval_source"),
                }
                for item in payloads
            ],
        }
        with buffer_path.open("a", encoding="utf-8") as fp:
            fp.write(json.dumps(event, ensure_ascii=False) + "\n")

    def get_last_retrieval_debug(self) -> Dict[str, Any] | None:
        return self._last_retrieval_debug

    def generate(self, *, question: str, chunks: List[Dict[str, Any]], temperature: float = None, max_tokens: int = None, stream: bool = True, history: Optional[List[Dict[str, str]]] = None, compress_history: bool = False, rolling_summary: Optional[str] = None, style: Optional[str] = None, extra_system: Optional[str] = None):
        t0 = time.time()
        # 关闭开关时，不使用滚动摘要
        try:
            if not getattr(settings, "ENABLE_ROLLING_SUMMARY", True):
                rolling_summary = None
        except Exception:
            pass
        # build optional conversation history summary
        history_summary = None
        try:
            hs = history if isinstance(history, list) else None
            need_compact = bool(compress_history)
            if hs and not need_compact:
                # 预算=模型窗口-预留；若未配置模型窗口，退回 SM_HISTORY_MAX_TOKENS
                model_window = self._model_context_window()
                headroom = int(getattr(settings, "SM_HISTORY_HEADROOM", 4096) or 4096)
                budget_tokens = max((model_window - headroom), int(getattr(settings, "SM_HISTORY_MAX_TOKENS", 2048) or 2048)) if model_window else int(getattr(settings, "SM_HISTORY_MAX_TOKENS", 2048) or 2048)
                joined = "\n".join([f"{m.get('role','user')}: {str(m.get('content',''))}" for m in hs if isinstance(m, dict)])
                # 超大历史先做长度预截（1MB），再估算 tokens，避免极端开销
                if len(joined) > 1_000_000:
                    joined = joined[-1_000_000:]
                if rolling_summary:
                    joined = (rolling_summary or "") + "\n" + joined
                est_tokens = self._estimate_tokens(joined)
                if est_tokens > budget_tokens:
                    need_compact = True
            if hs and need_compact:
                # 将已有滚动摘要与完整历史共同压缩为新的摘要
                if rolling_summary:
                    ext = {"role": "system", "content": f"[rolling_summary]\n{rolling_summary}"}
                    history_summary = self._summarize_history([ext] + hs)
                else:
                    history_summary = self._summarize_history(hs)
                self._last_history_summary = history_summary
                self._last_history_debug = {"mode": "summarized", "orig_turns": len(hs), "summary_chars": len(history_summary or ""), "estTokens": est_tokens, "budgetTokens": budget_tokens}
            elif hs:
                # 仅拼接最近若干条，提供轻量上下文
                recent_k = int(getattr(settings, "HISTORY_RECENT_TURNS", 4) or 4)
                tail = hs[-recent_k:]
                recent_text = "\n".join([f"{m.get('role','user')}: {str(m.get('content',''))}" for m in tail if isinstance(m, dict)])
                # 若存在滚动摘要，则与最近原文合并注入，以实现“摘要+近期原文”的主流策略
                history_summary = ((rolling_summary + "\n") if rolling_summary else "") + recent_text
                est_tokens = self._estimate_tokens(history_summary)
                budget_tokens = int(getattr(settings, "SM_HISTORY_MAX_TOKENS", 2048) or 2048)
                self._last_history_debug = {"mode": "recent_tail", "orig_turns": len(hs), "used_turns": len(tail), "summary_chars": len(history_summary or ""), "estTokens": est_tokens, "budgetTokens": budget_tokens}
            else:
                self._last_history_debug = {"mode": "none"}
        except Exception:
            history_summary = None
            self._last_history_debug = None
            self._last_history_summary = None

        # --- 分配 Prompt 片段预算并裁剪 context ---
        # 为 history/context 分配比例预算，避免历史占满
        try:
            model_window = self._model_context_window() or (int(getattr(settings, "SM_HISTORY_MAX_TOKENS", 2048) or 2048) + int(getattr(settings, "SM_HISTORY_HEADROOM", 4096) or 4096))
            headroom = int(getattr(settings, "SM_HISTORY_HEADROOM", 4096) or 4096)
            total_ctx_budget = max(model_window - headroom, 2048)
            history_budget = int(total_ctx_budget * 0.33)
            context_budget = int(total_ctx_budget * 0.5)
            # 裁剪 history_summary（若存在）
            if history_summary:
                hist_tokens = self._estimate_tokens(history_summary)
                if hist_tokens > history_budget:
                    # 简单按字符比例裁剪
                    ratio = max(history_budget / max(hist_tokens, 1), 0.1)
                    cut = max(int(len(history_summary) * ratio), 200)
                    history_summary = history_summary[:cut]
            # 裁剪 chunks 合并文本
            if chunks:
                chunks = self._trim_chunks_to_tokens(chunks, context_budget)
        except Exception:
            pass

        sections = self.prompt.build(question=question, chunks=chunks, history_summary=history_summary, style=style, extra_system=extra_system)
        messages = [{"role": s.role, "content": s.content} for s in sections]
        temperature = settings.SM_TEMPERATURE if temperature is None else temperature
        max_tokens = settings.SM_MAX_TOKENS if max_tokens is None else max_tokens
        try:
            self.logger.info(f"RAG.generate stream={stream} temp={temperature} max_tokens={max_tokens} prompt_chars={sum(len(m['content']) for m in messages)}")
        except Exception:
            pass
        out = self.llm.generate(messages, temperature=temperature, max_tokens=max_tokens, stream=stream)
        if not stream:
            try:
                self.logger.info(f"RAG.generate done took_ms={int((time.time()-t0)*1000)}")
            except Exception:
                pass
            prompt_chars = sum(len(m["content"]) for m in messages)
            completion_chars = len(out or "")
            ratio = 4 if self.prompt.language == "en" else 1
            self._last_usage = {
                "prompt_tokens": prompt_chars // ratio,
                "completion_tokens": completion_chars // ratio,
                "total_tokens": (prompt_chars + completion_chars) // ratio,
            }
            # 将模型正文中的 [doc_id:page] 等变体规范为 [id:page]
            try:
                out = self._normalize_citations(out)
            except Exception:
                pass
        return out

    def get_last_usage(self) -> Dict[str, Any] | None:
        return self._last_usage

    # --- helpers ---
    def _normalize_citations(self, text: str) -> str:
        if not isinstance(text, str) or not text:
            return text
        # 规范三类形式：
        # [doc_id:82:1] 或 [document_id:82:1] 或 [82:1] → [82:1]
        # 允许中英文提示词混入
        # 先把 [doc_id:82:1]、[document_id:82:1]、[文档ID:82:1] 等替换成 [82:1]
        patterns = [r"\[(?:doc(?:ument)?_?id|documentId|文档ID)\s*:\s*(\d+)\s*:\s*(\d+)\]",
                    r"\[(\d+)\s*:\s*(\d+)\]"]
        def repl(m: re.Match) -> str:
            return f"[{m.group(1)}:{m.group(2)}]"
        # 逐个替换复杂前缀形式
        text = re.sub(patterns[0], repl, text, flags=re.IGNORECASE)
        # 第二个是标准形式，保持不变（这里是幂等处理）
        return text

    # --- context helpers ---
    def _summarize_history(self, history: List[Dict[str, str]]) -> str:
        try:
            # 压缩为简洁要点，保留关键信息与用户约束
            lines = []
            for m in history:
                role = m.get("role", "user")
                content = str(m.get("content", ""))
                lines.append(f"{role}: {content}")
            body = "\n".join(lines[-20:])  # 限制输入规模
            msgs = [
                {"role": "system", "content": (
                    "请将以下对话历史压缩为6-10条要点，务必保留：用户目标/约束、偏好、拒答规则、安全要求、已达成结论与未决问题，以及与当前问题相关的关键信息。不要虚构。"
                    if self.prompt.language == "zh"
                    else "Summarize the conversation into 6-10 bullet points. MUST preserve: user goals/constraints, preferences, refusal/safety rules, reached conclusions and open questions, and key facts relevant to the current query. Do not fabricate."
                )},
                {"role": "user", "content": body},
            ]
            # 超时与重试保护
            summary = self.llm.generate(msgs, temperature=0.2, max_tokens=256, stream=False)
            if not summary:
                summary = self.llm.generate(msgs, temperature=0.2, max_tokens=256, stream=False)
            return summary or ""
        except Exception:
            return ""

    def get_last_history_debug(self) -> Dict[str, Any] | None:
        return self._last_history_debug

    def get_last_history_summary(self) -> str | None:
        return self._last_history_summary

    def _estimate_tokens(self, text: str) -> int:
        try:
            import tiktoken  # type: ignore
            model = None
            if getattr(settings, "SM_LLM_TYPE", "openai") == "openai":
                model = getattr(settings, "OPENAI_MODEL_NAME", None)
            # DashScope 没有官方 tiktoken 配置，退回 cl100k_base 近似
            enc = None
            if model:
                try:
                    enc = tiktoken.encoding_for_model(model)
                except Exception:
                    enc = tiktoken.get_encoding("cl100k_base")
            else:
                enc = tiktoken.get_encoding("cl100k_base")
            return len(enc.encode(text or ""))
        except Exception:
            # 回退：中文1:1，英文1:4 近似
            if not text:
                return 0
            zh = sum(1 for c in text if ord(c) > 127)
            en = len(text) - zh
            return zh + en // 4

    def _model_context_window(self) -> int | None:
        name = None
        try:
            if getattr(settings, "SM_LLM_TYPE", "openai") == "openai":
                name = getattr(settings, "OPENAI_MODEL_NAME", None)
            elif getattr(settings, "SM_LLM_TYPE", "dashscope") == "dashscope":
                name = getattr(settings, "DASHSCOPE_MODEL_NAME", None)
        except Exception:
            name = None
        # 简易映射，可扩展
        table = {
            "gpt-4o": 128000,
            "gpt-4o-mini": 128000,
            "gpt-3.5-turbo": 16000,
            "qwen-plus": 200000,
            "qwen-max": 200000,
            "deepseek-r1": 128000,
            "deepseek-chat": 128000,
        }
        return table.get(name) if name else None

    def _trim_chunks_to_tokens(self, chunks: List[Dict[str, Any]], budget_tokens: int) -> List[Dict[str, Any]]:
        if not chunks:
            return chunks
        kept: List[Dict[str, Any]] = []
        acc = 0
        for c in chunks:
            txt = (c or {}).get("text") or (c or {}).get("content") or ""
            tks = self._estimate_tokens(txt)
            if acc + tks > budget_tokens and kept:
                continue
            kept.append(c)
            acc += tks
            if acc >= budget_tokens:
                break
        return kept

    def ask_stream(
        self,
        *,
        question: str,
        kb_id: int,
        top_k: int = 5,
        focus_doc_ids: Optional[List[int]] = None,
        index_override: Optional[str] = None,
    ) -> Generator[str, None, None]:
        chunks = self.retrieve(
            query=question,
            kb_id=kb_id,
            top_k=top_k,
            focus_doc_ids=focus_doc_ids,
            index_override=index_override,
        )
        for part in self.generate(question=question, chunks=chunks, stream=True):
            yield part

    # --- citations helper ---
    def build_citations(self, chunks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Aggregate chunks into citation objects.
        Structure: {document_id, page, chunk_id, score, snippet, offsets}
        """
        citations: List[Dict[str, Any]] = []
        for c in chunks:
            md = c.get("metadata", {}) or {}
            text = (c.get("text") or c.get("content") or "").strip()
            citations.append({
                "document_id": md.get("document_id"),
                "page": md.get("page"),
                "chunk_id": c.get("chunk_id"),
                "score": c.get("score"),
                "snippet": text[:300],
                "offsets": {
                    "start": md.get("offset_start", 0),
                    "end": md.get("offset_end", 0),
                },
            })
        return citations

    # --- compare documents helper ---
    def compare_documents(
        self,
        *,
        kb_id: int,
        doc_ids: List[int],
        dimensions: List[str],
        top_k: int = 8,
        index_override: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Retrieve with focus on selected documents and generate a Markdown table comparison.
        Returns: { answer: str, chunks: List[Dict] }
        """
        dims = [str(x).strip() for x in (dimensions or []) if str(x).strip()]
        if not dims:
            dims = ["Methodology", "Results", "Limitations"]
        dims_text = ", ".join(dims)
        if self.prompt.language == "zh":
            question = (
                f"请对比以下维度：{dims_text}。以 Markdown 表格输出：列=论文（按标题或文档ID），行=维度。每个单元格给出精炼要点，并附必要的引文标签。"
            )
            extra = (
                "务必严格使用表格格式，避免长段落。每个要点后附加其来源引用，例如 [82:1]。若信息不足，填'—'并说明原因。不要编造。"
            )
            style = "简洁、要点化、表格化"
        else:
            question = (
                f"Compare the following dimensions: {dims_text}. Output a Markdown table: columns=papers (by title or id), rows=dimensions. In each cell, provide concise key points with citations."
            )
            extra = (
                "Use a strict table format, avoid long paragraphs. Append source citations like [82:1] after points. If insufficient info, put '—' and explain briefly. Do not fabricate."
            )
            style = "concise, bullet-style, tabular"

        # Focused retrieval
        rq_topk = max(top_k, 8)
        chunks = self.retrieve(
            query=question,
            kb_id=kb_id,
            top_k=rq_topk,
            focus_doc_ids=doc_ids,
            index_override=index_override,
        )
        answer = self.generate(
            question=question,
            chunks=chunks,
            stream=False,
            history=[],
            compress_history=False,
            style=style,
            extra_system=extra,
        )
        return {"answer": answer or "", "chunks": chunks}
