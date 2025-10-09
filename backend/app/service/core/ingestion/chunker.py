from __future__ import annotations

from typing import Iterable, List
from service.core.ingestion.interfaces import ParsedBlock, Chunker


class RecursiveCharacterChunker(Chunker):
    def __init__(self, target_chars: int = 2000, overlap: int = 200) -> None:
        self.target_chars = target_chars
        self.overlap = overlap

    def chunk(self, *, blocks: Iterable[ParsedBlock]) -> List[ParsedBlock]:
        results: List[ParsedBlock] = []
        for b in blocks:
            text = b.text or ""
            if not text:
                continue
            start = 0
            while start < len(text):
                end = min(len(text), start + self.target_chars)
                chunk_txt = text[start:end]
                results.append(ParsedBlock(text=chunk_txt, metadata=dict(b.metadata)))
                if end >= len(text):
                    break
                start = end - self.overlap
                if start < 0:
                    start = 0
        return results


