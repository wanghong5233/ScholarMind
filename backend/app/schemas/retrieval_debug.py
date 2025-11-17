from __future__ import annotations

from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class RetrievalVariant(BaseModel):
    tag: str
    text: str
    synthetic: bool = False
    language: Optional[str] = None


class RetrievalChunkPreview(BaseModel):
    chunk_id: Optional[str] = None
    score: Optional[float] = None
    document_id: Optional[int] = None
    page: Optional[int] = None
    source: Optional[str] = None
    element_type: Optional[str] = None
    logical_type: Optional[str] = None
    text_preview: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)


class RetrievalPathSample(BaseModel):
    path_id: str
    label: str
    query_tag: str
    source: Optional[str] = None
    hit_count: int
    hits: List[RetrievalChunkPreview]


class PromptSectionDebug(BaseModel):
    role: str
    content: str
    length: int


class RetrievalPreviewRequest(BaseModel):
    kb_id: int
    query: str = Field(..., min_length=1, max_length=400)
    top_k: int = Field(5, ge=1, le=50)
    session_id: Optional[str] = None
    focus_doc_ids: Optional[List[int]] = None
    boost_doc_ids: Optional[List[int]] = None
    index_mode: Optional[str] = None


class RetrievalDebugResponse(BaseModel):
    kb_id: int
    query: str
    top_k: int
    variant_meta: Dict[str, Any] = Field(default_factory=dict)
    variants: List[RetrievalVariant] = Field(default_factory=list)
    index_plan: List[Dict[str, Optional[str]]] = Field(default_factory=list)
    index_mode: Optional[str] = None
    indices_used: List[str] = Field(default_factory=list)
    index_stats: Dict[str, int] = Field(default_factory=dict)
    path_stats: Dict[str, int] = Field(default_factory=dict)
    path_samples: List[RetrievalPathSample] = Field(default_factory=list)
    rrf_candidates: List[RetrievalChunkPreview] = Field(default_factory=list)
    rrf_candidates_count: Optional[int] = Field(default=None, description="RRF融合后的实际候选数")
    mmr_chunks: List[RetrievalChunkPreview] = Field(default_factory=list)
    mmr_output_count: Optional[int] = Field(default=None, description="MMR输出的候选数（给精排的）")
    rerank_top_k: Optional[int] = Field(default=None, description="精排候选数（MMR输出数）")
    rerank_candidates: List[RetrievalChunkPreview] = Field(default_factory=list, description="精排前的候选chunks")
    rerank_scores: List[float] = Field(default_factory=list, description="精排后的分数列表")
    rerank_enabled: bool = Field(default=False, description="是否启用了精排")
    final_chunks: List[RetrievalChunkPreview] = Field(default_factory=list)
    memory: Dict[str, Any] = Field(default_factory=dict)
    prompt_sections: List[PromptSectionDebug] = Field(default_factory=list)
    prompt_total_chars: int = 0
    prompt_context_chars: int = 0

