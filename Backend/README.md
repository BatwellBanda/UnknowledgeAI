# UniKnowledge AI backend

FastAPI backend for learning and document processing. Authentication, courses/enrolments, explicit private/shared materials, upload, extraction, chunking, embeddings, and semantic search are implemented. Grounded answers and adaptive learning are upcoming work in [the roadmap](../Docs/IMPROVEMENT_ROADMAP.md).

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

Do not commit `.env`. Settings load `Backend/.env` regardless of the importing process's working directory; environment variables override file values. Uploads are stored in `Backend/uploads` (override with `UPLOAD_DIR`) wherever the server is started from. `SECRET_KEY` must be at least 32 characters; the app refuses to start otherwise. Changing it signs every user out.

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

Open <http://127.0.0.1:8000/app/> for the student interface. Developer API documentation remains at <http://127.0.0.1:8000/docs>; log in there, select **Authorize**, and paste the token. Embedding generation downloads the model on first use; the server reuses the local cache afterwards.

Check the running API from PowerShell:

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/ready
```

`/health` checks process liveness and answers even while every request thread is busy. `/ready` executes a database query and returns 200 when the connection works, or 503 with a generic response when it fails. Its `processing` field reports `queued_jobs` and `oldest_wait_seconds`; a wait that keeps growing means no worker is running. It does not verify migration state, embedding models, or Ollama. Database connection timeouts follow the configured driver; this is not a fixed-duration probe.

## Student interface

The local frontend is served by the same FastAPI application at `/app/`, so no frontend package installation or separate server is needed. It supports student registration/sign-in, course enrolment, uploads, processing progress/retries, course-filtered passage search, generated answers, and source-quote dialogs. Existing short-password accounts can still sign in; the longer password policy applies to registration.

Keep the API and `python -m app.worker` running in separate terminals. After sign-in, upload a document with **Upload material**. The UI queues processing automatically and polls saved job status. When it is ready, use **Search & answers** to find passages. Answer generation also requires the configured Ollama runtime/model. If generation is unavailable, the interface keeps passage search accessible.

Private and shared-material controls use the API's `owner_id` metadata; backend authorisation remains authoritative. Students cannot trigger processing for shared material they do not own. Instructors can explicitly publish to courses they teach. The original files are not displayed as PDF/slide images yet; source dialogs show extracted text with page/slide/section/line metadata.

Use **Download original** on a material card or inside a source dialog to save the uploaded file. The request sends the current bearer token in its header, checks current document permissions, and returns a private, non-cacheable attachment. Missing files return 404. This retrieves the whole original file; it does not open an embedded viewer at the cited page. Previously saved copies remain on the recipient's device after course access changes.

The interface is responsive, uses labelled forms and native modal dialogs, and renders document/model content as text rather than HTML. Tokens use tab-scoped sessionStorage, with memory-only fallback when browser storage is unavailable. Logout/expiry cancels pending requests and clears account data. Review session handling and deploy with HTTPS before release.

Verification: `/app/`, JS, and CSS return 200 on the existing local server; 92 regression tests pass, including static-route traversal checks and ownership metadata. JavaScript syntax checks pass. Browser discovery returned no available browser, so visual layout, keyboard interaction, and complete browser-driven flows have not been verified. See [frontend notes](../Frontend/README.md).

## Authentication and documents

| Method | Path | Behaviour |
| --- | --- | --- |
| POST | `/auth/register` | Creates a STUDENT. Omit role or supply STUDENT, case-insensitively; other roles return 422. |
| POST | `/auth/login` | Returns a bearer token (60 minutes by default, `ACCESS_TOKEN_EXPIRE_MINUTES`). |
| POST | `/auth/logout` | Revokes every token issued to the account, on all devices. |
| POST | `/auth/change-password` | Requires `current_password`; revokes older tokens and returns a new one. |
| PATCH | `/users/{id}/role` | ADMIN only: sets `STUDENT`, `LECTURER` or `ADMIN`. Admins cannot change their own role. |
| GET | `/users/me` | Returns the authenticated user's profile and current database role. |
| GET | `/courses` | Authenticated course catalog; `mine=true` limits to taught/enrolled courses. `join_code` is shown only to the course instructor or an admin, otherwise null. |
| POST | `/courses` | Lecturer/admin creates a course and becomes its assigned instructor. |
| GET | `/courses/{id}` | Authenticated course metadata. |
| POST | `/courses/{id}/enroll` | Student enrolment with body `{"join_code": "..."}` (case-insensitive); a wrong code returns 403. Repeating it returns the same enrolment. |
| POST | `/courses/{id}/join-code` | Instructor/admin issues a new code; the old one stops working, existing enrolments stay. |
| GET | `/courses/{id}/students` | Instructor/admin lists enrolled students. |
| DELETE | `/courses/{id}/students/{user_id}` | Instructor/admin removes a student, revoking shared access. |
| DELETE | `/courses/{id}/enroll` | Student removes their own enrolment and loses shared access. |
| GET | `/documents?offset=0&limit=20` | Lists owned documents and authorised shared material, with limit 1-100; optional `course_id` filter. |
| GET | `/documents/{id}` | Returns readable metadata; inaccessible documents return 404. |
| GET | `/documents/{id}/text` | Reads authorised extracted text; returns 400 if extraction is unavailable. |
| GET | `/documents/{id}/download` | Downloads the authorised original file as an attachment; inaccessible documents or missing files return 404. |
| GET | `/documents/{id}/chunks` | Reads authorised chunks and source locations; supports `offset` and `limit` (1-100). |
| DELETE | `/documents/{id}` | Owner deletes the document, its passages, jobs and stored file; 409 while processing. |
| POST | `/documents/upload` | Accepts supported extensions, up to 20 MB. |
| POST | `/documents/{id}/extract` | Extracts text; requires ownership. |
| POST | `/documents/{id}/chunk` | Creates chunks; requires ownership. |
| POST | `/documents/{id}/embed` | Creates embeddings; requires ownership. |
| POST | `/documents/{id}/process` | Queues the complete extract/chunk/embed pipeline; returns 202 and a saved job. |
| GET | `/documents/{id}/processing` | Returns the owner's latest job status, stage, attempts, and safe error message. |
| POST | `/search` | Searches readable, embedded materials using cosine similarity; returns chunks and source locations. |
| POST | `/chat` | Grounded answers with verified citation provenance through a configured local Ollama runtime. |

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

Coverage includes registration restrictions, validation, login, existing hashes, password byte limits, token validation, database role checks, profiles, course creation/enrolment, publishing, student privacy, revocation, upload failure cleanup, pagination, practice questions, quizzes, mastery, study planning, recordings, captions, transcripts, playback links and notes. Vector retrieval, broader extraction quality, and model generation need separate integration checks.

The opt-in PostgreSQL migration check uses the configured database but creates a fresh schema and rolls its entire transaction back:

```powershell
.\venv\Scripts\python.exe tests/verify_course_migration.py
```

It verifies upgrade/downgrade/re-upgrade, existing document/chunk preservation, source-location JSON round trips, and database constraints. It requires permission to create schemas and the enabled pgvector extension. It never downgrades the development schema.

## Durable document processing

Start the worker in a separate terminal, from `Backend`:

```powershell
cd C:\Dev\UniKnowledgeAI\Backend
.\venv\Scripts\python.exe -m app.worker
```

Keep the API running in its own terminal. Upload a document, then call its `POST /documents/{id}/process` endpoint in Swagger. Poll `GET /documents/{id}/processing` for `queued`, `running`, `completed`, or `failed`. Stages are `queued`, `extract`, `chunk`, `embed`, and `done`. A completed document is searchable. To process at most one queued job and exit, use `python -m app.worker --once`.

Jobs are saved in PostgreSQL rather than kept only in API memory. Repeating a request while a job is active returns that same job. Owners can retry a failed document by calling `/process` again, creating a fresh job. Job inspection and submission are owner-only, including for course materials; enrolled students cannot operate an instructor's processing jobs. The manual `/extract`, `/chunk`, and `/embed` routes remain available but return 409 while a queued/running job is active.

The worker uses `FOR UPDATE SKIP LOCKED` for claiming and a PostgreSQL session advisory lock on each document. The advisory lock is held across stage commits and released explicitly before its connection returns to the pool. This protects against duplicate workers, manual-operation races, and takeover of a slow but live worker. A running job with an expired five-minute lease can restart from extraction after its document lock becomes available. Three interrupted attempts end in a failed job; explicit user retry creates a new attempt history. Graceful interruption also leaves a running job eligible for later recovery rather than discarding it.

This is an at-least-once processing design: after a crash, extraction/chunking/embedding may run again and replace previous chunks/vectors. It is not a guarantee of exactly-once inference. Source-version snapshots and future chat history still need a deliberate retention design. On database failure, the worker may exit; restart it to recover eligible saved jobs. Production worker supervision and queue capacity controls remain future work. On this 8 GB machine, start with one worker.

Migration `c93e842bd560` adds the job table and an index allowing only one active job per document. It is applied to the local database; existing documents and all other records are preserved. Apply `python -m alembic upgrade head` before starting a worker in another environment. No worker was left running in the background by this change.

Verify the real PostgreSQL API/worker flow independently:

```powershell
.\venv\Scripts\python.exe tests/verify_processing_postgres.py
```

That test creates a fresh UUID-named schema, processes a real TXT fixture with stubbed embedding inference, checks locking/crash recovery/retry limits, and tests migration downgrade/re-upgrade. It removes only its own schema and temporary files; development data is untouched.

Lock semantics reference: [PostgreSQL explicit and advisory locking](https://www.postgresql.org/docs/current/explicit-locking.html).

## Source locations

Extraction now preserves ordered source segments. Chunk responses include `source_location`:

| Format | Location | Supported text |
| --- | --- | --- |
| PDF | `kind=pdf_page`, one-based `page` | Selectable page text; skipped blank pages do not renumber later pages. |
| PPTX | `kind=pptx_slide`, one-based `slide` | Text frames, grouped text, and tables; skipped blank slides do not renumber later slides. |
| DOCX | `kind=docx_paragraph` with `paragraph`, or `kind=docx_table` with `table`; optional `section` | Body paragraphs and tables in document order, with the most recent heading. No invented page numbers. |
| TXT | `kind=txt_lines`, one-based inclusive `line_start` and `line_end` | Strict UTF-8 (optional BOM); line ranges retain original blank lines. |

Chunks stay within a source segment. Each saved window fits the embedding model's actual `max_seq_length`, including special tokens, and is capped at 1000 characters. Windows use whitespace boundaries where possible; very long uninterrupted words can still split. Overlap is at most 150 characters and is reduced for short windows so processing always progresses. The splitter keeps original text offsets rather than decoding tokens, preserving source locations and exact source text after whitespace normalisation. TXT line ranges exclude trimmed leading/trailing blank lines. DOCX segments are individual paragraphs/tables, so retrieval quality and segment grouping still need evaluation.

Chunking now needs the embedding model/tokenizer, reused from the same process cache as embedding and search. A missing model returns a generic 503 without replacing saved chunks. Embedding separately rejects chunks exceeding the token limit with 400, preventing silent truncation of legacy chunks. Use **Process again** on an existing material to regenerate its chunks and vectors; existing documents are not automatically changed. No new database migration is needed.

The opt-in cached-model check also exercises token-dense text, Unicode, full source coverage, actual chunk/embed APIs, and retrieval of an ending fact with its source location:

```powershell
.\venv\Scripts\python.exe tests/verify_search_postgres.py --real-model
```

It uses a rolled-back PostgreSQL schema and offline model-cache loading; it does not establish retrieval accuracy on real university material.

Migration `b81d732ac459` adds nullable extraction-segment and chunk-location JSON columns. It is applied locally. Existing text, chunks, and vectors are preserved. Existing chunks have null locations; re-chunking legacy text without re-extraction labels its location `unknown`.

To add locations to an old document, run its `/extract`, `/chunk`, and `/embed` operations in that order. Successful re-extraction invalidates the previous chunks and embeddings, which must be regenerated. No existing document was automatically reprocessed in this release.

Empty, malformed, scanned/image-only, or unsupported-encoding content receives a generic 422 extraction error and a failed status. Old successful text/chunks remain available for recovery after a parsing failure, but chunking and embedding are blocked until re-extraction succeeds. Database persistence errors receive a generic 500 response. Detailed failures are logged on the server.

OCR, images/diagrams, speaker notes, DOCX headers/footers, and nested tables are not extracted. Locations are now returned by search; answer generation and citation viewers are not implemented yet.

## Semantic search

After uploading, extracting, chunking, and embedding a document, authorise in Swagger and call `POST /search`:

```json
{
  "query": "What does a subnet mask do?",
  "top_k": 5
}
```

Optional fields are `course_id`, `document_id`, and `min_similarity` (between -1 and 1). `top_k` is 1-20. Questions are trimmed, limited to 2000 characters, and checked against the model's actual token budget before encoding; overlong tokenized questions receive 400 instead of being silently truncated.

Results contain `chunk_id`, `document_id`, `course_id`, `document_name`, `chunk_index`, `text`, `similarity`, and `source_location`. Similarity is cosine similarity, not a probability that a passage answers the question. No default relevance threshold is imposed before calibration on real course questions. A threshold is optional and should be evaluated, not treated as a guarantee of answer support.

Response status is `matches`, `no_matches` (nothing meets the requested threshold), or `no_searchable_materials` (no readable, embedded, non-zero-vector chunks in the requested scope). Unknown or inaccessible scope identifiers also return no searchable material. An empty library does not load the model. Model failures return a generic 503 response and are logged server-side.

Permissions and course/document scope filters are part of the database query before ranking and limiting. Owners can search their own private notes; enrolled students can search published course material. Instructors/admins cannot search students' private notes. Unenrolment revokes published-material search access. Failed/unfinished documents and null/zero vectors are excluded. Legacy vectors remain searchable but may have null source locations until reprocessing.

Search uses the existing all-MiniLM-L6-v2 model and exact PostgreSQL/pgvector cosine ranking. No new migration or approximate index is needed for this batch. Evaluate latency and retrieval quality on a representative corpus before adding HNSW or changing chunk sizes. Initial model loading can make the first request slower.

Run the opt-in real PostgreSQL/API checks in a temporary, rolled-back schema:

```powershell
.\venv\Scripts\python.exe tests/verify_search_postgres.py
```

That command uses deterministic query/document vectors to verify ranking, filters, thresholds, privacy, and revocation. To also run a small full-path check with the actual cached model (no model download):

```powershell
.\venv\Scripts\python.exe tests/verify_search_postgres.py --real-model
```

The two-passage real-model check is a functional smoke test, not a retrieval-quality benchmark. The roadmap's held-out course question evaluation remains outstanding.

## Grounded answers with local Ollama

`POST /chat` accepts the same question, scope, top-k, and optional threshold fields as `/search`. It retrieves authorised passages and assigns temporary source IDs such as `S1`. Ollama is the selected generation provider. Empty or unsupported retrieval scopes return an abstention without calling generation. A missing runtime/model returns a controlled 503; generation timeouts return 504; malformed or truncated model responses return 502.

### Install and configure the local model

Install Ollama using the [official Windows download](https://ollama.com/download/windows), or run:

```powershell
winget install --id Ollama.Ollama --exact --accept-package-agreements --accept-source-agreements
```

Open a new terminal so its PATH contains Ollama, then download the initial model:

```powershell
ollama pull qwen2.5:1.5b
ollama list
```

The initial 1.5B model is chosen for this machine's roughly 8 GB of RAM; its download is about 986 MB. It is a starting point for testing, not a measured accuracy recommendation. Close unused memory-heavy applications before inference. Models run on the backend machine, not automatically on each student's phone.

Ollama normally runs in the Windows background after installation. If the API is unavailable, run `ollama serve` in another terminal. Do not start a second server if port 11434 is already in use. Check readiness:

```powershell
Invoke-RestMethod http://127.0.0.1:11434/api/tags
```

Optional `Backend/.env` entries (the defaults already match these values):

```dotenv
LLM_PROVIDER=ollama
OLLAMA_BASE_URL=http://127.0.0.1:11434
OLLAMA_MODEL=qwen2.5:1.5b
LLM_TIMEOUT_SECONDS=120
LLM_MAX_OUTPUT_TOKENS=768
```

Restart the backend after changing configuration. Set `LLM_PROVIDER=disabled` to disable generation. The adapter calls `/api/chat` on the configured URL, uses non-streaming structured JSON, temperature zero, an 8192-token model context, and a bounded output. The default URL is loopback; generation does not require a hosted API key. Initial runtime/model downloads require Internet; local inference can work without Internet once both generation and embedding models are cached, but the complete application has not been evaluated offline.

Example request after the document is embedded and Swagger is authorised:

```json
{"query": "What does a subnet mask do?", "top_k": 5}
```

The provider-independent implementation requests structured claims, each with source IDs and exact supporting quotations. The server validates the response schema, rejects unknown source IDs and invented quotes, and constructs the answer's citation markers and document/location metadata itself. It rechecks permissions and chunk contents before and after generation; changed access or material returns 409. Invalid generated evidence returns a generic 502 rather than an unverified answer.

This verifies citation provenance, not whether a quoted passage logically supports a paraphrased claim. Answer faithfulness and resistance to instructions embedded in material still require evaluation with the selected real model. The prompt treats source material as untrusted data and requires abstention when the passages do not establish an answer. No uncalibrated default similarity cutoff is presented as a correctness guarantee.

Retrieved context is capped at 12,000 passage characters; generation-token budgeting still needs refinement. Document chunks are checked against the embedding tokenizer separately. The initial endpoint is stateless; conversation history, stored citation snapshots, and streaming remain implementation work. Provider tests use an HTTP mock to check the exact Ollama request and controlled error paths. PostgreSQL retrieval and permission checks are real. Run an opt-in live-generation check after installation:

```powershell
.\venv\Scripts\python.exe tests/verify_search_postgres.py --ollama
```

It tests an actual local answer and an unrelated-question abstention through the authenticated API and real PostgreSQL retrieval in a rolled-back schema, using a fixed query vector to isolate generation. It does not replace a held-out retrieval/answer-quality evaluation.

Current setup status: code integration and 81 regression tests pass. Ollama is not installed on this machine yet. Installation attempts encountered DNS/TLS failures and an observed installer download rate around 1 MB per minute; the incomplete download was removed. No real Ollama inference result is claimed. Install the runtime/model on a working, faster connection before running the live check.

References: [Ollama chat API](https://docs.ollama.com/api/chat), [structured JSON output](https://docs.ollama.com/capabilities/structured-outputs), and [initial model details](https://ollama.com/library/qwen2.5:1.5b).

## Practice, mastery and study plans

Open **Practice & plan** in the student app. Run `alembic upgrade head` first (migration `f5c8a2d91b37` adds the tables).

**Topics.** A course's instructor (or an admin) defines its topics; enrolled students read them.

**Questions.** Every multiple-choice question is tied to a passage: its source document, chunk, location and an exact supporting quote.
- **Writing by hand:** any course member can write a question from a passage they can read, using **Make practice question** on a search result.
- **AI drafts:** **Draft with AI** retrieves passages for a topic and asks Ollama for one question per passage. A draft is kept only if it has four distinct options and a quote that appears word for word in the passage. The options are shuffled so the answer's position carries no signal. Drafts start private and marked *needs review*.
- **Publishing:** the course instructor can publish a reviewed question once its source material is published to the same course.

**Visibility.** Permissions are applied in the database query. A question is visible to its author, or to enrolled students once published, and only while the viewer can still read its source document. Answer keys appear only in the author's own list and in submitted quiz results.

**Reprocessing and deletion.**
- When a document is reprocessed, a question re-attaches to the new passage that contains its quote. If no passage does, it drops out of quizzes.
- Deleting a source document deletes its questions. Past quiz grades stay.

**Quizzes.** A quiz is assembled in this order:
1. questions answered wrongly last time;
2. unseen questions;
3. the least recently answered correct ones.

Within each group, weaker topics come first. Each attempt keeps a copy of every question as it was asked, so later edits never change a grade. Explanations and source passages are shown only after submission, and only while the source is still readable. Skipped questions count as incorrect.

**Mastery** ([mastery_service.py](app/services/mastery_service.py)) is a recency-weighted share of correct answers. The weights halve every 14 days, and only the latest 20 answers per topic count.

| Level | Rule |
| --- | --- |
| Not enough evidence | Fewer than 4 answers; never reported as weak |
| Weak | Below 60% |
| Developing | 60–79% |
| Strong | 80% and above |

**Study plan** ([planner_service.py](app/services/planner_service.py)) is deterministic and explains every session.
- **Availability:** students enter minutes per weekday after classes, an optional exam date (the plan stops the day before), and how hard each topic is for them. Sessions are 30 minutes, plus a final block of 15 minutes or more.
- **Priority:** each topic's priority is (1 − mastery, minimum 0.1; 0.5 without evidence) × a difficulty weight (0.8 / 1 / 1.25).
- **Allocation:** each block goes to the highest priority ÷ (1 + blocks already given). A weak topic earns more time without its priority being counted again for every extra block. A day repeats a topic only when every topic already has a block that day.
- **Missed sessions:** sessions from the last 14 days are rescheduled first, once per topic, unless the topic is now strong or has since been completed.
- **Updates:** the plan rebuilds after every quiz submission and settings change. Example reason: *"Subnetting needs revision after your last two quizzes; the exam is in 10 days."*

| Endpoint | Purpose |
| --- | --- |
| `GET/POST /courses/{id}/topics`, `PATCH /topics/{id}` | List (members) and manage (instructor) topics |
| `GET/POST /questions`, `PATCH/DELETE /questions/{id}` | Your own questions with answer keys; create from a passage; edit; delete |
| `POST /questions/generate` | Draft questions with the local model (10 requests per hour) |
| `POST/DELETE /questions/{id}/publish` | Publish to or withdraw from the class |
| `POST /quizzes`, `GET /quizzes[/{id}]`, `POST /quizzes/{id}/submit` | Start, review and submit quizzes |
| `GET /courses/{id}/mastery` | Per-topic mastery |
| `GET /courses/{id}/study-plan`, `POST …/rebuild`, `PUT …/settings` | Read, replan, set availability/exam/difficulty |
| `POST /study-sessions/{id}/complete` | Mark a session done |

Plan endpoints accept an optional `today` (the student's local date, within a day of the server's UTC date).

Verify on PostgreSQL in a rolled-back schema:

```powershell
.\venv\Scripts\python.exe tests/verify_learning_postgres.py
```

It checks:
- the migration against the models;
- the full practice loop through the API;
- cascade and SET NULL rules, and the check, unique and status constraints;
- downgrade and re-upgrade.

The planner validates planning *behaviour* on controlled profiles, not improvement in exam results. Generated questions still need human review: the checks confirm structure and an exact supporting quote, not that the distractors are wrong or the question is well posed.

## Lecture recordings, transcripts and notes

Recordings (MP3, M4A, WAV, OGG, WebM, MP4; up to `MAX_RECORDING_MB` and `MAX_RECORDING_MINUTES`) are stored as documents. Course sharing, processing jobs, search, cited answers and practice questions therefore work for them unchanged.

**Install FFmpeg** (it provides `ffprobe` too): `winget install Gyan.FFmpeg`, then open a new terminal. Without it, recording uploads return 503.

Rules:
- **Upload checks:** the container signature must match the extension. FFprobe then runs in a separate process with a timeout and must find an audio track and a length within the limit. Rejected files are removed.
- **Transcript sources:**
  - **Captions:** attach a WebVTT or SubRip file to the upload, or replace the transcript later.
  - **Whisper:** without captions, processing transcribes the recording locally (Hugging Face `transformers`, `WHISPER_MODEL`, CPU). The model, about 290 MB for `openai/whisper-base`, downloads on first use; `openai/whisper-tiny` is smaller but less accurate.
  - With `TRANSCRIPTION_PROVIDER=disabled`, processing fails with a message asking for a caption file.
- **Searchable passages:** consecutive transcript lines are grouped into passages within the embedding token budget. Each passage keeps its time span (`{"kind": "recording", "start_seconds", "end_seconds"}`), so search results and citations offer **Play from m:ss**.
- **Corrections:** the uploader can correct any line. The transcript is then marked *stale* until the recording is processed again; the old index stays searchable meanwhile. Processing reuses the stored transcript and never re-transcribes over corrections. **Transcribe again** deletes the transcript first.
- **Playback:** media elements cannot send bearer tokens. The app therefore requests a playback link: a signed token scoped to one user and one recording, expiring after `MEDIA_LINK_MINUTES`, in the query string (which access logs omit).
  - Each request, including the range requests used for seeking, rechecks current access.
  - Signing out revokes earlier links.
  - A playback token is never accepted as a sign-in token.
- **Notes:** private to their author, optionally pinned to a time in a recording, and hidden while the material is unreadable. Deleting a recording deletes its transcript and notes.

| Endpoint | Purpose |
| --- | --- |
| `POST /documents/upload` (with optional `captions` file) | Upload a recording |
| `GET /documents/{id}/transcript` | Timestamped transcript, source and stale flag |
| `PUT /documents/{id}/transcript` (multipart `captions`) | Replace with a caption file (uploader) |
| `DELETE /documents/{id}/transcript` | Remove, so the next processing re-transcribes (uploader) |
| `PATCH /documents/{id}/transcript/segments/{segment_id}` | Correct a line (uploader) |
| `POST /documents/{id}/media-link`, `GET /documents/{id}/media?token=…` | Playback link and range-capable stream |
| `GET/POST /documents/{id}/notes`, `PATCH/DELETE /notes/{id}` | Private notes |

Verify on PostgreSQL in a rolled-back schema (needs FFmpeg and the cached embedding model):

```powershell
.\venv\Scripts\python.exe tests/verify_recordings_postgres.py
```

To measure transcription quality, put a checked reference transcript beside each recording (`week3.mp3` + `week3.txt`) and run:

```powershell
.\venv\Scripts\python.exe tests/evaluate_transcription.py path\to\week3.mp3 --report tmp\transcription.json
```

It reports word error rate (substitutions, deletions and insertions) and the real-time factor on this machine. This scores transcription alone, separately from notes and answers. Pilot it on real, noisy lecture audio before relying on automatic transcripts.

## Current limits

For repeatable measured retrieval, run `venv\Scripts\python.exe scripts/evaluate_retrieval.py` from `Backend`. See [the evaluation guide](../Docs/RETRIEVAL_EVALUATION.md) for metrics, the initial synthetic development baseline, and preparing separate real-course development/held-out sets. Reports are saved under ignored `tmp/evaluation/` and the temporary database schema is rolled back.

- Embedded original-file viewers, runtime/model installation, and real-model answer evaluation remain unfinished.
- `/ready` checks database connectivity and the processing backlog; migration and model readiness remain separate checks.
- Rate-limit counters are kept in the API process's memory. Run a single API process, or move them to Redis before scaling out.
- Topics cannot be deleted yet (rename them instead). Two simultaneous plan rebuilds for the same student and course can briefly duplicate planned sessions; the next rebuild replaces them.
- Whisper transcription is implemented but has not been run against a real model here; tests mock it. Transcripts have no speaker labels, and a lecturer's slides are not yet linked to moments in a recording. Recording consent and retention rules must come from the institution.
- Email-based password reset and account deletion remain future work; an operator can reset a password with `scripts/create_user.py --reset-password`.

## Production safeguards

- **Course access:** each course has an 8-character join code that only its instructor and admins can see. Students need it to enrol; instructors can issue a new code and remove students.
- **Rate limits** (HTTP 429 with `Retry-After`): login 10 attempts per account and address per 15 minutes, plus 120 per address per minute; registration 30 per address per hour; chat 10 and search 60 per user per minute; uploads 30 and processing requests 30 per user per hour; enrolment attempts 10 per user per minute. Per-address limits are generous because a campus network can share one public address. Set `TRUST_PROXY_HEADERS=true` only behind a reverse proxy that sets `X-Forwarded-For`.
- **Bounded model work:** at most `MAX_CONCURRENT_GENERATIONS` answers (default 2) and `MAX_CONCURRENT_EMBEDDINGS` search embeddings (default 4) run at once. Extra requests wait up to `BUSY_WAIT_SECONDS`, then get 503, so slow model calls cannot occupy every request thread.
- **Upload safety:** file contents must match the extension (PDF header, real Word/PowerPoint archive, UTF-8 text without binary data). Office archives are limited to 300 MB expanded size and a 200× compression ratio; PDFs to 2000 pages. Parsing runs in a child process that is killed after `EXTRACTION_TIMEOUT_SECONDS` (default 120), so a hostile file cannot stall the worker.
- **Storage:** each user may store up to `MAX_USER_STORAGE_MB` (default 500); further uploads return 413.
- **Sessions:** logout and password changes revoke all of the account's tokens on the server. Unknown-email logins take as long as wrong passwords.
- **HTTP headers:** a strict Content-Security-Policy (relaxed only for `/docs`), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy: no-referrer`, and HSTS over HTTPS. Every response carries `X-Request-ID`, which also appears in the access log; logs include paths but not query strings or bodies.

### Lecturer and admin accounts

Public registration only creates students. From `Backend`, create or promote privileged accounts with:

```powershell
.\venv\Scripts\python.exe -m scripts.create_user --email lecturer@example.ac --name "Lecturer Name" --role LECTURER
```

The password is entered at a prompt, never on the command line. For an existing email, the command changes the role and signs the account out everywhere. After that, an admin can change roles through `PATCH /users/{id}/role`.

### Deploying

Serve the API behind HTTPS, for example with a reverse proxy, and run it as one process (`uvicorn app.main:app --host 127.0.0.1 --port 8000`), plus one or more `python -m app.worker` processes. Workers coordinate through PostgreSQL locks, so you can add more for throughput.

References: [bcrypt password-length behaviour](https://github.com/pyca/bcrypt#maximum-password-length), [embedding model](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2), and [pgvector](https://github.com/pgvector/pgvector).
