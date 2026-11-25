import copy
import unittest
from pathlib import Path

from pydantic import ValidationError
from evaluation.retrieval import Dataset, Question, first_relevant_rank, summarise


class RetrievalEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.data = {"version": "test-v1", "description": "fixture", "split": "development",
                     "materials": [{"id": "doc", "course": "course", "title": "Notes", "text": "A supported fact."}],
                     "questions": [{"id": "q", "query": "Which fact?", "course": "course",
                                    "evidence": [{"document": "doc", "quote": "supported fact"}]}]}

    def test_starter_dataset_has_explicit_development_label_and_valid_sources(self):
        path = Path(__file__).resolve().parents[1] / "evaluation/datasets/starter.json"
        dataset = Dataset.model_validate_json(path.read_bytes())
        self.assertEqual(dataset.split, "development")
        self.assertEqual(len(dataset.materials), 9)
        self.assertEqual(sum(q.kind == "answerable" for q in dataset.questions), 18)
        self.assertEqual(sum(q.kind == "unanswerable" for q in dataset.questions), 3)

    def test_invalid_labels_and_duplicate_ids_are_rejected(self):
        for mutation in [
            lambda d: d["materials"].append(copy.deepcopy(d["materials"][0])),
            lambda d: d["questions"].append(copy.deepcopy(d["questions"][0])),
            lambda d: d["questions"][0].update(course="unknown"),
            lambda d: d["questions"][0]["evidence"][0].update(document="unknown"),
            lambda d: d["questions"][0]["evidence"][0].update(quote="invented quote"),
            lambda d: d["questions"][0].update(evidence=[]),
            lambda d: d["questions"][0].update(kind="unanswerable"),
        ]:
            data = copy.deepcopy(self.data)
            mutation(data)
            with self.assertRaises(ValidationError):
                Dataset.model_validate(data)

    def test_right_document_without_evidence_is_not_a_hit(self):
        question = Question.model_validate(self.data["questions"][0])
        results = [{"document_id": "uuid", "text": "Different fact"},
                   {"document_id": "other", "text": "supported fact"},
                   {"document_id": "uuid", "text": "supported\n fact"}]
        self.assertEqual(first_relevant_rank(question, results, {"doc": "uuid"}), 3)
        self.assertIsNone(first_relevant_rank(question, results[:2], {"doc": "uuid"}))

    def test_metrics_handle_misses_ranks_and_exclude_unanswerable_probes(self):
        rows = [{"id": str(i), "kind": "answerable", "first_relevant_rank": rank, "latency_ms": i + 1}
                for i, rank in enumerate([1, 2, 5, None])]
        rows.append({"id": "probe", "kind": "unanswerable", "first_relevant_rank": None, "latency_ms": 100})
        metrics = summarise(rows)
        self.assertEqual(metrics["hit_rate_at_1"], .25)
        self.assertEqual(metrics["hit_rate_at_5"], .75)
        self.assertAlmostEqual(metrics["mrr_at_5"], (1 + .5 + .2) / 4)
        self.assertEqual(metrics["missed_question_ids"], ["3"])
        self.assertEqual(metrics["query_latency_ms"], {"median": 3, "p95": 100})
        with self.assertRaises(ValueError):
            summarise([])
