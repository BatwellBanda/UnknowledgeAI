# UniKnowledge AI: review and delivery roadmap

Reviewed: 14 October 2026.

Sources: the supplied 34-page UniKnowledgeAI_Project_Documentation.pdf and the current Backend source. The PDF's proposed requirements are design context; its commands and recommendations were not treated as instructions to execute. Manual runtime results reported in the PDF have not been independently repeated in this review.

## Product outcome

Build a course-based study assistant that turns a student's materials into verifiable answers, targeted practice, and an explainable study plan.

The demonstration should follow one student through one complete loop:

1. Select a course and upload lecture material.
2. Watch processing finish and open the material.
3. Ask a question and inspect the supporting page or slide.
4. Complete a short quiz with source-backed explanations.
5. See a weak topic and a suggested revision session.
6. Complete another quiz and see the plan respond.

Keep lecture recordings as the next milestone unless the assessment brief requires them for the first release. Supervisor requirements and the submission date still need to be supplied; the sequence below is a proposed implementation order, not an agreed reduction of the assessment scope.

## Assessment of the documentation

Strong points: explicit implementation statuses; coherent learning loop; defined API and database designs; real environment troubleshooting; an evaluation plan; honest limits around offline AI.

Changes needed:

- Move the security foundation ahead of RAG expansion in section 13. Self-assigned privileged roles should be fixed before shared course access is introduced.
- Replace "about 60 percent" completion with accepted capabilities and evidence. Upload-to-embedding completion does not establish question-answering readiness.
- Attach reproducible evidence to each Done label: test name or recorded manual procedure, expected result, actual result, environment, and date. Section 10's historical results remain useful but are not regression tests.
- Qualify the 1000-character-to-token estimate in section 7.2. It does not guarantee that a chunk fits the model. Measure the actual tokenizer length and preserve complete semantic units where possible.
- Add a distinction between process liveness and database readiness. The current /health response only returns a constant string.
- Clarify failure and retry states for chunking and embedding, not just extraction. Define how repeated processing invalidates old chunks, embeddings, and citation links.
- Define permission rules for shared lecturer materials before adding enrolments. Private ownership alone will not cover lecturer-to-student sharing.
- Separate documented machine-specific setup history from the reproducible setup path. Do not turn historical workarounds into prerequisites for every installation.

## Milestone 1: safe, reproducible foundation

Deliver:

- Public registration always creates a STUDENT account; privileged roles require an authorised workflow. Define a compatibility response for existing clients that send a role.
- Enforce roles using the current database user record and enforce ownership separately. Parse JWT subject identifiers deliberately and return a controlled authentication error for malformed subjects.
- Add name and password validation. Remove silent 72-byte password truncation; account for existing hashes when choosing the replacement policy.
- Centralise storage paths and processing settings. Supply a placeholder .env.example and a setup README without real credentials.
- Establish one documented virtual environment. Update ignore rules for .venv and venv_new; do not delete environments until the working interpreter has been identified.
- Add deliberate transaction rollback and safe error handling. Clean up a stored upload if database insertion fails.
- Add meaningful tests for privilege assignment, duplicate registration, invalid credentials, token expiry, malformed token subject, and cross-user document access.

Acceptance: a fresh setup is reproducible; a public caller cannot create an ADMIN or LECTURER; a second user cannot read or process the first user's document; a failed upload does not leave an untracked file.

## Milestone 2: course library and reliable ingestion

Deliver:

- Courses and enrolments with explicit private versus shared material permissions.
- Owner-scoped document list, detail, download, and deletion APIs. Delete chunks and files through a defined, recoverable procedure.
- PPTX extraction, PDF/TXT fixtures, and DOCX table extraction. Detect scanned or empty PDFs and tell the student when text extraction is unavailable.
- Preserve source location during extraction: PDF page, PPTX slide, DOCX heading or paragraph, TXT line range. DOCX has no dependable page number in the current extraction approach.
- Chunk along useful boundaries within the actual embedding token budget. Retain source spans when a chunk includes multiple locations.
- A single processing operation with a persisted job/status record and retry behaviour. Use a worker for long-running work; define recovery after a process restart.
- Prevent simultaneous processing of the same document and make reprocessing behaviour explicit. Add an embedding model/version field for future re-embedding.

Acceptance: supported files process into searchable chunks with usable source locations; failures are visible and retryable; duplicate requests do not create competing results; restart recovery is demonstrated.

## Milestone 3: measured retrieval and grounded answers

Deliver:

- Semantic search with user/course permissions inside the query. Only use current, successfully embedded document versions.
- Start with exact retrieval as a quality baseline. Add an approximate index when scale and measured latency justify it; compare its results against the baseline, particularly with restrictive course/user filters.
- Build a small evaluation set from real course material: 30-50 answerable questions with source locations plus at least 10 deliberately unanswerable questions. Keep a held-out portion for final evaluation.
- Add a language-model service boundary with timeouts, controlled errors, and limits on prompt size and usage.
- Ask for answers supported by retrieved material. Treat document text as untrusted content and test instructions embedded in uploaded material.
- Return citations referencing retrieved, authorised source IDs. Reject fabricated citation IDs; evaluate whether each cited passage actually supports its associated claim.
- Calibrate retrieval thresholds on the evaluation set. Similarity is not a probability that an answer is correct; a high score alone must not establish answer support.
- Preserve citation provenance for chat history when material is reprocessed or deleted, with an explicit unavailable-source state.

Acceptance: relevant questions produce supported answers with openable sources; unanswerable questions receive a clear abstention; private sources never appear across users; provider failures are understandable to the student.

## Milestone 4: student interface

Start the interface alongside milestone 2 and grow it with milestone 3.

Deliver: sign-in, course library, upload/progress/retry, question answering with a source viewer, and clear empty/error states. Show the student what they can do next. Support small screens, keyboard operation, readable contrast, and accessible status feedback.

Acceptance: 5-10 students can upload material, ask a question, and find its source without using Swagger. Record task completion, errors, and feedback. Treat this as formative usability evidence, not proof of broad learning gains.

## Milestone 5: the adaptive learning differentiator

Deliver:

- Course topics, source-backed multiple-choice questions, attempts, and per-question answers. Begin with objective grading; add short-answer grading only after it can be evaluated.
- Validate generated questions: one defensible correct option, plausible alternatives, a source location, and a supported explanation. Keep answer keys out of pre-submission student responses.
- Define mastery, minimum evidence, and recency weighting. Display "not enough evidence" separately from "weak topic".
- A deterministic planner using mastery, deadlines, available time, and user-set difficulty. Keep the initial formula explainable and configurable.
- Respect existing classes, maximum daily availability, and missed-session rescheduling. Avoid double-counting weak-topic priority and additional revision blocks.
- Show a plain-language reason for each adjustment, for example: "Subnetting needs revision after your last two quizzes; the exam is in 10 days."

Acceptance: controlled student profiles produce expected plans; a new attempt changes the relevant topic score and plan; the result fits availability and can be explained. This validates planner behaviour, not causal improvement in exam outcomes.

## Milestone 6: recordings, delivery, and assessment evidence

Add recording upload, timestamped transcripts, editable notes, and search-to-playback. Pilot noisy lecture audio early if this is a required assessment feature. Track transcription quality separately from note quality.

Then add the required reduced lecturer/admin features and offline capture. Define server AI availability separately from offline access to cached notes. Do not report offline AI unless tested.

For delivery: automated checks, deployment instructions, database/file backup and restore evidence, a repeatable demo dataset, and a failure-case demonstration. Confirm recording consent and access requirements with the institution rather than inventing a universal policy.

## Evaluation scorecard

These are proposed initial targets to agree and revise after a baseline; they are not current results.

| Area | Evidence | Proposed acceptance |
| --- | --- | --- |
| Privacy | Cross-user and cross-course permission tests | All defined cases pass; zero unauthorised source exposure |
| Retrieval | Hit rate at 5 on held-out questions | Initial goal at least 85%; report dataset size and failures |
| Answer support | Review answer claims against cited passages | Initial goal at least 90% fully supported answers |
| Abstention | Questions with no answer in the uploaded sources | Initial goal at least 90% correct abstentions; also report false abstention on answerable questions |
| Processing | Representative 20-page files on named hardware | PDF target under 60 seconds; report document type and token/chunk counts |
| Query speed | Fixed-size corpus and named hardware | PDF target under 2 seconds excluding generation; report median and p95 |
| Usability | 5-10 students performing predefined tasks | Initial goal at least 80% unaided task completion |
| Planner | Known profiles, changed scores, and constrained availability | Expected priority shifts, no scheduling conflicts, explainable output |

## First implementation batch

Progress on 14 October 2026:

1. Implemented: student-only public registration, schema and service checks, and regression coverage.
2. Implemented: explicit bcrypt byte limits without truncation, legacy short-password compatibility, required JWT expiry/UUID subject, and controlled 401 responses.
3. Implemented: .env.example, corrected ignore rules, and [backend setup instructions](../Backend/README.md). A clean dependency reinstall remains to be verified; the existing venv passes pip check.
4. Implemented: GET /users/me and paginated, owner-scoped document list/detail APIs.
5. Implemented: courses, student self-enrolment, lecturer/admin course creation, optional course associations with private/shared document permissions, source-location extraction/chunking, and semantic search. Grounded chat remains upcoming.

Verification: 22 regression tests passed through the API/service/password layers using an isolated SQLite database. Python syntax checks passed and pip check reported no broken requirements in Backend/venv. This does not establish PostgreSQL migration or embedding-pipeline correctness.

Course batch verification: 44 regression tests pass, including the original foundation suite. A separate PostgreSQL test verified migrations in an isolated transaction-scoped schema, legacy document preservation, uniqueness/visibility/foreign-key constraints, downgrade, and re-upgrade. Migration a62c941fe308 was then applied to the local database: its existing document remained unchanged and private; roles/users/chunk counts were preserved; schema comparison found no model differences. Embedding generation and source-location extraction remain outside this verification.

Initial course policy: the authenticated catalog allows open student self-enrolment. The creator is the assigned instructor. Only that instructor with a lecturer/admin role can publish course materials. Students' course-linked notes stay private unless a separate sharing workflow is implemented later. Programme restrictions, enrollment approval, and instructor reassignment remain future work.

Source batch verification: 56 tests pass, including real PDF, PPTX, DOCX, and TXT fixtures processed through upload/extract/chunk APIs. Tests cover blank page/slide numbering, tables and grouped slide text, DOCX sections, TXT line ranges, chunk permissions/revocation, legacy unknown locations, empty/corrupt inputs, and re-extraction invalidation. PostgreSQL migration tests verify JSON round trips, legacy preservation, and downgrade/re-upgrade. Migration b81d732ac459 was applied locally with the existing document and all three chunks/vectors preserved; schema comparison found no differences. Old documents need explicit re-extraction, re-chunking, and re-embedding to obtain accurate locations. Character-based chunk sizes still need tokenizer calibration and retrieval evaluation.

Search batch verification: 63 regression tests pass. An opt-in PostgreSQL API check passes 12 deterministic-vector cases for cosine ranking, top-k, thresholds, course/document filters, exclusion of private/failed/unfinished/null/zero-vector material, and enrollment revocation. A separate full-path smoke check passes using the actual cached model, authenticated API, PostgreSQL cosine query, and source-location response. Temporary database schemas were rolled back; development data was unchanged. This is functional evidence, not a measured course-corpus retrieval score. Search uses exact ranking, with no arbitrary default relevance cutoff. Grounded answering, held-out retrieval evaluation, tokenizer-aware document chunking, and durable processing remain outstanding.

Grounded-answer preparation: /chat, structured claims/evidence, exact-quote validation, server-built citations, no-material abstention, and access/content rechecks around generation are implemented. 77 regression tests pass. Real PostgreSQL tests also cover chat retrieval, citation metadata, private scopes, and revocation during a stubbed generation call. Actual generation remains pending a model-service selection and configuration: no hosted API key is currently configured, and no Ollama executable was found. The endpoint is stateless and returns 503 for non-empty generation requests until connected. Citation provenance checks do not prove semantic faithfulness; evaluation with a real model remains required. No new migration was needed, and temporary integration schemas were rolled back.

Ollama selection and integration: the user selected local Ollama. The adapter now uses /api/chat with the structured answer schema, explicit timeouts/output limits, and controlled offline/missing-model/malformed-response errors. Default model is qwen2.5:1.5b for the roughly 8 GB machine. All 81 regression tests pass, including HTTP-mocked Ollama transport tests. Runtime installation remains blocked by observed network problems: WinGet stalled, direct attempts encountered DNS/TLS errors, and the installer briefly downloaded at about 1 MB per minute before further timeouts. The incomplete installer was removed and no installation/download process remains intentionally running. A live-generation verification command is provided in the README, but actual Ollama inference and quality evaluation have not yet been performed. Configuration defaults work once the runtime and model are installed; no hosted API key is needed.

Durable processing batch: saved job submission/status endpoints and a separate PostgreSQL worker now run extract/chunk/embed as one tracked operation. Active jobs are unique per document; duplicate submissions reuse the job. Manual processing is excluded while a job is active. Session advisory locks prevent a second worker from taking over slow live work, while expired jobs recover after lock release; three interruptions require an explicit fresh retry. Invalid embedding batches cannot mark a document searchable. 89 regression tests and separate real PostgreSQL API/worker/locking/recovery/retry-limit/migration checks pass. Embedding inference was stubbed in worker tests; real embedding/search inference was verified in the earlier search batch. Migration c93e842bd560 adds the job table; no existing record is intentionally modified. Worker supervision, queue capacity controls, and source-version retention remain future work. Ollama still was not found during this batch.

Student interface batch: a local, buildless frontend is served by FastAPI at /app/. It includes registration/sign-in/logout, course enrolment, private uploads/instructor publishing, queued processing/progress/retry, passage search, grounded-answer display, and source-quote dialogs. Document responses now include owner_id so shared materials can show the appropriate read-only controls. The existing local server returns 200 for the page and both assets; 92 regression tests and JavaScript syntax checks pass. No browser was available, so browser-driven flows and visual/keyboard QA remain unverified. Source dialogs show extracted passages, not original PDF/slide images. Chat remains stateless, and live generation still needs Ollama installation. This frontend remains in the local repository; no hosted Site was created or deployed.

Runtime follow-up, 15 October 2026: started the API and separate processing worker locally. PostgreSQL reports migration c93e842bd560 at head. Added `/ready`, which performs a database query and returns a generic 503 on database errors, while `/health` remains a process-liveness check. All 94 regression tests pass, including database failure sanitisation and independent liveness. The running API returns 200 for readiness, the student page, JavaScript, and CSS; JavaScript syntax and whitespace checks pass. Readiness does not establish worker/model availability or migration correctness. Ollama remains unavailable, so real answer generation is still pending installation; browser interaction and visual QA remain unverified.

Original-source download batch, 15 October 2026: added authenticated `GET /documents/{id}/download` plus Download original controls on material cards and source dialogs. The endpoint applies current owner/course permissions, confines resolved storage paths to the upload directory, sanitises attachment filenames, and uses private/no-store and nosniff headers. Six additional tests verify original-byte integrity, anonymous/private access denial, course access revocation, missing files, path escape prevention, and Unicode filenames. All 100 regression tests pass; JavaScript syntax and whitespace checks pass. Browser discovery still returns no available browser, so actual browser download interaction remains unverified. No migration or reprocessing is required. The downloaded file is an attachment; an embedded original-file viewer and page-level navigation remain future work. Access changes cannot remove copies already saved to a device.

Token-budget batch, 16 October 2026: production chunking now checks the actual embedding tokenizer and model limit, including special tokens, before saving each window. It retains the 1000-character cap, reduces overlap for short windows, keeps source segments separate, and calculates TXT locations after trimming whitespace. Embedding rejects oversized legacy chunks with a controlled 400 before inference; a chunking-model failure leaves previous chunks untouched. All 107 regression tests pass. Real PostgreSQL worker/recovery checks pass with model doubles. A separate cached-model/PostgreSQL integration check verifies dense punctuation, Unicode, uninterrupted text, complete character-span coverage, actual chunk/embed API calls, and successful source retrieval of an ending fact. The dense fixture produces five chunks within the real 256-token limit. Test schemas were removed or rolled back; existing development documents were not reprocessed. No new migration is needed. Use Process again to update older materials. This prevents model truncation; it does not establish a course-corpus retrieval score, semantic completeness of every chunk, or generation faithfulness. Generation context budgeting and held-out quality evaluation remain outstanding.

Retrieval evaluation batch, 16 October 2026: added a repeatable evaluator that runs real TXT upload/extract/chunk/embed/search with the cached model and PostgreSQL in an isolated rolled-back schema. A validated, explicitly synthetic development dataset contains 9 documents, 18 answerable questions, and 3 unanswerable probes. Initial results: hit rate at 1 is 17/18 (94.4%), hit rate at 5 is 18/18 (100%), and MRR at 5 is 0.9583. The port-number question ranks fourth and remains an explicit case for future comparison. Reports record individual source passages, timings, dataset/model/implementation metadata, and failures. All 111 regression tests pass, including known-value scoring and invalid-label checks. This is evaluator-development evidence, not held-out course-corpus accuracy. Unanswerable retrievals are recorded without an invented abstention score; real-model generation evaluation remains pending. See [the evaluation guide](RETRIEVAL_EVALUATION.md) for preparing real course sets and repeatable commands.


Hardening batch, November 2026: added the following.
- Instructor-issued course join codes, with roster management and student removal.
- Per-account and per-address rate limits.
- Bounded concurrent generation and query embedding.
- Upload content checks: ZIP-bomb and PDF page limits, and parsing in a child process that is killed on timeout.
- Document deletion and per-user storage quotas.
- Server-side token revocation; a 32+ character secret key; equalised login timing.
- Admin role provisioning, security headers, request IDs and the processing backlog in `/ready`.

All 125 regression tests then passed. Rate-limit counters remain in process memory.

Milestone 5 batch, 25 November 2026: adaptive learning is implemented end to end (see the [backend README](../Backend/README.md#practice-mastery-and-study-plans)).
- **Topics:** instructors define each course's topics.
- **Questions:** every multiple-choice question is tied to a document passage and an exact supporting quote. Students and instructors write them from search results, or draft them with the local model; drafts are kept only with four distinct options and a word-for-word quote. Instructors publish reviewed questions to the class. Visibility follows current read access to the source document. Reprocessing re-anchors questions by their quote, and deleting a source removes its questions while past grades remain.
- **Quizzes:** answer keys stay hidden until submission. Each attempt keeps a copy of every question as asked. Previously missed questions are asked again first.
- **Mastery:** a recency-weighted score (14-day half-life, latest 20 answers), reported as not enough evidence below 4 answers, otherwise weak / developing / strong.
- **Planner:** deterministic. It respects per-weekday availability, the exam-date horizon and self-rated difficulty. It interleaves topics, reschedules missed sessions once, avoids double-counting weak topics, and gives every session a plain-language reason. It rebuilds after each quiz.
- **Student app:** a Practice & plan view, a quiz dialog with source links, and a question editor launched from search results.

Evidence:
- **Regression tests:** all 162 pass. The 37 new tests cover topic and question permissions, exact-quote validation, publishing rules, answer-key hiding, grading, double-submission, revocation, re-anchoring, deletion cascades, mocked generation, mastery levels and recency, and planner profiles (availability, interleaving, explanations, exam horizon, missed-session rescheduling, difficulty, determinism). They also cover a plan changing after a new quiz result.
- **PostgreSQL:** `tests/verify_learning_postgres.py` passes in a rolled-back schema. It checks the models against the migration, the API practice loop, SET NULL/cascade behaviour, constraints, and downgrade/re-upgrade.
- **Browser:** for the first time, the student app was driven in headless Microsoft Edge (Playwright) against an isolated, since-dropped schema with real processing and embeddings. Covered: the lecturer writes and publishes questions; the student quizzes, opens sources, sees mastery change to "needs work", sets availability, exam date and difficulty, and marks a session done. The 390-pixel phone layout has no horizontal overflow, and no page errors occurred. This check found and fixed a quiz-result contrast problem, and right/wrong answers are now also labelled in text.
- **Deployment:** migration `f5c8a2d91b37` has not been applied to the development database.

Limits:
- AI drafting remains untested against a real model because Ollama is still not installed. Drafts need human review: the automated checks confirm structure and quotation, not distractor quality.
- Planner tests validate behaviour on controlled profiles, not learning gains.
- Topic deletion and planning around specific class times (rather than minutes per weekday) are future work.

Milestone 6 batch 1, 25 November 2026: lecture recordings (see the [backend README](../Backend/README.md#lecture-recordings-transcripts-and-notes)).
- **Upload:** audio and video recordings, up to 200 MB and 180 minutes by default. Each is checked by container signature, then by FFprobe in a separate process with a timeout. Recordings are stored as documents, so course sharing, processing jobs, search, cited answers and practice questions apply unchanged.
- **Timestamped transcripts:** from a WebVTT/SubRip caption file, or from local Whisper during processing. Transcript lines are grouped into passages within the embedding token budget, and each passage keeps its time span.
- **Corrections:** the uploader can correct any line. Corrections mark the index stale until the recording is reprocessed, and they are never overwritten by re-transcription.
- **Search-to-playback:** results, citations and quiz sources offer "Play from m:ss". Playback uses short-lived links scoped to one user and one recording; access is rechecked on every range request, and signing out revokes the links.
- **Editable notes:** private to their author and optionally pinned to a time.
- **Transcription quality** is tracked separately from note quality with a word-error-rate evaluator (`tests/evaluate_transcription.py`).

Evidence:
- **Regression tests:** all 187 pass, 25 of them new. They cover caption parsing, transcript grouping, WER, upload checks and clean-up, the separate size limit, transcription once then reuse, the unavailable-transcription message, correction permissions and staleness, caption replacement while processing (409), playback scope/expiry/revocation/range requests, notes privacy and cascade deletion, and real FFprobe/FFmpeg probing and decoding.
- **PostgreSQL:** `tests/verify_recordings_postgres.py` passes in a rolled-back schema. It checks the models against migration `a3d9e6f1c204`, then runs an API loop with real FFprobe and the cached embedding model: upload, processing, search returning the timed source, byte-range playback, a correction, notes and cascades. It also checks constraints and downgrade/re-upgrade.
- **Browser:** headless Microsoft Edge, run against an isolated, since-dropped schema with the real worker and synthesised speech. The lecturer uploads a lecture with captions and corrects a line, which shows the stale banner; reprocessing then makes the correction searchable. The student finds the passage, plays from it, seeks from the transcript and notes, filters the transcript, and writes and edits timed notes. A video uploaded without captions fails with guidance to add captions, then succeeds once captions are added. There were no page errors and no overflow at 390 px. The app's CSP blocked Playwright's eval-based polling, as intended, so the check polls from Python instead.
- **Deployment:** migrations `f5c8a2d91b37` and `a3d9e6f1c204` have not been applied to the development database.

Limits:
- Real Whisper inference is unverified because the model (about 290 MB for whisper-base) has not been downloaded on this connection. Noisy lecture audio still needs a pilot with reference transcripts.
- Not yet done: speaker labels, linking slides to recording moments, and offline capture.
- Recording consent and access requirements must be confirmed with the institution.

Remaining in milestone 6: reduced lecturer/admin features, offline capture of notes (separate from server AI availability), and delivery evidence. Delivery covers automated checks, deployment instructions, backup/restore, a demo dataset and a failure-case demonstration.

This batch supports the existing architecture. A backend rewrite is not justified by the reviewed code.

## Technical references checked

- [Current embedding model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2): 384-dimensional vectors and default truncation beyond 256 word pieces. This supports checking token length instead of relying on character estimates.
- [pgvector documentation](https://github.com/pgvector/pgvector): exact search is the default; approximate indexes trade recall for speed, and filtering with approximate indexes needs attention. This supports establishing a measured exact-search baseline before index tuning.
