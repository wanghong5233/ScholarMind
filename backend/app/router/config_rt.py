from fastapi import APIRouter
from core.config import settings
from typing import Any, Dict

# 解析健康检查所需的轻量依赖
try:
    from nltk import word_tokenize as _wt
except Exception:  # pragma: no cover
    _wt = None

try:
    import fitz as _fitz  # PyMuPDF
except Exception:  # pragma: no cover
    _fitz = None

from service.core.rag.nlp.rag_tokenizer import RagTokenizer
from importlib import import_module

router = APIRouter()


@router.get("/feature-flags")
def get_feature_flags():
    return {
        "retrievalStrategy": settings.SM_RETRIEVAL_STRATEGY,
        "rerankerStrategy": settings.SM_RERANKER_STRATEGY,
        "enableCitations": settings.SM_ENABLE_CITATIONS,
        "streamingEnabled": settings.SM_STREAMING_ENABLED,
        "defaultLanguage": settings.SM_DEFAULT_LANGUAGE,
        "multiQueryNum": settings.SM_MULTI_QUERY_NUM,
        "hydeEnabled": settings.SM_HYDE_ENABLED,
        "ragTopK": settings.SM_RAG_TOPK,
        "retrievePageSize": settings.SM_RETRIEVE_PAGE_SIZE,
        "maxTokens": settings.SM_MAX_TOKENS,
        "temperature": settings.SM_TEMPERATURE,
        # history controls
        "historyMaxTokens": settings.SM_HISTORY_MAX_TOKENS,
        "historyHeadroom": settings.SM_HISTORY_HEADROOM,
        "historyRecentTurns": settings.HISTORY_RECENT_TURNS,
        "enableRollingSummary": settings.ENABLE_ROLLING_SUMMARY,
    }


@router.get("/parsing-health")
def parsing_health() -> Dict[str, Any]:
    """轻量自检：解析链路关键依赖可用性。
    不读取真实文件，避免重 IO/CPU。
    """
    deepdoc_import = True
    try:
        import_module("service.core.rag.app.naive")
    except Exception:
        deepdoc_import = False

    nltk_punkt_ok = True
    if _wt is None:
        nltk_punkt_ok = False
    else:
        try:
            _ = _wt("Hello world, this is a test.")
        except Exception:
            nltk_punkt_ok = False

    pymupdf_ok = _fitz is not None

    rag_tokenizer_ok = True
    try:
        _rt = RagTokenizer()
        _ = _rt.tokenize("Hello world, This is tokenizer smoke test.")
    except Exception:
        rag_tokenizer_ok = False

    return {
        "deepdoc_import": deepdoc_import,
        "nltk_punkt_ok": nltk_punkt_ok,
        "pymupdf_ok": pymupdf_ok,
        "rag_tokenizer_ok": rag_tokenizer_ok,
    }
