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
5. Next: course permissions and source-location extraction before introducing search and chat.

Verification: 22 regression tests passed through the API/service/password layers using an isolated SQLite database. Python syntax checks passed and pip check reported no broken requirements in Backend/venv. This does not establish PostgreSQL migration or embedding-pipeline correctness.

This batch supports the existing architecture. A backend rewrite is not justified by the reviewed code.

## Technical references checked

- [Current embedding model card](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2): 384-dimensional vectors and default truncation beyond 256 word pieces. This supports checking token length instead of relying on character estimates.
- [pgvector documentation](https://github.com/pgvector/pgvector): exact search is the default; approximate indexes trade recall for speed, and filtering with approximate indexes needs attention. This supports establishing a measured exact-search baseline before index tuning.
