"""Token usage tracker for DeepResearch LLM calls."""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from config import settings


class TokenUsageTracker:
    """Aggregate token usage and estimated cost across agents."""

    def __init__(
        self,
        price_table: Optional[Dict[str, Dict[str, float]]] = None,
        default_input_per_1k: float = 0.0,
        default_output_per_1k: float = 0.0,
        snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        self._by_agent: Dict[str, Dict[str, Any]] = {}
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._total_tokens = 0
        self._total_cost = 0.0
        self._price_table = price_table or {}
        self._default_input_per_1k = max(0.0, float(default_input_per_1k or 0.0))
        self._default_output_per_1k = max(0.0, float(default_output_per_1k or 0.0))
        self._pricing_enabled = bool(self._price_table) or self._default_input_per_1k > 0 or self._default_output_per_1k > 0

        if snapshot:
            self._load_snapshot(snapshot)

    @classmethod
    def from_settings(cls, snapshot: Optional[Dict[str, Any]] = None) -> "TokenUsageTracker":
        price_table = cls._load_price_table(getattr(settings, "LLM_PRICE_TABLE_JSON", None))
        return cls(
            price_table=price_table,
            default_input_per_1k=getattr(settings, "LLM_DEFAULT_INPUT_USD_PER_1K", 0.0),
            default_output_per_1k=getattr(settings, "LLM_DEFAULT_OUTPUT_USD_PER_1K", 0.0),
            snapshot=snapshot,
        )

    def record(self, payload: Dict[str, Any]) -> None:
        label = str(payload.get("label") or "unknown")
        model = str(payload.get("model") or "unknown")
        prompt_tokens = int(payload.get("prompt_tokens") or 0)
        completion_tokens = int(payload.get("completion_tokens") or 0)
        total_tokens = int(payload.get("total_tokens") or (prompt_tokens + completion_tokens))
        if total_tokens <= 0 and prompt_tokens <= 0 and completion_tokens <= 0:
            return

        self._prompt_tokens += prompt_tokens
        self._completion_tokens += completion_tokens
        self._total_tokens += total_tokens

        agent = self._by_agent.setdefault(
            label,
            {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
                "estimated_cost_usd": None,
                "models": [],
                "calls": 0,
            },
        )
        agent["prompt_tokens"] += prompt_tokens
        agent["completion_tokens"] += completion_tokens
        agent["total_tokens"] += total_tokens
        agent["calls"] += 1
        models = agent.get("models") or []
        if model and model not in models:
            models.append(model)
        agent["models"] = models

        cost = self._estimate_cost(model, prompt_tokens, completion_tokens)
        if cost is not None:
            agent_cost = float(agent.get("estimated_cost_usd") or 0.0) + cost
            agent["estimated_cost_usd"] = round(agent_cost, 6)
            self._total_cost += cost

    def summary(self) -> Dict[str, Any]:
        return {
            "prompt_tokens": self._prompt_tokens,
            "completion_tokens": self._completion_tokens,
            "total_tokens": self._total_tokens,
            "estimated_cost_usd": round(self._total_cost, 6) if self._pricing_enabled else None,
            "by_agent": self._sorted_agents(),
            "pricing": {
                "configured": self._pricing_enabled,
                "default_input_usd_per_1k": self._default_input_per_1k,
                "default_output_usd_per_1k": self._default_output_per_1k,
                "model_prices": self._price_table,
            },
        }

    def _estimate_cost(self, model: str, prompt_tokens: int, completion_tokens: int) -> Optional[float]:
        if not self._pricing_enabled:
            return None
        prices = self._price_table.get(model) or {}
        input_price = float(prices.get("input", self._default_input_per_1k))
        output_price = float(prices.get("output", self._default_output_per_1k))
        if input_price <= 0 and output_price <= 0:
            return None
        return (prompt_tokens / 1000.0) * input_price + (completion_tokens / 1000.0) * output_price

    def _sorted_agents(self) -> Dict[str, Dict[str, Any]]:
        return {label: self._by_agent[label] for label in sorted(self._by_agent.keys())}

    def _load_snapshot(self, snapshot: Dict[str, Any]) -> None:
        self._prompt_tokens = int(snapshot.get("prompt_tokens") or 0)
        self._completion_tokens = int(snapshot.get("completion_tokens") or 0)
        self._total_tokens = int(snapshot.get("total_tokens") or 0)
        if self._pricing_enabled:
            self._total_cost = float(snapshot.get("estimated_cost_usd") or 0.0)
        by_agent = snapshot.get("by_agent") or {}
        if isinstance(by_agent, dict):
            for label, data in by_agent.items():
                if not isinstance(data, dict):
                    continue
                self._by_agent[str(label)] = {
                    "prompt_tokens": int(data.get("prompt_tokens") or 0),
                    "completion_tokens": int(data.get("completion_tokens") or 0),
                    "total_tokens": int(data.get("total_tokens") or 0),
                    "estimated_cost_usd": data.get("estimated_cost_usd"),
                    "models": list(data.get("models") or []),
                    "calls": int(data.get("calls") or 0),
                }

    @staticmethod
    def _load_price_table(raw: Optional[str]) -> Dict[str, Dict[str, float]]:
        if not raw:
            return {}
        try:
            data = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            return {}
        if not isinstance(data, dict):
            return {}
        table: Dict[str, Dict[str, float]] = {}
        for model, prices in data.items():
            if not isinstance(prices, dict):
                continue
            table[str(model)] = {
                "input": float(prices.get("input") or 0.0),
                "output": float(prices.get("output") or 0.0),
            }
        return table
