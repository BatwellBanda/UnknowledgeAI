# Retrieval evaluation

The evaluator measures whether authenticated search retrieves labelled evidence after real TXT upload, extraction, token-aware chunking, and embedding. It uses the cached MiniLM model and PostgreSQL exact cosine search. It creates its own transaction-scoped schema and temporary upload directory, then rolls back the schema and removes those files. Development accounts, courses, documents, and uploads are preserved, including on a failed evaluation.

Run from `Backend` with PostgreSQL available, the configured database role allowed to create schemas, pgvector installed, and the embedding model already cached:

```powershell
.\venv\Scripts\python.exe scripts/evaluate_retrieval.py
```

The default dataset is [starter.json](../Backend/evaluation/datasets/starter.json). The JSON report is written to `tmp/evaluation/retrieval-baseline.json`, which Git ignores. The evaluator forces offline model-cache loading; a missing cached model fails the run rather than downloading it. It does not require the API server, processing worker, or Ollama to be running. Avoid running tests or other model workloads simultaneously when comparing query latency.

## What the metrics mean

| Metric | Definition |
| --- | --- |
| Hit rate at 1 | Fraction of answerable questions whose first result contains labelled evidence in its labelled document. |
| Hit rate at 5 | Fraction of answerable questions with at least one such result among the first five. |
| MRR at 5 | Mean reciprocal rank of the first matching result; a miss contributes zero. |
| Median / p95 query latency | Single warmed authenticated in-process API call per question, including query embedding and database search. P95 uses the nearest-rank method. Includes unanswerable probes. Excludes ingestion, model loading, network, and generation. |

A matching filename alone is insufficient: the passage must contain the labelled quote after whitespace normalisation. A label split across chunks can therefore produce a miss. A question with several acceptable evidence labels receives credit for finding any one of them; this is not multi-source recall or a measure of complete answer support.

Unanswerable probes have no expected evidence. Their retrieved passages and similarity values are recorded for review, but they are excluded from retrieval hit-rate and MRR denominators. Search currently returns nearest passages without a calibrated default relevance cutoff, so returning a passage is not a claim that the question is answerable. Neither a similarity threshold nor an abstention score is inferred from these three probes.

The report includes dataset/version and implementation hashes, model revision/token limit, package versions, hardware details, chunk settings, individual rankings/source locations, timing, and missed question IDs. A report is written only after the full run succeeds; failed runs do not replace an earlier report.

## Initial development baseline: 16 October 2026

The synthetic practice dataset contains 9 short TXT documents across Networking, Programming, and Databases; 18 answerable questions; and 3 unanswerable probes. Half of the answerable questions use a course filter and half search the whole fixture library. It is explicitly a **development dataset**, not held-out university evidence.

| Result | Observed |
| --- | --- |
| Hit rate at 1 | 17/18 (94.4%) |
| Hit rate at 5 | 18/18 (100%) |
| MRR at 5 | 0.9583 |
| Top-five misses | 0 |

The first run used the cached `all-MiniLM-L6-v2` model on CPU, 256-token limit, Python 3.14.6, Windows 11, and an Intel processor with 8 logical CPUs. Query `net-06`, “Which identifier distinguishes applications running on the same host?”, ranked the labelled port-number passage fourth. Keep this case when comparing future changes. Each filtered course has only three short documents, making top-five retrieval particularly easy. Use a larger real course corpus with plausible distractors before drawing conclusions about search quality; no production accuracy claim follows.

## Use real course material

Copy the starter JSON to a separate dataset and replace its materials and questions. Each material has a unique stable `id`, a `course` label, a `title`, and UTF-8 `text`. The evaluator uploads that text as TXT; original PDF/PPTX extraction accuracy is covered separately and is not measured by this dataset format.

Each question has a unique `id`, a `query`, an optional `course` filter, and a `kind` of `answerable` or `unanswerable`. Answerable questions need at least one evidence object with `document` equal to a material ID and `quote` copied exactly from that material. Use a short supporting passage. Unanswerable questions have an empty or omitted `evidence` list. Review their labels against the complete corpus rather than assuming a question is unanswerable because one document lacks its answer. Validation rejects duplicate IDs, unknown course/document references, missing quotations, and evidence outside a selected course.

Start with the roadmap's 30–50 answerable questions and at least 10 unanswerable questions, using varied wording and realistic confusing alternatives. Separate development and held-out questions before tuning. Use the development file for chunking/model/threshold changes, then freeze those choices and evaluate the held-out file once for the assessment. The `split` field records the declared split; it cannot enforce who has seen the questions. Keep material and query identifiers stable across comparable runs.

```powershell
.\venv\Scripts\python.exe scripts/evaluate_retrieval.py --dataset evaluation/datasets/course-development.json --output ..\tmp\evaluation\course-development.json
.\venv\Scripts\python.exe scripts/evaluate_retrieval.py --dataset evaluation/datasets/course-held-out.json --output ..\tmp\evaluation\course-held-out.json
```

The two course dataset files above are examples of files to prepare; they are not supplied. Run several repetitions under similar idle conditions before comparing latency. Inspect failures and source passages alongside aggregate scores. Answer faithfulness, prompt injection, citation support, and correct abstention still require a separate evaluation with the installed generation model and human-reviewed labels.
