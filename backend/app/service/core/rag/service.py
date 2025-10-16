from __future__ import annotations
from typing import List, Dict, Any, Generator, Optional
from dataclasses import dataclass
import re
from core.config import settings
from service.core.rag.retrieval.vector_store import ESVectoreStore, RetrieveQuery
from service.core.rag.prompt.builder import PromptBuilder
from service.core.rag.llm.client import LLMClient
from core.config import settings
import logging
import time


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
            max_context_chars=6000,
        )
        self.llm = LLMClient()
        self.logger = logging.getLogger("rag.service")
        self._last_usage: Dict[str, Any] | None = None
        self._last_retrieval_debug: Dict[str, Any] | None = None
        self._last_history_debug: Dict[str, Any] | None = None
        self._last_history_summary: str | None = None

    def retrieve(
        self,
        *,
        query: str,
        kb_id: int,
        top_k: int = 5,
        focus_doc_ids: Optional[List[int]] = None,
        use_vector: bool = True,
        index_override: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        # strategy-aware retrieval entry
        strategy = getattr(settings, "SM_RETRIEVAL_STRATEGY", "basic")
        if strategy == "multi_query":
            return self._retrieve_multi_query(query=query, kb_id=kb_id, top_k=top_k, focus_doc_ids=focus_doc_ids, index_override=index_override)
        # basic fallback
        rq = RetrieveQuery(
            text=query,
            kb_id=kb_id,
            top_k=top_k,
            focus_doc_ids=focus_doc_ids,
            index_override=index_override,
            use_vector=use_vector,
        )
        t0 = time.time()
        results = self.store.search(query=rq)
        dt = int((time.time() - t0) * 1000)
        try:
            self.logger.info(
                f"RAG.retrieve kb={kb_id} top_k={top_k} focus={len(focus_doc_ids or [])} hits={len(results)} took_ms={dt} index={'session' if index_override else 'default'}"
            )
        except Exception:
            pass
        # debug footprint
        try:
            self._last_retrieval_debug = {
                "strategy": "basic",
                "kb_id": kb_id,
                "top_k": top_k,
                "hits": len(results),
                "took_ms": dt,
                "index": (index_override or settings.ES_DEFAULT_INDEX),
            }
        except Exception:
            self._last_retrieval_debug = None
        # convert to common dict format for prompt
        chunks: List[Dict[str, Any]] = []
        for r in results:
            chunks.append({"text": r.text, "metadata": r.metadata, "score": r.score, "chunk_id": r.chunk_id})
        return chunks

    # --- advanced retrieval strategies ---
    def _retrieve_multi_query(
        self,
        *,
        query: str,
        kb_id: int,
        top_k: int,
        focus_doc_ids: Optional[List[int]],
        index_override: Optional[str],
    ) -> List[Dict[str, Any]]:
        """Generate N sub-queries via LLM, retrieve in parallel, fuse by RRF, then dedup and cut to top_k.
        Minimal implementation: serial calls; easy to evolve to async.
        """
        n = max(int(getattr(settings, "SM_MULTI_QUERY_NUM", 4) or 4), 2)
        # 1) expand queries
        prompts = [
            {"role": "system", "content": "You are a helpful assistant that rewrites the question into diverse search intents."},
            {"role": "user", "content": f"Rewrite the question into {n} diverse short search queries, one per line, concise, no numbering.\nQuestion: {query}"},
        ]
        try:
            qtext = self.llm.generate(prompts, temperature=0.2, max_tokens=128, stream=False) or ""
            subs = [s.strip(" -•\t").strip() for s in qtext.splitlines() if s.strip()]
        except Exception:
            subs = []
        # 组装子查询：包含原始问题；去重；过滤过短/与原问题完全相同但语义无差异的噪声
        pool: List[str] = [query] + subs
        uniq: List[str] = []
        seen: set[str] = set()
        for s in pool:
            t = s.strip()
            if not t:
                continue
            if len(t) < 3:
                continue
            k = t.casefold()
            if k in seen:
                continue
            seen.add(k)
            uniq.append(t)
        if not uniq:
            uniq = [query]
        subs = uniq[:n]

        # 2) retrieve for each sub-query
        all_hits: List[Dict[str, Any]] = []
        per_q_hits: Dict[str, int] = {}
        for q in subs:
            rq = RetrieveQuery(
                text=q,
                kb_id=kb_id,
                top_k=max(top_k * 2, 10),
                focus_doc_ids=focus_doc_ids,
                index_override=index_override,
                use_vector=True,
            )
            results = self.store.search(query=rq)
            per_q_hits[q] = len(results or [])
            for r in results:
                all_hits.append({
                    "chunk_id": r.chunk_id,
                    "text": r.text,
                    "score": float(r.score or 0.0),
                    "metadata": r.metadata,
                    "q": q,
                })

        # 3) RRF fuse (Reciprocal Rank Fusion)
        # build per-query rank list
        ranks: Dict[str, Dict[str, int]] = {}
        for q in subs:
            q_hits = [h for h in all_hits if h["q"] == q]
            # sort by ES score desc
            q_hits.sort(key=lambda x: x["score"], reverse=True)
            for i, h in enumerate(q_hits[: max(top_k * 3, 30)]):
                ranks.setdefault(q, {})[h["chunk_id"]] = i + 1

        agg: Dict[str, float] = {}
        for h in all_hits:
            cid = h["chunk_id"]
            s = 0.0
            for q in subs:
                r = ranks.get(q, {}).get(cid)
                if r is not None:
                    s += 1.0 / (60 + r)  # k=60，稳健融合
            if s > 0:
                agg[cid] = agg.get(cid, 0.0) + s

        # 4) dedup by chunk_id, keep metadata/text of first occurrence
        by_id: Dict[str, Dict[str, Any]] = {}
        for h in all_hits:
            cid = h["chunk_id"]
            if cid not in by_id:
                by_id[cid] = h

        ordered = sorted(by_id.values(), key=lambda x: agg.get(x["chunk_id"], 0.0), reverse=True)
        cut = ordered[: top_k]
        # map to chunks
        chunks: List[Dict[str, Any]] = []
        for r in cut:
            chunks.append({"text": r["text"], "metadata": r["metadata"], "score": agg.get(r["chunk_id"], 0.0), "chunk_id": r["chunk_id"]})
        try:
            self.logger.info(f"RAG.retrieve[mq] kb={kb_id} subs={len(subs)} hits_all={len(all_hits)} fused={len(chunks)} top_k={top_k}")
        except Exception:
            pass
        # fallback: 如完全无命中，退回 basic 策略，保证稳健
        if not chunks:
            try:
                self.logger.warning("RAG.retrieve[mq] got 0 chunks, fallback to basic retrieval")
            except Exception:
                pass
            return self.retrieve(query=query, kb_id=kb_id, top_k=top_k, focus_doc_ids=focus_doc_ids, index_override=index_override)
        # debug footprint for MQ+RRF（裁剪以控制体积）
        try:
            fused_preview = [{"chunk_id": r["chunk_id"], "fused": float(agg.get(r["chunk_id"], 0.0)), "doc": r["metadata"].get("document_id"), "page": r["metadata"].get("page")} for r in ordered[: min(50, len(ordered))]]
            self._last_retrieval_debug = {
                "strategy": "multi_query",
                "subqueries": subs,
                "per_query_hits": per_q_hits,
                "kb_id": kb_id,
                "hits_all": len(all_hits),
                "fused_kept": len(chunks),
                "top_k": top_k,
                "fused_preview": fused_preview,
                "index": (index_override or settings.ES_DEFAULT_INDEX),
            }
        except Exception:
            self._last_retrieval_debug = None
        return chunks

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
