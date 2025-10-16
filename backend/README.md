# ScholarMind - Backend Service

> **Note**: For comprehensive technical documentation, architecture diagrams, and detailed API references, please refer to **[`readme/readme.md`](./readme/readme.md)**.

This directory contains the backend service for ScholarMind, an AI-powered research assistant for academic literature.

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose installed
- A configured `.env` file (copy from `.env.example` and fill in your API keys)

### Launch All Services

```bash
# From the backend directory
docker compose up -d --build
```

This will start:
- **API Server** (`scholarmind_api`) on port 8000
- **PostgreSQL** (`scholarmind_db`) on port 5432
- **Elasticsearch** (`scholarmind_vector`) on port 9200
- **Redis** (`scholarmind_redis`) on port 6379

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

## 📚 Further Reading

- **[Technical Architecture & Flow Diagrams](./readme/readme.md)** - In-depth system design
- **[Alembic Migrations Guide](./app/alembic/README.md)** - Database schema management
- **[API Reference](http://localhost:8000/docs)** - Interactive Swagger documentation
