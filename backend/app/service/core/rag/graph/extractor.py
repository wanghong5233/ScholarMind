"""LLM-backed extraction for knowledge graph building."""

from __future__ import annotations

from dataclasses import dataclass
import json
import re
from typing import Any, Dict, List, Optional, Tuple

from core.config import settings
from service.core.rag.llm.client import LLMClient


@dataclass
class GraphExtraction:
    entities: List[Dict[str, Any]]
    relations: List[Dict[str, Any]]


def _safe_json_loads(raw: str) -> Dict[str, Any]:
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        pass
    # Try to extract JSON object or array
    for pattern in (r"\{.*\}", r"\[.*\]"):
        match = re.search(pattern, raw, flags=re.DOTALL)
        if not match:
            continue
        try:
            return json.loads(match.group(0))
        except Exception:
            continue
    return {}


class GraphExtractor:
    """Extract entities and relations for knowledge graph building."""

    def __init__(self, llm_client: Optional[LLMClient] = None) -> None:
        self.llm = llm_client or LLMClient()

    def extract_from_text(
        self,
        text: str,
        *,
        max_entities: int,
        max_relations: int,
        context_hint: Optional[str] = None,
    ) -> GraphExtraction:
        if not text or not settings.SM_GRAPH_ENABLE_LLM:
            return GraphExtraction(entities=[], relations=[])

        limit = int(getattr(settings, "SM_GRAPH_TEXT_TRUNCATE_CHARS", 1800) or 1800)
        text = (text or "").strip()
        if len(text) > limit:
            text = text[:limit]

        system_prompt = (
            "You extract academic knowledge graph facts. "
            "Return JSON only with keys: entities, relations. "
            "entities: list of {name, type, aliases}. "
            "relations: list of {head, relation, tail}."
        )
        user_prompt = (
            f"Text:\n{text}\n\n"
            f"Constraints:\n"
            f"- Max entities: {max_entities}\n"
            f"- Max relations: {max_relations}\n"
            "- Prefer canonical academic terms\n"
            "- Keep relation labels concise\n"
        )
        if context_hint:
            user_prompt = f"{context_hint}\n\n{user_prompt}"

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            raw = self.llm.generate(
                messages,
                temperature=0.1,
                max_tokens=int(getattr(settings, "SM_GRAPH_LLM_MAX_TOKENS", 512) or 512),
                stream=False,
            )
        except Exception:
            return GraphExtraction(entities=[], relations=[])
        payload = _safe_json_loads(str(raw))
        entities = payload.get("entities") if isinstance(payload, dict) else []
        relations = payload.get("relations") if isinstance(payload, dict) else []
        entities = [e for e in entities if isinstance(e, dict)] if isinstance(entities, list) else []
        relations = [r for r in relations if isinstance(r, dict)] if isinstance(relations, list) else []
        return GraphExtraction(
            entities=entities[:max_entities],
            relations=relations[:max_relations],
        )

    def extract_query_entities(self, query: str, *, max_entities: int) -> List[str]:
        if not query:
            return []
        if not settings.SM_GRAPH_ENABLE_LLM:
            return self._fallback_query_entities(query, max_entities=max_entities)

        system_prompt = (
            "Extract core entities from the query. "
            "Return a JSON array of entity names only."
        )
        user_prompt = f"Query:\n{query}\n\nMax entities: {max_entities}"
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]
        try:
            raw = self.llm.generate(
                messages,
                temperature=0.1,
                max_tokens=int(getattr(settings, "SM_GRAPH_QUERY_MAX_TOKENS", 256) or 256),
                stream=False,
            )
        except Exception:
            return self._fallback_query_entities(query, max_entities=max_entities)
        payload = _safe_json_loads(str(raw))
        if isinstance(payload, list):
            names = [str(item).strip() for item in payload if str(item).strip()]
            return names[:max_entities]
        if isinstance(payload, dict) and isinstance(payload.get("entities"), list):
            names = [str(item).strip() for item in payload.get("entities") if str(item).strip()]
            return names[:max_entities]
        return self._fallback_query_entities(query, max_entities=max_entities)

    def _fallback_query_entities(self, query: str, *, max_entities: int) -> List[str]:
        tokens = re.findall(r"[A-Za-z][A-Za-z0-9\-\_]+", query or "")
        seen: set[str] = set()
        results: List[str] = []
        for token in tokens:
            key = token.lower()
            if key in seen:
                continue
            seen.add(key)
            results.append(token)
            if len(results) >= max_entities:
                break
        return results
