# ScholarMind - Backend Service

> **Note**: For comprehensive technical documentation, architecture diagrams, and detailed API references, please refer to **[`readme/readme.md`](./readme/readme.md)**.

This directory contains the backend service for ScholarMind, an AI-powered research assistant for academic literature.

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose installed
- A configured `.env` file (copy from `.env.example` and fill in your API keys)

### Launch Low-Cost Demo Services

```bash
# From the backend directory
docker compose up -d --build
```

This will start:
- **API Server** (`scholarmind_api`) on port 8000
- **PostgreSQL + pgvector** (`scholarmind_db`) on port 5432
- **Redis** (`scholarmind_redis`) on port 6379

Heavy local services are disabled by default. MinerU, Grobid, and the local
reranker remain available for full local demos:

```bash
docker compose --profile heavy-local up -d --build
```

The default parser order prefers remote/lightweight parsing
(`llamaparse,unstructured_api,unstructured,pymupdf`) and keeps citation
metadata such as page, page range, bounding boxes, and structure labels when the
selected parser provides them.

### Stable Public Demo (Cloudflare Tunnel + Vercel)

To avoid intermittent `Network Error` caused by unstable QUIC(UDP), the compose file includes a
`cloudflared` service (profile: `public`) pinned to a fixed version and forced to use `http2` (TCP).

```bash
# 1) Configure tunnel token
# Edit .env and set: CF_TUNNEL_TOKEN=your_real_token

# 2) Start backend + public tunnel
docker compose --profile public up -d --build
```

Check runtime status:

```bash
# Cloudflare Tunnel logs
docker compose logs -f cloudflared

# Local health
curl http://localhost:8000/health

# Public health (replace with your tunnel hostname)
curl https://api-scholarmind.wh5233.me/health
```

Optional auto-recovery watchdog (Windows Task Scheduler):

```bash
# 1) Set the public health URL in .env (recommended)
# SM_PUBLIC_HEALTH_URL=https://api-scholarmind.wh5233.me/health

# 2) Install the watchdog task (runs every 1 minute)
powershell -ExecutionPolicy Bypass -File .\scripts\install_tunnel_watchdog_task.ps1

# 3) Query task status
schtasks /Query /TN "ScholarMind-Tunnel-Watchdog" /V /FO LIST

# 4) Read watchdog logs
type .\.watchdog\tunnel_watchdog.log
```

The watchdog only restarts `cloudflared` when:
- Local API `/health` is healthy
- Public `/health` fails continuously for 3 checks
- Cooldown window and daily restart cap both allow recovery

This avoids restart storms while keeping your public demo available.

### Verify Services

```bash
# Check service status
docker compose ps

# View API documentation
curl http://localhost:8000/docs
# Or open in browser: http://localhost:8000/docs
```

## 🔧 Common Operations

### View Logs

```bash
# Follow logs for the main API service
docker compose logs -f scholarmind_api

# View all services
docker compose logs -f
```

### Access Containers

```bash
# Get a shell inside the API container
docker compose exec scholarmind_api bash

# Connect to PostgreSQL
docker compose exec scholarmind_db psql -U postgres -d gsk
```

### Database Migrations

```bash
# Run pending migrations
docker compose exec scholarmind_api alembic upgrade head

# Create a new migration
docker compose exec scholarmind_api alembic revision -m "description"
```

See **[`app/alembic/README.md`](./app/alembic/README.md)** for detailed migration guide.

### Vector Store

ScholarMind now uses PostgreSQL + pgvector as the default vector store:

- PostgreSQL uses `pgvector/pgvector:pg15` so the `vector` extension is available.
- `20_add_pgvector_rag_chunks.py` creates the `rag_chunks` table and vector/text indexes for RAG chunks.
- `21_add_pgvector_ltm_facts.py` creates the `ltm_facts` table for long-term memory recall.
- `SM_VECTOR_STORE=pgvector` is the default. `elasticsearch` remains only as a temporary rollback setting during migration.

## 📚 Further Reading

- **[Technical Architecture & Flow Diagrams](./readme/readme.md)** - In-depth system design
- **[Alembic Migrations Guide](./app/alembic/README.md)** - Database schema management
- **[API Reference](http://localhost:8000/docs)** - Interactive Swagger documentation
