# UniKnowledge AI backend

FastAPI backend for learning and document processing. Authentication, courses/enrolments, explicit private/shared materials, upload, extraction, chunking, and embeddings are implemented. Search, grounded answers, and adaptive learning are upcoming work in [the roadmap](../Docs/IMPROVEMENT_ROADMAP.md).

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
| GET | `/courses` | Authenticated course catalog; `mine=true` limits to taught/enrolled courses. |
| POST | `/courses` | Lecturer/admin creates a course and becomes its assigned instructor. |
| GET | `/courses/{id}` | Authenticated course metadata. |
| POST | `/courses/{id}/enroll` | Student self-enrolment; repeating it returns the same enrolment. |
| DELETE | `/courses/{id}/enroll` | Student removes their own enrolment and loses shared access. |
| GET | `/documents?offset=0&limit=20` | Lists owned documents and authorised shared material, with limit 1-100; optional `course_id` filter. |
| GET | `/documents/{id}` | Returns readable metadata; inaccessible documents return 404. |
| GET | `/documents/{id}/text` | Reads authorised extracted text; returns 400 if extraction is unavailable. |
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

New passwords require at least 12 characters and at most 72 UTF-8 bytes. They are not trimmed or silently truncated. Names are trimmed and must contain 1-150 characters. Privileged roles require a future controlled provisioning workflow. The reusable `require_role` dependency checks the user's current database role and protects course creation and self-enrolment.

### Courses and materials

Course creation accepts `{"code": "ICT101", "title": "Programming", "description": "Optional description"}`. Codes are normalised to uppercase and are unique. The creating lecturer or administrator is the assigned instructor. Course metadata is available to authenticated users; this initial catalog permits open self-enrolment by students. Programme restrictions, approval/invitations, and instructor reassignment are not implemented yet.

Document uploads still accept the original `file` field. Optional multipart fields are `course_id` and `visibility` (`private`, the default, or `course`). A course link requires the uploader to be its instructor or an enrolled member. Only the assigned instructor, with a current LECTURER or ADMIN role, can publish `course` material.

Private documents remain owner-only, even when linked to a course: instructors and administrators cannot inspect students' private notes through these APIs. Shared material is readable by its owner, assigned instructor, and enrolled students. Permissions are applied in the database query; a course filter does not bypass them. Extraction, chunking, and embedding remain owner-only. Unenrolment revokes shared access immediately while preserving access to the student's own notes.

Migration `a62c941fe308` adds courses, enrolments, and document visibility. Existing documents become private with no course association. Apply it with `python -m alembic upgrade head` on each environment before using the changed APIs. It has already been applied to the current local development database. No existing accounts were assigned new roles.

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

Coverage includes registration restrictions, validation, login, existing hashes, password byte limits, token validation, database role checks, profiles, course creation/enrolment, publishing, student privacy, revocation, upload failure cleanup, and pagination. Vector retrieval, broader extraction quality, and model generation need separate integration checks.

The opt-in PostgreSQL migration check uses the configured database but creates a fresh schema and rolls its entire transaction back:

```powershell
.\venv\Scripts\python.exe tests/verify_course_migration.py
```

It verifies upgrade/downgrade/re-upgrade, existing document preservation, and database constraints. It requires permission to create schemas and the enabled pgvector extension. It never downgrades the development schema.

## Current limits

- PPTX uploads are accepted but extraction is not implemented.
- Processing uses separate manual endpoints rather than durable background jobs.
- Document deletion, source-location citations, search, and grounded answers remain unfinished.
- `/health` reports process liveness and does not check database readiness.
- Rate limiting, password reset, privileged provisioning, and production CORS configuration remain future work.

References: [bcrypt password-length behaviour](https://github.com/pyca/bcrypt#maximum-password-length), [embedding model](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), and [pgvector](https://github.com/pgvector/pgvector).
