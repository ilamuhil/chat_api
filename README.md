# Chat API

A FastAPI-based chat application with RAG (Retrieval-Augmented Generation) capabilities, supporting real-time WebSocket communication, background job processing with Redis Queue (RQ), and PostgreSQL-backed storage.

## Features

- **FastAPI** REST API with WebSocket support
- **JWT Authentication** for secure API access
- **Background Job Processing** using Redis Queue (RQ)
- **RAG Pipeline** for document ingestion and retrieval
- **Hybrid retrieval** using PostgreSQL full-text search and pgvector semantic search
- **Async database access** for API and WebSocket request paths
- **Two Postgres databases**
  - **Dashboard DB** (used by the Next.js dashboard): orgs/bots/training sources/files
  - **Chat DB** (managed by this service, typically Neon): documents/embeddings/messages/training jobs
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
docker compose up -d
```

This will:
- Build the API and worker containers
- Start Redis container
- Start the FastAPI server at `http://localhost:8100`
- Start RQ workers for background job processing

### 5. Verify Installation

Check if the API is running:

```bash
curl http://localhost:8100/docs
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
| `PYTHON_CHAT_DB_HOST` | Chat DB host (Neon, etc.) | `ep-...neon.tech` |
| `PYTHON_CHAT_DB_PORT` | Chat DB port | `5432` |
| `PYTHON_CHAT_DB_USERNAME` | Chat DB username | `neondb_owner` |
| `PYTHON_CHAT_DB_PASSWORD` | Chat DB password | `********` |
| `PYTHON_CHAT_DB_NAME` | Chat DB database name | `neondb` |
| `OPENAI_API_KEY` | OpenAI API key for LLM responses | `sk-...` |
| `OPENAI_MODEL` | (Optional) OpenAI model | `gpt-4o-mini` |
| `R2_ACCOUNT_ID` | Cloudflare account id for R2 S3 endpoint | `xxxxxxxxxxxxxxxxxxxx` |
| `ACCESS_KEY_ID` | R2 access key id | `xxxxxxxx` |
| `SECRET_ACCESS_KEY` | R2 secret access key | `xxxxxxxx` |
| `CLOUDFLARE_R2_BASE_URL` | (Optional) public base URL for objects | `https://pub-....r2.dev` |
| `R2_API_KEY_TOKEN` | (Optional) Cloudflare API token (not used by S3 client) | `xxxxxxxx` |
| `LOG_LEVEL` | Minimum level for console and what the app emits | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `LOG_DIR` | (Optional) Base directory for file logs | `logs` |
| `DEBUG_LOG_DIR` | (Optional) DEBUG-only rotating files | `logs/debug_logs` |
| `INFO_LOG_DIR` | (Optional) INFO and WARNING files | `logs/info_logs` |
| `ERROR_LOG_DIR` | (Optional) ERROR, CRITICAL, and exceptions | `logs/error_logs` |
| `REDIS_URL` | Redis connection URL | `redis://redis:6379/0` (Docker) or `redis://localhost:6379/0` (local) |

**Note**: In Docker Compose, use `redis://redis:6379/0` where `redis` resolves to the Redis container hostname. For production, use your managed Redis service URL.

## Databases (high-level)

- **Dashboard DB**: tables defined in `app/models/dashboard_db_models.py` and documented in `dashboard_db_schema.txt`.
- **Chat DB**: tables defined in `app/models/chat_db_models.py` and documented in `chat_db_schema.txt`.

The service connects to both via environment variables in `app/db/session.py`.
The API uses pooled `AsyncSession` factories for database work in async routes
and WebSocket message handling. Synchronous sessions remain for RQ workers and
other explicitly synchronous callbacks.

## RAG and ingestion

Training workers use `app.ingestion.pipeline.IngestionPipeline` to extract each
source into knowledge units, consolidate them with the configuration's minimum,
target, and maximum token limits, and persist documents and embeddings:

- URL and uploaded HTML content use the HTML extractor/cleaner, Markdown
  converter, and shared Markdown parser.
- PDFs use the shared Docling converter and token-aware PDF parser.
- DOCX files use the Docling extractor and shared Markdown parser.
- CSV files use the table-oriented CSV parser.
- Markdown files use the shared Markdown parser; TXT files use the text pipeline.

Supported file extensions are `.csv`, `.docx`, `.html`, `.htm`, `.md`, `.markdown`,
`.pdf`, and `.txt`. XLSX ingestion is not implemented. Unknown types fail explicitly.
Filename metadata takes precedence over MIME type when selecting a parser.

Knowledge-unit metadata preserves source IDs, original filenames or URLs,
heading paths, content types, and parser-specific page/table information.
Document text includes heading context. Embedding and tokenization helpers live
in `app/rag/embeddings.py`.

Each source's documents and embeddings are committed together. Retrying a source
replaces only the documents for its embedding configuration; a failed extraction
or embedding leaves existing documents intact. Failed sources can be queued
again. Configuration states track readiness only: unsuccessful training returns
a new configuration to `draft`, and existing active configurations stay active.
Training activates a configuration pair only after every requested source succeeds.

Source and job `error_message` columns store JSONB arrays. Each error includes
`id`, `code`, `stage`, `message`, `action`, `retryable`, `occurred_at`, `job_id`,
`source_id`, and `resolved_at`. New errors append to the history; successful
retries mark previous source errors resolved. Provider/SQL exception text remains
in logs; user-facing errors use safe messages and suggested actions. `retryable`
describes whether retrying without correcting the source is likely to help;
every `training_failed` source is eligible for an explicit retry.

`POST /api/training/queue` accepts optional `source_ids` and `retry_failed: true`
to retry only failed sources. The dashboard exposes individual retries and a
"Retry failed sources" button. `GET /api/training/jobs/{job_id}` exposes job
status, source error histories, and retry source IDs. Queue errors, outer worker
failures, RQ timeouts, and cleanup errors use the same error format. RQ stores a
fallback error in job metadata if database error reporting is unavailable.

Dashboard DB changes are owned by Prisma in `../chat-dashboard`: migration
`20261005123000_structured_training_errors` converts source errors to JSONB.
Chat DB changes use Alembic revision `6e40a12bc893`, which converts job errors,
adds selected source IDs, and removes `failed` from configuration states. Both
migrations preserve legacy messages as error-history entries.

Worker entry points are `app.ingestion.jobs.process_training_job` and
`app.ingestion.cleanup.delete_training_source_job`. Deploy with the RQ queue
drained of tasks referencing the removed `app.services.worker_fns` module.
Training jobs allow 30 minutes for extraction and embedding, including Docling OCR.

At retrieval time, keyword search uses the generated `documents.search_vector`
GIN index. The vector combines weighted heading paths and document content.
Semantic search uses the active embedding and bot configuration pair. Chat now invokes
`app.rag.RetrievalPipeline` for each AI response. It reads prior user/assistant
turns from that conversation's agent checkpoint before adding the current
message, bounds history, and rewrites follow-ups into standalone search queries.
The original user message still goes to the answer agent. Rewrite failures fall
back to the original query.

Keyword retrieval matches any parsed query lexeme against the weighted heading
and content vector. Semantic retrieval applies the configured cosine similarity
threshold. Both searches exclude other organizations, bots, configurations,
inactive documents and deleted documents; semantic search also excludes deleted
vectors. Each branch returns up to `retrieval_k` candidates. Reciprocal rank
fusion combines and deduplicates the two lists; it does not use an LLM reranker.

Context assembly keeps complete chunks within the answer model's token budget,
including numbered source labels, headings and page information. Oversize chunks
are skipped so shorter evidence can fit. The agent cites these blocks with `[1]`,
`[2]`, etc. WebSocket AI replies retain the existing message format and add
`retrieval_status` plus `sources` (citation number, source/document IDs, label,
page, URL). Clients can use this metadata to display source links. No-match or
budget-exhausted results produce `no_evidence`; search errors produce
`unavailable`. Both still allow greetings, lead capture and counsellor handover,
while factual answers must use the current evidence.

Optional `bot_configurations.settings` controls `rewrite_model`,
`max_history_tokens` (default 2000), `max_query_tokens` (256),
`max_context_tokens` (8000), and `rrf_k` (60). The active answer model determines
context token counts. The pipeline validates the active configuration pair used
by the chat session.

Chat-only Alembic revision `8bf16c07a2de` adds `retrieval_logs.details` JSONB. It
records the original/rewritten query, status, keyword/semantic ranks and scores,
fusion scores, context selection and source references. Rewrite fallbacks and
search failures also retain safe reasons and stages in this audit record. Existing cosine score
arrays use NULL for keyword-only results. No Dashboard DB change is needed.

Run the unit suite with `.venv/bin/python -m unittest discover -s tests`.
The optional PostgreSQL/pgvector verification runs with
`RUN_POSTGRES_RETRIEVAL_TESTS=1 .venv/bin/python -m unittest discover -s tests -p test_postgres_retrieval.py`;
it mocks embedding calls and rolls back all fixture writes.

## Project Structure

```
chat_api/
├── alembic/                      # Alembic migrations (chat DB only)
│   ├── alembic.ini
│   ├── env.py
│   ├── script.py.mako
│   └── versions/
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
│   │   └── utils.py             # Text normalization helpers
│   ├── infra/                     # Infrastructure
│   │   ├── redis_client.py      # Redis client configuration
│   │   └── r2_storage.py        # Cloudflare R2 (S3) client helpers
│   ├── models/                    # SQLAlchemy ORM models
│   │   ├── chat_db_models.py      # Chat DB models (documents/embeddings/messages/training jobs)
│   │   └── dashboard_db_models.py # Dashboard DB models (orgs/bots/training sources/files)
│   ├── services/                  # Business logic
│   │   ├── chat.py              # Chat message handling
│   │   └── notifications.py     # Dashboard notification persistence
│   ├── ingestion/                # Parsers, knowledge units, training/cleanup jobs
│   ├── rag/                      # Embeddings, query preparation, hybrid retrieval
│   ├── ws/                        # WebSocket utilities
│   │   └── auth.py              # WebSocket authentication
│   ├── logging_config.py         # Logging configuration
│   └── main.py                   # FastAPI application entry point
├── logs/                          # Application logs
├── .dockerignore                  # Docker ignore patterns
├── .env.example                   # Environment variables template
├── docker-compose.yml             # Docker Compose configuration
├── Dockerfile                     # Docker image definition
├── pyproject.toml                 # Project metadata and dependency ranges
├── uv.lock                        # Reproducible dependency lockfile
├── public.pem                     # JWT public key (RS256)
├── chat_db_schema.txt             # Chat DB schema reference
├── dashboard_db_schema.txt        # Dashboard DB schema reference
└── training_flow.md               # Training + upload flow notes
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
docker compose up workers

# Or locally (requires Redis running)
rq worker default
```

## Development

### Local Development (without Docker)

1. Install uv:
```bash
# Installation instructions: https://docs.astral.sh/uv/getting-started/installation/
```

2. Sync the locked dependencies:
```bash
# Install uv: https://docs.astral.sh/uv/getting-started/installation/
uv sync --locked
```

3. Set up environment variables in `.env.local`

4. Start Redis locally:
```bash
docker run --rm -p 6379:6379 redis:7
```

5. Run the application:
```bash
uv run uvicorn app.main:app --reload
```

6. Run workers in a separate terminal:
```bash
uv run rq worker default
```

`uv sync --locked` creates the project environment from `pyproject.toml` and
`uv.lock`. Use `uv add package-name` to add a direct dependency and commit the
resulting lockfile. The dependency ranges in `pyproject.toml` describe safe
major-version boundaries; exact resolved versions remain in `uv.lock`.
Upgrade intentionally with `uv lock --upgrade-package package-name`, then
validate the application before updating more packages. Use
`uvx pip-audit --path .venv` to audit the synced environment without adding the
audit tool to runtime dependencies.

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

### Chat DB (`chat_db_schema.txt`)
Chat DB tables for messages, documents, embeddings, and training jobs. Models live in `app/models/chat_db_models.py`.

### Dashboard DB (`dashboard_db_schema.txt`)
Dashboard DB tables for organizations, users, bots, training sources, files,
conversations, leads, notifications, and follow-ups. The authoritative schema
is maintained by Prisma; the SQLAlchemy mirror lives in
`app/models/dashboard_db_models.py`. This service does not run dashboard
Alembic migrations.

## Migrations (Chat DB only)

Alembic is configured in `alembic/` and targets `app/models/chat_db_models.py`.
The current Chat DB migrations include the generated
`documents.search_vector` column and its GIN index. The vector indexes
`metadata_json.structure.heading_paths` and `content`.

### Initialize a new Chat DB

The reusable bootstrap module is `app/db/bootstrap.py`. It enables the
PostgreSQL extensions required by the models (`vector` and `pgcrypto`) and
creates any missing Chat DB tables from the SQLAlchemy metadata. It never
drops tables or changes existing columns.

Run it from the project root after setting the `CHAT_DB_*` variables in
`.env.local`:

```bash
uv run python -m app.db.bootstrap
```

The command is safe to run again. To skip extension creation when your
database provider manages extensions separately:

```bash
uv run python -m app.db.bootstrap --skip-extensions
```

The current migration history contains changes for an existing legacy schema;
it is not a complete empty-database bootstrap. Therefore, after running the
bootstrap command against a brand-new empty Chat DB, mark the database at the
current migration revision:

```bash
uv run alembic -c alembic/alembic.ini stamp head
```

Do not run `stamp head` against a database whose tables were not verified.
For an existing database, inspect its schema and migration state first, then
use `upgrade head` to apply pending migrations.

Create a new migration after editing models:

```bash
uv run alembic -c alembic/alembic.ini revision --autogenerate -m "describe change"
```

Dashboard schema changes are managed by the dashboard Prisma schema. Update
`app/models/dashboard_db_models.py` as the SQLAlchemy mirror, but do not create
or run Alembic migrations for the Dashboard DB from this service.

Apply migrations:

```bash
uv run alembic -c alembic/alembic.ini upgrade head
```

Inspect migration status:

```bash
uv run alembic -c alembic/alembic.ini current
uv run alembic -c alembic/alembic.ini history
```

## Logging

`LOG_LEVEL` sets how verbose the process is (console + which records are created). File handlers split output by severity:

| Directory | File | Levels |
|-----------|------|--------|
| `logs/debug_logs/` | `debug.log` | `DEBUG` only |
| `logs/info_logs/` | `app.log` | `INFO`, `WARNING` |
| `logs/error_logs/` | `error.log` | `ERROR`, `CRITICAL`, and `logger.exception()` tracebacks |

Override with `LOG_DIR` (base) or the per-level `*_LOG_DIR` variables. Directories are created on startup; `logs/` is gitignored.

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
