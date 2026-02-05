# Chat API

A FastAPI-based chat application with RAG (Retrieval-Augmented Generation) capabilities, supporting real-time WebSocket communication, background job processing with Redis Queue (RQ), and a PostgreSQL backend (Neon).

## Features

- **FastAPI** REST API with WebSocket support
- **JWT Authentication** for secure API access
- **Background Job Processing** using Redis Queue (RQ)
- **RAG Pipeline** for document ingestion and retrieval
- **PostgreSQL (Neon)** for application data storage
- **Cloudflare R2** (S3-compatible) for file storage
- **Docker** support for easy deployment

## Prerequisites

- Docker and Docker Compose installed
- `public.pem` file for JWT token verification (RS256)
- Environment variables configured (see `.env.example`)

## Quick Start with Docker

### 1. Clone the Repository

```bash
git clone <repository-url>
cd chat_api
```

### 2. Set Up Environment Variables

Copy the example environment file and fill in your values:

```bash
cp .env.example .env.local
```

Edit `.env.local` with your actual configuration values. See [Environment Variables](#environment-variables) section for details.

### 3. Add JWT Public Key

Place your `public.pem` file in the project root directory. This file is used to verify JWT tokens from your authentication service.

### 4. Start Services

Start all services (API, workers, and Redis) using Docker Compose:

```bash
docker-compose up -d
```

This will:
- Build the API and worker containers
- Start Redis container
- Start the FastAPI server on port 8000
- Start RQ workers for background job processing

### 5. Verify Installation

Check if the API is running:

```bash
curl http://localhost:8000/docs
```

You should see the FastAPI interactive documentation.

## Docker Services

The `docker-compose.yml` file defines three services:

### `api`
- **Port**: 8000
- **Command**: `uvicorn app.main:app --host 0.0.0.0 --port 8000`
- **Purpose**: Main FastAPI application server

### `workers`
- **Command**: `rq worker default`
- **Purpose**: Background job workers for processing training sources (URLs, files)

### `redis`
- **Port**: 6379 (exposed for local development)
- **Purpose**: Message queue and caching for RQ workers

## Environment Variables

Create a `.env.local` file (or `.env` for production) with the following variables:

| Variable | Description | Example |
|----------|-------------|---------|
| `APP_ENV` | Application environment | `development` or `production` |
| `DASHBOARD_DB_HOST` | Dashboard DB host | `your-rds.amazonaws.com` |
| `DASHBOARD_DB_PORT` | Dashboard DB port | `5432` |
| `DASHBOARD_DB_USERNAME` | Dashboard DB username | `postgres` |
| `DASHBOARD_DB_PASSWORD` | Dashboard DB password | `********` |
| `DASHBOARD_DB_NAME` | Dashboard DB database name | `postgres` |
| `CHAT_DB_HOST` | Neon host | `ep-...neon.tech` |
| `CHAT_DB_PORT` | Neon port | `5432` |
| `CHAT_DB_USERNAME` | Neon username | `neondb_owner` |
| `CHAT_DB_PASSWORD` | Neon password | `********` |
| `CHAT_DB_NAME` | Neon database name | `neondb` |
| `OPENAI_API_KEY` | OpenAI API key for LLM responses | `sk-...` |
| `R2_ACCOUNT_ID` | Cloudflare account id for R2 S3 endpoint | `xxxxxxxxxxxxxxxxxxxx` |
| `ACCESS_KEY_ID` | R2 access key id | `xxxxxxxx` |
| `SECRET_ACCESS_KEY` | R2 secret access key | `xxxxxxxx` |
| `CLOUDFLARE_R2_BASE_URL` | (Optional) public base URL for objects | `https://pub-....r2.dev` |
| `R2_API_KEY_TOKEN` | (Optional) Cloudflare API token (not used by S3 client) | `xxxxxxxx` |
| `USER_AGENT` | User agent for HTTP requests | `Mozilla/5.0 (compatible; ChatAPI/1.0)` |
| `LOG_LEVEL` | Logging level | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `REDIS_URL` | Redis connection URL | `redis://redis:6379/0` (Docker) or `redis://localhost:6379/0` (local) |

**Note**: In Docker Compose, use `redis://redis:6379/0` where `redis` resolves to the Redis container hostname. For production, use your managed Redis service URL.

## Project Structure

```
chat_api/
├── app/                          # Main application package
│   ├── api/                      # API layer
│   │   ├── middleware/           # HTTP middleware (JWT authentication)
│   │   │   └── jwt.py           # JWT verification middleware
│   │   ├── routes/              # API route handlers
│   │   │   ├── training.py      # Training source queue endpoints
│   │   │   └── ws_chat.py       # WebSocket chat endpoint
│   │   └── router.py            # API router aggregation
│   ├── core/                     # Core utilities
│   │   ├── env.py               # Environment variable loading
│   │   └── jwt.py               # JWT token verification
│   ├── db/                       # Database configuration
│   │   └── session.py           # SQLAlchemy session factories
│   ├── domain/                   # Domain models (Pydantic)
│   │   └── chat.py              # Chat session models
│   ├── helpers/                  # Helper utilities
│   │   └── utils.py             # Text cleaning, R2 storage helpers
│   ├── infra/                     # Infrastructure
│   │   ├── redis_client.py      # Redis client configuration
│   │   └── r2_storage.py        # Cloudflare R2 (S3) client helpers
│   ├── models/                    # SQLAlchemy ORM models
│   │   ├── chat_db_models.py      # Local Python chat database models
│   │   └── dashboard_db_models.py            # Public schema models (users/orgs/bots/files/etc.)
│   ├── services/                  # Business logic
│   │   ├── chat.py              # Chat message handling
│   │   └── worker_fns.py       # Background job functions (URL/file processing)
│   ├── ws/                        # WebSocket utilities
│   │   └── auth.py              # WebSocket authentication
│   ├── logging_config.py         # Logging configuration
│   └── main.py                   # FastAPI application entry point
├── logs/                          # Application logs
├── .dockerignore                  # Docker ignore patterns
├── .env.example                   # Environment variables template
├── docker-compose.yml             # Docker Compose configuration
├── Dockerfile                     # Docker image definition
├── public.pem                     # JWT public key (RS256)
├── python_chat_db_schema.txt      # Schema of postgres database managed by python server
├── dashboard_db_schema.txt             # Dashboard Database Schema reference
└── requirements.txt               # Python dependencies
```

### Directory Descriptions

- **`app/api/`**: HTTP API layer with routes, middleware, and request handling
- **`app/core/`**: Core utilities used across the application (env loading, JWT)
- **`app/db/`**: Database connection and session management
- **`app/domain/`**: Pydantic models for request/response validation
- **`app/helpers/`**: Reusable utility functions (text processing, storage helpers)
- **`app/infra/`**: Infrastructure components (Redis, external services)
- **`app/models/`**: SQLAlchemy ORM models for database tables
- **`app/services/`**: Business logic and service layer functions
- **`app/ws/`**: WebSocket-specific authentication and utilities

## Running Workers

Background workers process training jobs (URL scraping, file processing). They run automatically with Docker Compose, but you can also run them manually:

```bash
# Using Docker Compose
docker-compose up workers

# Or locally (requires Redis running)
rq worker default
```

## Development

### Local Development (without Docker)

1. Create and activate virtual environment:
```bash
python -m venv chat_env
source chat_env/bin/activate  # On Windows: chat_env\Scripts\activate
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables in `.env.local`

4. Start Redis locally:
```bash
docker run --rm -p 6379:6379 redis:7
```

5. Run the application:
```bash
uvicorn app.main:app --reload
```

6. Run workers in a separate terminal:
```bash
rq worker default
```

## API Endpoints

### Training
- `POST /api/training/queue` - Queue a training job for processing
- `DELETE /api/training/delete/{source_id}` - Delete a training source

### Chat
- `WS /api/chat/ws` - WebSocket endpoint for real-time chat

### Documentation
- `GET /docs` - Interactive API documentation (Swagger UI)
- `GET /redoc` - Alternative API documentation (ReDoc)

## Database Schemas

### Python Chat Database (`python_chat_db_schema.txt`)
Local PostgreSQL database for chat messages, documents, embeddings, and training jobs. See `python_chat_db_schema.txt` for the complete schema.

### Public Schema (Neon / Postgres)
Application tables for users, organizations, bots, API keys, training sources, files, and conversations. Models are defined in `app/models/public.py`.

## Logging

Logs are written to:
- **Console**: Pretty-printed JSON format (development)
- **File**: `logs/app.log` - Timed rotating log file

Log level is controlled by the `LOG_LEVEL` environment variable.

## Production Deployment

1. Set `APP_ENV=production` in your environment
2. Use managed Redis service (update `REDIS_URL`)
3. Set up proper secrets management (don't commit `.env` files)
4. Configure reverse proxy (Nginx/Traefik) if needed
5. Use multiple worker instances for scalability

## License

Copyright (c) 2026 ILAMUHIL ILAVENIL. All rights reserved.

This software and associated documentation files (the "Software") are proprietary 
and confidential. Unauthorized copying, modification, distribution, or use of 
this Software, via any medium, is strictly prohibited without express written 
permission from the copyright holder.
