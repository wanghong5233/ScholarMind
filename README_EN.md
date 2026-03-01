# ScholarMind 🧠

> AI assistant platform for researchers: six-layer multi-strategy RAG, DeepResearch closed-loop Agent, Doc Studio ReAct intelligent editing.

---

## 🎯 Live Demo

**[https://demo-scholarmind.wh5233.me](https://demo-scholarmind.wh5233.me)**

Try RAG Q&A, DeepResearch literature review, and Doc Studio editing without local deployment. ⭐ Star the repo if it helps.

---

[![Star](https://img.shields.io/github/stars/wanghong5233/ScholarMind?style=social)](https://github.com/wanghong5233/ScholarMind)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.100%2B-009688.svg)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)
[![Docker](https://img.shields.io/badge/Docker-Compose-2496ED.svg)](https://docs.docker.com/compose/)

**English** | [中文](README.md)

ScholarMind uses a **four-service microservice architecture**, integrating production-grade RAG retrieval, DeepResearch research Agent, and Doc Studio document editing Agent for paper understanding, literature review, academic writing, and knowledge base management. **Preview**: RAG chat, DeepResearch reports, Doc Studio editing → [Live Demo](https://demo-scholarmind.wh5233.me)

---

## Table of Contents

- [Live Demo](#-live-demo)
- [Architecture Overview](#architecture-overview)
- [Core Modules](#core-modules)
  - [RAG Retrieval](#1-rag-retrieval)
  - [DeepResearch](#2-deepresearch)
  - [Doc Studio](#3-doc-studio)
- [Quick Start](#quick-start)
  - [Troubleshooting](#troubleshooting)
- [Tech Stack](#tech-stack)
- [Architecture Highlights](#architecture-highlights)
- [License](#license)

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────────┐
│                React Frontend (Vite + Ant Design)                     │
│   deep_chat  │  Doc Studio  │  Notebook  │  admin                   │
└───────────┬──────────────┬──────────────┬──────────────┬────────────┘
            │              │              │              │
            ▼              ▼              ▼              ▼
┌───────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐
│ scholarmind   │  │ deep_research│  │ doc_studio  │  │ (same)      │
│ _api :8000   │  │   :8004     │  │   :8003     │  │             │
└───────┬───────┘  └──────┬──────┘  └──────┬──────┘  └─────────────┘
        │                  │                │
        │  ◄─── internal token ────────────┘
        │
        ├─ PostgreSQL    (users/sessions/documents/messages/memory/audit)
        ├─ Elasticsearch (dense_vector + BM25 full-text)
        ├─ Redis         (queue/cache/SSE replay)
        ├─ Reranker :8002 (BGE-Reranker)
        └─ MinerU + Grobid (high-fidelity PDF parsing)
```

| Service | Port | Role |
|---------|------|------|
| `scholarmind_api` | 8000 | Main API: auth, RAG chat, session management, document processing |
| `deep_research` | 8004 | DeepResearch Agent: multi-turn research, queue scheduling, report generation |
| `doc_studio` | 8003 | Doc Studio Agent: LaTeX/Markdown editing, file system, HitL confirmation |
| `reranker` | 8002 | Cross-Encoder reranking |

---

## Core Modules

### 1. RAG Retrieval

Six-layer multi-strategy retrieval pipeline with hybrid search over in-session documents and user knowledge bases.

| Stage | Description |
|-------|-------------|
| **Query Variants** | CJK translation, Multi-Query rewriting, HyDE hypothetical documents |
| **Multi-path retrieval** | BM25 + vector × N variants × M indices (session_only / global_only / hybrid) |
| **RRF fusion** | Rank-based dimension-agnostic fusion |
| **MMR diversity** | Jaccard token similarity for relevance vs. diversity balance |
| **Formula context expansion** | ±2 chunks around formula chunks for definitions and explanations |
| **Metadata reranking** | Year, citation count, section, knowledge graph boost, multimodal |
| **Cross-Encoder reranking** | BGE-Reranker-Large INT8 final ordering |

**Document ingestion**: MinerU high-fidelity parsing → Grobid metadata → strategy-aware chunking → Embedding → ES dual index.

**SSE event sequence**: `progress.accepted` → `progress.history` (STM) → `progress.memory` (LTM) → `progress.retrieving` → `progress.rerank` → `delta.*` (streaming tokens) → `completion`. Supports `Last-Event-ID` reconnection replay.

**Memory**: Session KB (in-session docs) + User KB (optional RAG) + STM (recent history) + LTM (cross-session long-term memory).

**Session KB vs User KB**: Session KB is per-session; User KB is cross-session. Both are independent.

---

### 2. DeepResearch

Closed-loop Agent system for deep paper research, literature review, and report generation.

**Six-layer Agent system**:

| Agent | Role |
|-------|------|
| **PlannerAgent** | RAG-aided JSON research plan generation |
| **ResearchAgent** | Beam-Select action scoring, CJK stripping + anchor injection, Observe→Decide→Act loop |
| **NoteAgent** | Compresses summary into bullet notes for Reporter input token control |
| **DecisionAgent** | Evidence quality (sufficient / followup / tool_calls), three-layer LLM fallback |
| **ManagerAgent** | Adaptive topic expansion, prevents queue stall |
| **ReporterAgent** | Section-wise generation, citation filtering, quality gates |

**Tools**: `paper.search` (Semantic Scholar + arXiv), `web.search`, `rag.ask`, `rag.compare`, `web.open_page`, `code.exec`, etc.

**Resilience**: Lease scheduling, watchdog timeout, checkpoint resume, full-chain SSE observability. `MAX_ACTIVE_RUNS=2` global concurrency; overflow enqueued; priority + aging prevents starvation.

**Citation quality**: Two-layer funnel; academic domains exempt; quality gates (min paragraphs, citations, coverage); FAILED if unmet.

---

### 3. Doc Studio

Cursor-like LaTeX/Markdown intelligent editing. ReAct loop + Human-in-the-loop confirmation for risky actions.

| Capability | Description |
|------------|-------------|
| **Ask/Agent modes** | Ask: read-only analysis; Agent: full toolchain execution |
| **Dynamic tool orchestration** | Locate → read segment → precise rewrite; 16 tools with per-tool budget guards |
| **Human-in-the-loop** | Risky ops (e.g., bulk delete) require confirmation; asyncio.Future suspend, up to 900s |
| **Semantic hybrid search** | embedding + lexical n-gram, incremental index + cold-start prewarm |
| **Async cancellable** | SSE events, interrupt and reconnect replay |
| **Multimodal** | Image attachments auto-switch to vision model |
| **LaTeX CJK** | Unicode/CJK detection auto-injects ctex for Chinese compilation |

**Notebook**: Doc Studio workspaceId=`notebook` for DeepResearch report one-click import. System dirs read-only; user dirs fully writable.

**approval_token security**: Path-bound, single-use (`pop()`), TTL cleanup, replay-resistant.

---

## Quick Start

**Requirements**: Docker Compose, 8GB+ RAM, NVIDIA GPU (see [Troubleshooting](#troubleshooting) without GPU), DashScope API Key.

```bash
git clone https://github.com/wanghong5233/ScholarMind.git
cd ScholarMind/backend
cp .env.example .env
# Edit .env: DASHSCOPE_API_KEY, JWT_SECRET_KEY, ELASTIC_PASSWORD
make up-build && make migrate
cd ../frontend && npm run dev   # frontend separate
```

**Access**: Frontend http://localhost:5173 | API docs http://localhost:8000/docs

**Required env vars**: `DASHSCOPE_API_KEY`, `JWT_SECRET_KEY` (`python -c "import secrets; print(secrets.token_hex(32))"`), `ELASTIC_PASSWORD` (8+ chars). Optional: `SEMANTIC_SCHOLAR_API_KEY`, `TAVILY_API_KEY`/`SERPER_API_KEY`. See `backend/.env.example`.

**Commands**: `make up` start | `make down` stop | `make logs-api` logs

### Troubleshooting

| Issue | Solution |
|-------|----------|
| MinerU/Reranker fails (no GPU) | In `docker-compose.yml`: set `dockerfile` to `Dockerfile` for mineru and reranker; comment out `deploy.resources.reservations`. Reranker can use DashScope cloud; MinerU without GPU has limited parsing |
| Elasticsearch timeout | Set `ELASTIC_PASSWORD` in `.env`; ES first start ~2–3 min |
| Blank frontend / 401 | Ensure `JWT_SECRET_KEY` matches backend |
| PDF parsing fails | MinerU needs GPU; verify NVIDIA driver and Container Toolkit |

---

## Tech Stack

| Layer | Technology |
|-------|------------|
| **Backend** | FastAPI, Python 3.11+, SQLAlchemy, Alembic |
| **Database** | PostgreSQL 15, Elasticsearch 8.x, Redis 7 |
| **Frontend** | React 18, TypeScript, Ant Design, Valtio |
| **AI** | DashScope / OpenAI (Embedding, LLM), BGE-Reranker, MinerU, Grobid |
| **Ops** | Docker Compose |

---

## Architecture Highlights

| Highlight | Description |
|-----------|-------------|
| **Three-layer auth** | User Token (7 days) / Admin Token (2h) / Internal Service Token, fully decoupled |
| **SSE auth** | EventSource cannot send headers; supports `?token=` query param (HTTPS recommended) |
| **per-session flow control** | `AskRunControl` ensures at most one active ask per session; new request cancels previous |
| **Reconnection replay** | `AskStreamReplayBuffer` (memory + Redis), `Last-Event-ID` seamless replay |
| **Product isolation** | `sessions.surface` separates deep_chat / doc_studio |
| **GraphRAG** | Entity extraction → graph node match → `boost_chunk_ids` in RAG metadata |
| **MinerU + Grobid** | Dual-engine alignment for high-fidelity academic PDF parsing |

---

## License

[MIT License](./LICENSE).  
If this helps you, consider ⭐ [Star](https://github.com/wanghong5233/ScholarMind).
