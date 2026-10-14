# UniKnowledge AI backend

FastAPI backend for learning and document processing. Upload, extraction, chunking, and embeddings are implemented. Search, grounded answers, courses, and adaptive learning are upcoming work in [the roadmap](../Docs/IMPROVEMENT_ROADMAP.md).

## Setup on Windows

Run commands from `C:\Dev\UniKnowledgeAI\Backend`. Use one virtual environment consistently. The documented environment uses Python 3.14; `requirements.txt` is the project's existing dependency snapshot. A new clean installation of that snapshot has not been verified in this batch.

```powershell
cd C:\Dev\UniKnowledgeAI\Backend
py -3.14 -m venv venv
.\venv\Scripts\python.exe -m pip install -r requirements.txt
.\venv\Scripts\python.exe -m pip install -r requirements-dev.txt
if (!(Test-Path -LiteralPath .env)) { Copy-Item -LiteralPath .env.example -Destination .env }
```

Edit `.env` with your database URL and a random signing secret. Generate the secret with:

```powershell
.\venv\Scripts\python.exe -c "import secrets; print(secrets.token_hex(32))"
```

Do not commit `.env`. Settings load `Backend/.env` regardless of the importing process's working directory; environment variables override file values. Upload storage still depends on starting the server from `Backend`.

### PostgreSQL prerequisite

Use PostgreSQL with a compatible pgvector extension installed on the server. In a PostgreSQL administrator connection, create a development role and database, replacing the password placeholder:

```sql
CREATE USER uniknowledge_user WITH PASSWORD '<choose-a-local-password>';
CREATE DATABASE uniknowledge_db OWNER uniknowledge_user;
```

Connect to `uniknowledge_db` as the administrator and run:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
GRANT USAGE, CREATE ON SCHEMA public TO uniknowledge_user;
```

Configure `DATABASE_URL` to use this database and application role. Existing databases need not be recreated. The current vector migration assumes the extension is already enabled.

### Migrate and run

```powershell
.\venv\Scripts\python.exe -m alembic upgrade head
.\venv\Scripts\python.exe seed_roles.py
.\venv\Scripts\python.exe -m uvicorn app.main:app --reload
```

Open <http://127.0.0.1:8000/docs>. Log in, select **Authorize**, and paste the token. Embedding generation downloads the model on first use; the server reuses the local cache afterwards.

## Authentication and documents

| Method | Path | Behaviour |
| --- | --- | --- |
| POST | `/auth/register` | Creates a STUDENT. Omit role or supply STUDENT, case-insensitively; other roles return 422. |
| POST | `/auth/login` | Returns a 60-minute bearer token. |
| GET | `/users/me` | Returns the authenticated user's profile and current database role. |
| GET | `/documents?offset=0&limit=20` | Lists only the caller's documents, with limit 1-100. |
| GET | `/documents/{id}` | Returns owned document metadata; missing or other users' documents return 404. |
| POST | `/documents/upload` | Accepts supported extensions, up to 20 MB. |
| POST | `/documents/{id}/extract` | Extracts text; requires ownership. |
| POST | `/documents/{id}/chunk` | Creates chunks; requires ownership. |
| POST | `/documents/{id}/embed` | Creates embeddings; requires ownership. |

Registration example:

```json
{
  "full_name": "Jane Student",
  "email": "jane@example.com",
  "password": "a long unique passphrase"
}
```

New passwords require at least 12 characters and at most 72 UTF-8 bytes. They are not trimmed or silently truncated. Names are trimmed and must contain 1-150 characters. Privileged roles require a future controlled provisioning workflow. The reusable `require_role` dependency checks the user's current database role; document access is governed separately by ownership.

### Existing accounts

Existing bcrypt hashes remain readable, including passwords below the new registration minimum. Login does not apply that minimum to old accounts.

The previous code truncated passwords after 72 bytes. Login now rejects longer inputs because the old hash cannot establish which suffix was originally used. Affected accounts need a reset through a trusted provisioning process; no public password-reset endpoint is implemented yet. Existing privileged accounts are not demoted automatically: review accounts created through the old public registration endpoint before deploying.

Missing, invalid, expired, incorrectly signed, and malformed-subject tokens return 401 with a Bearer challenge. Tokens require an expiry and a UUID subject. Authorisation uses the current database role instead of trusting an old token's role claim.

## Regression tests

```powershell
cd C:\Dev\UniKnowledgeAI\Backend
.\venv\Scripts\python.exe -m unittest discover -s tests -v
```

Tests use Python's unittest runner and HTTPX's ASGI transport. They set a test signing secret and database URL before importing the app and create fresh in-memory SQLite tables for each API case. They do not connect to the development PostgreSQL database, modify uploaded files, or download an embedding model.

Coverage includes registration restrictions, validation, login, existing hashes, password byte limits, token validation, database role checks, profiles, document privacy, and pagination. PostgreSQL migrations, vector retrieval, extraction quality, and model generation need separate integration checks.

## Current limits

- PPTX uploads are accepted but extraction is not implemented.
- Processing uses separate manual endpoints rather than durable background jobs.
- Document deletion, source-location citations, search, and grounded answers remain unfinished.
- `/health` reports process liveness and does not check database readiness.
- Rate limiting, password reset, privileged provisioning, and production CORS configuration remain future work.

References: [bcrypt password-length behaviour](https://github.com/pyca/bcrypt#maximum-password-length), [embedding model](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), and [pgvector](https://github.com/pgvector/pgvector).
