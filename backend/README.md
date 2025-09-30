# ScholarMind - Backend Service

This directory contains the backend service for the ScholarMind project, an AI-powered research assistant for academic literature.

## 🚀 Quick Start

### Prerequisites

- Docker and Docker Compose
- A configured `.env` file in this directory (you can copy `.env.example`).

### Launching the Service

From the `backend` directory, run the following command:

```bash
# Build and start all services in detached mode
docker compose up -d --build
```

### Checking Service Status

```bash
# List all running services for the project
docker compose ps

# Check the health of the API
curl http://localhost:8000/docs
```

## 🔧 Development & Debugging

### Tailing Logs

```bash
# Follow the logs for the main API service
docker compose logs -f scholarmind_api

# Follow logs for other services
docker compose logs -f scholarmind_db
docker compose logs -f scholarmind_vector
```

### Accessing Containers

```bash
# Get a shell inside the main API container
docker compose exec scholarmind_api bash

# Connect to the PostgreSQL database
docker compose exec scholarmind_db psql -U postgres -d gsk
```
