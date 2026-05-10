from __future__ import annotations

import argparse
import re
from pathlib import Path
from typing import Iterable


ROOT = Path(__file__).resolve().parents[1]

DEFAULT_TARGETS = (
    "app/service/core/conversation",
    "app/service/core/rag/llm/client.py",
    "app/service/core/rag/history/long_term_memory.py",
    "app/service/core/rag/service.py",
    "app/service/core/rag/graph/extractor.py",
    "app/service/core/ingestion/mineru_parser.py",
    "services/doc_studio/service/llm_client.py",
    "services/doc_studio/service/agent_service.py",
    "services/doc_studio/service/tools/analysis_tools.py",
    "services/deep_research/service/llm_client.py",
    "services/deep_research/service/pipeline.py",
    "services/deep_research/service/tools/rag_ask_tool.py",
)

TOKEN_LITERAL_RE = re.compile(r"\b(max_tokens|max_completion_tokens)\s*=\s*(\d+)\b")
TEMPERATURE_LITERAL_RE = re.compile(r"\btemperature\s*=\s*([01](?:\.\d+)?)\b")
LEGACY_POLICY_VERSION_RE = re.compile(r"\bpolicy_version\b\s*[:=]\s*[\"']legacy[\"']")


def _iter_python_files(targets: Iterable[str]) -> Iterable[Path]:
    for raw in targets:
        path = (ROOT / raw).resolve()
        if path.is_file() and path.suffix == ".py":
            yield path
            continue
        if path.is_dir():
            for item in sorted(path.rglob("*.py")):
                yield item


def _allow_line(line: str) -> bool:
    normalized = line.strip()
    if not normalized:
        return True
    if normalized.startswith("#"):
        return True
    if "# llm-policy-allow" in normalized:
        return True
    if "settings." in normalized:
        return True
    if "getattr(settings" in normalized:
        return True
    if "policy." in normalized or "_policy" in normalized:
        return True
    if "override_" in normalized:
        return True
    return False


def scan_magic_literals(targets: Iterable[str]) -> list[str]:
    violations: list[str] = []
    for path in _iter_python_files(targets):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as exc:
            violations.append(f"{path}: read_error={exc}")
            continue
        for idx, line in enumerate(content.splitlines(), start=1):
            if _allow_line(line):
                continue
            token_match = TOKEN_LITERAL_RE.search(line)
            temp_match = TEMPERATURE_LITERAL_RE.search(line)
            legacy_policy_match = LEGACY_POLICY_VERSION_RE.search(line)
            if token_match or temp_match or legacy_policy_match:
                rel = path.relative_to(ROOT)
                violations.append(f"{rel}:{idx}: {line.strip()}")
    return violations


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Guardrail check: forbid new LLM magic-number literals in migrated paths.",
    )
    parser.add_argument(
        "--targets",
        type=str,
        default=",".join(DEFAULT_TARGETS),
        help="Comma-separated target files/directories (relative to backend/).",
    )
    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    targets = [item.strip() for item in str(args.targets or "").split(",") if item.strip()]
    if not targets:
        raise SystemExit("No guardrail targets configured.")
    violations = scan_magic_literals(targets)
    if violations:
        print("LLM policy guardrail check failed. Found literal assignments:")
        for item in violations:
            print(f"- {item}")
        raise SystemExit(1)
    print("LLM policy guardrail check passed.")


if __name__ == "__main__":
    main()
