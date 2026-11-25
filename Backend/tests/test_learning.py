"""Milestone 5: topics, source-backed questions, quizzes, mastery and the study planner."""
import json
import logging
import unittest
import uuid
from datetime import date, datetime, timedelta, timezone
from unittest.mock import patch
import httpx
from fastapi import HTTPException
from sqlalchemy import select

from test_foundation import APITestCase
from app.models.chunk import DocumentChunk
from app.models.course import Course, Enrollment
from app.models.document import Document
from app.models.learning import QuizAttempt, QuizAttemptAnswer, QuizQuestion, StudyPlanSettings, StudySession, Topic
from app.models.role import Role
from app.models.user import User
from app.schemas.learning import TopicMastery
from app.schemas.search import SearchResponse, SearchResult
from app.schemas.source import SourceLocation
from app.services import llm_service, mastery_service, planner_service
from app.services.mastery_service import Evidence, assess
from app.services.planner_service import build_plan

LEARNING_TABLES = [Topic.__table__, QuizQuestion.__table__, QuizAttempt.__table__, QuizAttemptAnswer.__table__,
                   StudyPlanSettings.__table__, StudySession.__table__]
logging.getLogger("app.services.question_service").setLevel(logging.WARNING)
SUBNET = "A subnet mask separates the network portion of an address from the host portion."
ROUTING = "A router forwards packets between networks using its routing table."


class LearningAPITestCase(APITestCase):
    def setUp(self):
        super().setUp()
        LEARNING_TABLES[0].metadata.create_all(self.engine, tables=LEARNING_TABLES)
        lecturer_role = self.db.scalar(select(Role).where(Role.name == "LECTURER"))
        self.instructor = User(full_name="Lecturer", email="lecturer@example.com", hashed_password=self.fixture_hash, role_id=lecturer_role.id)
        self.db.add(self.instructor)
        self.db.flush()
        self.course = Course(code="NET101", title="Networking", instructor_id=self.instructor.id)
        self.db.add(self.course)
        self.db.flush()
        self.db.add(Enrollment(course_id=self.course.id, user_id=self.owner.id))
        self.shared = Document(owner_id=self.instructor.id, course_id=self.course.id, visibility="course", original_filename="lecture.pdf",
                               stored_filename="lecture.pdf", file_type="pdf", file_size_bytes=10, processing_status="embedded")
        self.private_lecturer = Document(owner_id=self.instructor.id, course_id=self.course.id, visibility="private", original_filename="draft.pdf",
                                         stored_filename="draft.pdf", file_type="pdf", file_size_bytes=10, processing_status="embedded")
        self.db.add_all([self.shared, self.private_lecturer])
        self.db.flush()
        self.chunk = DocumentChunk(document_id=self.shared.id, chunk_index=0, content=SUBNET + " " + ROUTING,
                                   source_location={"kind": "pdf_page", "page": 4})
        self.private_chunk = DocumentChunk(document_id=self.private_lecturer.id, chunk_index=0, content="Draft: " + SUBNET)
        self.owner_chunk = DocumentChunk(document_id=self.document.id, chunk_index=0, content=ROUTING)
        self.subnetting = Topic(course_id=self.course.id, name="Subnetting")
        self.routing = Topic(course_id=self.course.id, name="Routing")
        self.db.add_all([self.chunk, self.private_chunk, self.owner_chunk, self.subnetting, self.routing])
        self.db.commit()

    def question(self, **changes):
        return {"topic_id": str(self.subnetting.id), "chunk_id": str(self.chunk.id), "source_quote": SUBNET,
                "stem": "What does a subnet mask separate?",
                "options": ["Network and host portions", "Two routers", "Ports and sockets", "Packets and frames"],
                "correct_index": 0, "explanation": "The mask splits an address into network and host parts.", **changes}

    async def create_question(self, user=None, **changes):
        response = await self.client.post("/questions", headers=self.headers(user), json=self.question(**changes))
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    async def published_question(self, **changes):
        created = await self.create_question(self.instructor, **changes)
        response = await self.client.post(f"/questions/{created['id']}/publish", headers=self.headers(self.instructor))
        self.assertEqual(response.status_code, 200, response.text)
        return created

    def enrol(self, user):
        self.db.add(Enrollment(course_id=self.course.id, user_id=user.id))
        self.db.commit()

    async def start_quiz(self, user=None, **changes):
        return await self.client.post("/quizzes", headers=self.headers(user), json={"course_id": str(self.course.id), **changes})


class TopicTests(LearningAPITestCase):
    async def test_instructor_manages_topics_and_members_read_them(self):
        response = await self.client.post(f"/courses/{self.course.id}/topics", headers=self.headers(self.instructor), json={"name": " Addressing "})
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["name"], "Addressing")
        response = await self.client.post(f"/courses/{self.course.id}/topics", headers=self.headers(self.instructor), json={"name": "Routing"})
        self.assertEqual(response.status_code, 409)
        response = await self.client.patch(f"/topics/{self.routing.id}", headers=self.headers(self.instructor), json={"description": "Static and dynamic"})
        self.assertEqual(response.json()["description"], "Static and dynamic")
        response = await self.client.get(f"/courses/{self.course.id}/topics", headers=self.headers())
        self.assertEqual([t["name"] for t in response.json()], ["Addressing", "Routing", "Subnetting"])

    async def test_students_cannot_manage_and_outsiders_cannot_read(self):
        response = await self.client.post(f"/courses/{self.course.id}/topics", headers=self.headers(), json={"name": "Mine"})
        self.assertEqual(response.status_code, 403)
        response = await self.client.patch(f"/topics/{self.routing.id}", headers=self.headers(), json={"name": "Hijacked"})
        self.assertEqual(response.status_code, 403)
        response = await self.client.get(f"/courses/{self.course.id}/topics", headers=self.headers(self.other))
        self.assertEqual(response.status_code, 403)


class QuestionAuthoringTests(LearningAPITestCase):
    async def test_student_writes_private_question_from_readable_passage(self):
        created = await self.create_question()
        self.assertEqual(created["visibility"], "private")
        self.assertTrue(created["reviewed"])
        self.assertEqual(created["origin"], "manual")
        self.assertEqual(created["source_location"]["page"], 4)
        self.assertEqual(created["document_id"], str(self.shared.id))
        listed = await self.client.get("/questions", headers=self.headers())
        self.assertEqual([q["id"] for q in listed.json()], [created["id"]])
        listed = await self.client.get("/questions", headers=self.headers(self.instructor))
        self.assertEqual(listed.json(), [])

    async def test_quote_must_be_exact_and_source_readable(self):
        response = await self.client.post("/questions", headers=self.headers(), json=self.question(source_quote="An invented claim"))
        self.assertEqual(response.status_code, 400)
        response = await self.client.post("/questions", headers=self.headers(), json=self.question(chunk_id=str(self.private_chunk.id)))
        self.assertEqual(response.status_code, 404)
        self.enrol(self.other)
        response = await self.client.post("/questions", headers=self.headers(self.other), json=self.question(chunk_id=str(self.owner_chunk.id), source_quote=ROUTING))
        self.assertEqual(response.status_code, 404)

    async def test_question_content_is_validated(self):
        for changes in [{"options": ["Same", "same ", "Other"]}, {"correct_index": 4}, {"options": ["Only one"]}, {"stem": " "}]:
            response = await self.client.post("/questions", headers=self.headers(), json=self.question(**changes))
            self.assertEqual(response.status_code, 422, changes)

    async def test_non_member_cannot_author_for_course(self):
        response = await self.client.post("/questions", headers=self.headers(self.other), json=self.question(chunk_id=str(self.owner_chunk.id)))
        self.assertEqual(response.status_code, 403)

    async def test_only_author_edits_and_deletes(self):
        created = await self.create_question()
        response = await self.client.patch(f"/questions/{created['id']}", headers=self.headers(self.instructor), json={"stem": "Changed by someone else?"})
        self.assertEqual(response.status_code, 404)
        response = await self.client.patch(f"/questions/{created['id']}", headers=self.headers(), json={"correct_index": 1, "topic_id": str(self.routing.id)})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.json()["correct_index"], response.json()["topic_id"]), (1, str(self.routing.id)))
        response = await self.client.patch(f"/questions/{created['id']}", headers=self.headers(), json={"correct_index": 9})
        self.assertEqual(response.status_code, 422)
        response = await self.client.delete(f"/questions/{created['id']}", headers=self.headers(self.other))
        self.assertEqual(response.status_code, 404)
        response = await self.client.delete(f"/questions/{created['id']}", headers=self.headers())
        self.assertEqual(response.status_code, 204)

    async def test_publishing_rules(self):
        own = await self.create_question()
        response = await self.client.post(f"/questions/{own['id']}/publish", headers=self.headers())
        self.assertEqual(response.status_code, 403)
        draft = await self.create_question(self.instructor, chunk_id=str(self.private_chunk.id))
        response = await self.client.post(f"/questions/{draft['id']}/publish", headers=self.headers(self.instructor))
        self.assertEqual(response.status_code, 409)
        self.assertIn("source material", response.json()["detail"])
        published = await self.published_question()
        response = await self.client.patch(f"/questions/{published['id']}", headers=self.headers(self.instructor), json={"reviewed": False})
        self.assertEqual(response.json()["visibility"], "private")
        response = await self.client.post(f"/questions/{published['id']}/publish", headers=self.headers(self.instructor))
        self.assertEqual(response.status_code, 409)
        self.assertIn("mark it reviewed", response.json()["detail"])


class QuizTests(LearningAPITestCase):
    async def test_private_questions_stay_private_and_published_ones_are_shared(self):
        await self.create_question()
        self.enrol(self.other)
        response = await self.start_quiz(self.other)
        self.assertEqual(response.status_code, 409)
        await self.published_question()
        response = await self.start_quiz(self.other)
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["total_count"], 1)
        response = await self.start_quiz(self.owner)
        self.assertEqual(response.json()["total_count"], 2)

    async def test_answer_key_is_hidden_until_submission_then_graded(self):
        await self.published_question()
        await self.create_question(topic_id=str(self.routing.id), source_quote=ROUTING, stem="What forwards packets between networks?",
                                   options=["A switch", "A router", "A hub", "A repeater"], correct_index=1, explanation="Routers forward packets between networks.")
        response = await self.start_quiz()
        quiz = response.json()
        self.assertNotIn(SUBNET, response.text)
        for item in quiz["items"]:
            self.assertIsNone(item["correct_index"])
            self.assertIsNone(item["explanation"])
            self.assertIsNone(item["source"])
        reloaded = await self.client.get(f"/quizzes/{quiz['id']}", headers=self.headers())
        self.assertIsNone(reloaded.json()["items"][0]["correct_index"])
        by_stem = {item["stem"]: item["position"] for item in quiz["items"]}
        answers = {by_stem["What does a subnet mask separate?"]: 0, by_stem["What forwards packets between networks?"]: 0}
        response = await self.client.post(f"/quizzes/{quiz['id']}/submit", headers=self.headers(), json={"answers": answers})
        self.assertEqual(response.status_code, 200, response.text)
        result = response.json()
        self.assertEqual((result["status"], result["correct_count"], result["total_count"]), ("submitted", 1, 2))
        subnet = next(i for i in result["items"] if i["topic_name"] == "Subnetting")
        self.assertTrue(subnet["is_correct"])
        self.assertEqual(subnet["source"]["quote"], SUBNET)
        self.assertEqual(subnet["source"]["source_location"]["page"], 4)
        self.assertEqual(subnet["source"]["document_name"], "lecture.pdf")
        routing = next(i for i in result["items"] if i["topic_name"] == "Routing")
        self.assertEqual((routing["is_correct"], routing["correct_index"]), (False, 1))
        response = await self.client.post(f"/quizzes/{quiz['id']}/submit", headers=self.headers(), json={"answers": answers})
        self.assertEqual(response.status_code, 409)

    async def test_quiz_belongs_to_its_student(self):
        await self.create_question()
        quiz = (await self.start_quiz()).json()
        self.enrol(self.other)
        for request in [self.client.get(f"/quizzes/{quiz['id']}", headers=self.headers(self.other)),
                        self.client.post(f"/quizzes/{quiz['id']}/submit", headers=self.headers(self.other), json={"answers": {}})]:
            self.assertEqual((await request).status_code, 404)
        listed = await self.client.get("/quizzes", headers=self.headers(self.other))
        self.assertEqual(listed.json(), [])

    async def test_invalid_answers_are_rejected(self):
        await self.create_question()
        quiz = (await self.start_quiz()).json()
        for answers in [{"5": 0}, {"0": 4}, {"0": -1}]:
            response = await self.client.post(f"/quizzes/{quiz['id']}/submit", headers=self.headers(), json={"answers": answers})
            self.assertEqual(response.status_code, 422, answers)
        response = await self.client.post(f"/quizzes/{quiz['id']}/submit", headers=self.headers(), json={"answers": {}})
        self.assertEqual(response.json()["correct_count"], 0)
        self.assertIsNone(response.json()["items"][0]["chosen_index"])

    async def test_quiz_rejects_topic_from_another_course(self):
        await self.create_question()
        response = await self.start_quiz(topic_id=str(uuid.uuid4()))
        self.assertEqual(response.status_code, 404)

    async def test_revoked_access_hides_questions_and_explanations(self):
        await self.published_question()
        quiz = (await self.start_quiz()).json()
        self.db.query(Enrollment).filter(Enrollment.user_id == self.owner.id).delete()
        self.db.commit()
        response = await self.client.post(f"/quizzes/{quiz['id']}/submit", headers=self.headers(), json={"answers": {"0": 0}})
        self.assertEqual(response.status_code, 200, response.text)
        item = response.json()["items"][0]
        self.assertTrue(item["source_unavailable"])
        self.assertIsNone(item["explanation"])
        self.assertIsNone(item["source"])
        response = await self.start_quiz()
        self.assertEqual(response.status_code, 403)

    async def test_reprocessing_reanchors_by_quote_or_retires_question(self):
        created = await self.create_question()
        self.db.query(DocumentChunk).filter(DocumentChunk.document_id == self.shared.id).delete()
        self.db.add(DocumentChunk(document_id=self.shared.id, chunk_index=0, content="Intro. " + SUBNET, source_location={"kind": "pdf_page", "page": 7}))
        self.db.commit()
        self.db.expire_all()
        self.assertIsNone(self.db.get(QuizQuestion, uuid.UUID(created["id"])).chunk_id)
        response = await self.start_quiz()
        self.assertEqual(response.status_code, 201, response.text)
        question = self.db.get(QuizQuestion, uuid.UUID(created["id"]))
        self.assertIsNotNone(question.chunk_id)
        self.assertEqual(question.source_location["page"], 7)
        self.db.query(DocumentChunk).filter(DocumentChunk.document_id == self.shared.id).delete()
        self.db.add(DocumentChunk(document_id=self.shared.id, chunk_index=0, content="Rewritten lecture without that sentence."))
        self.db.commit()
        response = await self.start_quiz()
        self.assertEqual(response.status_code, 409)

    async def test_deleting_source_document_removes_its_questions_but_keeps_grades(self):
        created = await self.create_question(chunk_id=str(self.owner_chunk.id), source_quote=ROUTING, topic_id=str(self.routing.id))
        quiz = (await self.start_quiz()).json()
        await self.client.post(f"/quizzes/{quiz['id']}/submit", headers=self.headers(), json={"answers": {"0": 0}})
        with patch("app.services.document_service.UPLOAD_DIR", "missing-test-upload-dir"):
            response = await self.client.delete(f"/documents/{self.document.id}", headers=self.headers())
        self.assertEqual(response.status_code, 204, response.text)
        self.db.expire_all()
        self.assertIsNone(self.db.get(QuizQuestion, uuid.UUID(created["id"])))
        result = (await self.client.get(f"/quizzes/{quiz['id']}", headers=self.headers())).json()
        self.assertEqual(result["correct_count"], 1)
        self.assertTrue(result["items"][0]["source_unavailable"])

    async def test_missed_questions_are_asked_again_first(self):
        first = await self.create_question()
        await self.create_question(stem="Which portion follows the network portion?", options=["Host", "Port", "Frame", "Bit"],
                                   explanation="The rest of the address identifies the host.")
        quiz = (await self.start_quiz(size=2)).json()
        missed_position = next(i["position"] for i in quiz["items"] if i["stem"] == "What does a subnet mask separate?")
        answers = {str(i["position"]): (3 if i["position"] == missed_position else 0) for i in quiz["items"]}
        await self.client.post(f"/quizzes/{quiz['id']}/submit", headers=self.headers(), json={"answers": answers})
        for _ in range(5):
            follow_up = (await self.start_quiz(size=1)).json()
            self.assertEqual(follow_up["items"][0]["stem"], "What does a subnet mask separate?", first)


class GenerationTests(LearningAPITestCase):
    def setUp(self):
        super().setUp()
        self.source = SearchResult(chunk_id=self.chunk.id, document_id=self.shared.id, course_id=self.course.id,
            document_name="lecture.pdf", chunk_index=0, text=self.chunk.content, similarity=0.7,
            source_location=SourceLocation(kind="pdf_page", page=4))
        patcher = patch("app.services.search_service.search", return_value=SearchResponse(query="q", status="matches", results=[self.source]))
        self.search = patcher.start()
        self.addCleanup(patcher.stop)

    def generated(self, **changes):
        return {"status": "ok", "question": "What does a subnet mask separate?", "correct_option": 2,
                "options": ["Two routers", "Ports", "Network and host portions", "Frames"],
                "explanation": "The passage says it separates network and host portions.", "quote": SUBNET, **changes}

    async def generate(self, user=None):
        return await self.client.post("/questions/generate", headers=self.headers(user), json={"topic_id": str(self.subnetting.id), "count": 1})

    async def test_valid_generation_is_saved_unreviewed_with_consistent_key(self):
        with patch("app.services.llm_service.generate_question", return_value=self.generated()) as model:
            response = await self.generate()
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["status"], body["rejected"], len(body["created"])), ("generated", 0, 1))
        question = body["created"][0]
        self.assertEqual(question["options"][question["correct_index"]], "Network and host portions")
        self.assertEqual(sorted(question["options"]), sorted(self.generated()["options"]))
        self.assertEqual((question["origin"], question["reviewed"], question["visibility"]), ("generated", False, "private"))
        self.assertEqual(model.call_args.args, ("Subnetting", self.chunk.content))
        self.assertEqual(self.search.call_args.args[1].course_id, self.course.id)

    async def test_unsupported_or_malformed_output_is_rejected(self):
        for output in [self.generated(quote="A fact that is not in the passage"), self.generated(options=["A", "B", "C"]),
                       self.generated(options=["Same", "same", "C", "D"]), {"status": "insufficient"}, {"unexpected": True},
                       self.generated(correct_option=7)]:
            with patch("app.services.llm_service.generate_question", return_value=output):
                response = await self.generate()
            self.assertEqual(response.status_code, 200, response.text)
            self.assertEqual((response.json()["rejected"], response.json()["created"]), (1, []), output)
        self.assertEqual(self.db.query(QuizQuestion).count(), 0)

    async def test_provider_outage_is_reported_and_bad_output_skipped(self):
        with patch("app.services.llm_service.generate_question", side_effect=HTTPException(status_code=503, detail="Cannot reach Ollama")):
            response = await self.generate()
        self.assertEqual(response.status_code, 503)
        with patch("app.services.llm_service.generate_question", side_effect=HTTPException(status_code=502, detail="invalid")):
            response = await self.generate()
        self.assertEqual(response.json()["rejected"], 1)

    async def test_no_materials_skip_generation_and_outsiders_are_refused(self):
        self.search.return_value = SearchResponse(query="q", status="no_searchable_materials", results=[])
        with patch("app.services.llm_service.generate_question") as model:
            response = await self.generate()
        self.assertEqual(response.json()["status"], "no_searchable_materials")
        model.assert_not_called()
        response = await self.generate(self.other)
        self.assertEqual(response.status_code, 403)

    async def test_generation_request_uses_question_schema(self):
        def handle(request):
            body = json.loads(request.content)
            self.assertIn("options", body["format"]["properties"])
            self.assertIn("untrusted", body["messages"][0]["content"])
            self.assertEqual(json.loads(body["messages"][1]["content"])["passage"], "Passage")
            return httpx.Response(200, json={"done": True, "message": {"content": json.dumps({"status": "insufficient"})}})
        client = httpx.Client(transport=httpx.MockTransport(handle), trust_env=False)
        with patch("app.services.llm_service.httpx.Client", return_value=client):
            self.assertEqual(llm_service.generate_question("Topic", "Passage"), {"status": "insufficient"})


NOW = datetime(2026, 11, 25, 12, tzinfo=timezone.utc)


def evidence(*marks, days_ago=0, attempt=None):
    attempt = attempt or uuid.uuid4()
    return [Evidence(NOW - timedelta(days=days_ago), bool(mark), attempt) for mark in marks]


class MasteryTests(unittest.TestCase):
    def test_too_little_evidence_is_not_called_weak(self):
        result = assess(evidence(0, 0, 0), NOW)
        self.assertEqual((result.level, result.score, result.answers), ("not_enough_evidence", None, 3))

    def test_levels(self):
        self.assertEqual(assess(evidence(1, 0, 0, 0), NOW).level, "weak")
        self.assertEqual(assess(evidence(1, 1, 1, 0), NOW).level, "developing")
        self.assertEqual(assess(evidence(1, 1, 1, 1, 1), NOW).level, "strong")

    def test_recent_answers_count_more(self):
        improving = evidence(0, 0, 0, days_ago=28) + evidence(1, 1, 1, days_ago=0)
        declining = evidence(1, 1, 1, days_ago=28) + evidence(0, 0, 0, days_ago=0)
        self.assertGreater(assess(improving, NOW).score, 0.75)
        self.assertLess(assess(declining, NOW).score, 0.25)

    def test_recent_quiz_scores_are_newest_first(self):
        old, new = uuid.uuid4(), uuid.uuid4()
        result = assess(evidence(1, 1, days_ago=3, attempt=old) + evidence(0, 1, days_ago=1, attempt=new), NOW)
        self.assertEqual(result.recent_quiz_scores, [0.5, 1.0])


def mastery(name, level, score=None, answers=8, recent=()):
    return TopicMastery(topic_id=uuid.uuid5(uuid.NAMESPACE_DNS, name), topic_name=name, level=level, score=score, answers=answers,
                        correct=0, last_practised_at=None, recent_quiz_scores=list(recent))


MONDAY = date(2026, 11, 23)


class PlannerTests(unittest.TestCase):
    weak = mastery("Subnetting", "weak", 0.4, recent=[0.25, 0.5])
    strong = mastery("Routing", "strong", 0.9)
    unknown = mastery("Switching", "not_enough_evidence", answers=1)

    def count(self, blocks):
        totals = {}
        for block in blocks:
            totals[block.topic_id] = totals.get(block.topic_id, 0) + 1
        return totals

    def test_weak_topics_get_more_time_and_availability_is_respected(self):
        minutes = [90, 0, 60, 0, 45, 0, 120]
        blocks, notes = build_plan(MONDAY, None, minutes, {}, [self.weak, self.strong, self.unknown])
        self.assertEqual(notes, [])
        per_day = {}
        for block in blocks:
            per_day[block.session_date] = per_day.get(block.session_date, 0) + block.minutes
        for day, used in per_day.items():
            self.assertLessEqual(used, minutes[day.weekday()])
        self.assertNotIn(MONDAY + timedelta(days=1), per_day)
        self.assertEqual(sum(per_day.values()), 90 + 60 + 45 + 120)
        totals = self.count(blocks)
        self.assertGreater(totals[self.weak.topic_id], totals[self.unknown.topic_id])
        self.assertGreater(totals[self.unknown.topic_id], totals[self.strong.topic_id])
        self.assertEqual({b.kind for b in blocks if b.topic_id == self.unknown.topic_id}, {"quiz"})

    def test_topics_interleave_within_a_day(self):
        blocks, _ = build_plan(MONDAY, None, [90] * 7, {}, [self.weak, self.strong, self.unknown])
        for day in {b.session_date for b in blocks}:
            topics = [b.topic_id for b in blocks if b.session_date == day]
            self.assertEqual(len(topics), len(set(topics)))

    def test_reason_explains_the_adjustment(self):
        blocks, _ = build_plan(MONDAY, MONDAY + timedelta(days=10), [30] * 7, {}, [self.weak])
        self.assertEqual(blocks[0].reason, "Subnetting needs revision after your last two quizzes; the exam is in 10 days.")
        self.assertEqual(blocks[0].kind, "revision")
        blocks, _ = build_plan(MONDAY, None, [30] * 7, {}, [self.unknown])
        self.assertEqual(blocks[0].reason, "Not enough evidence for Switching yet (1 answer); a short quiz will measure it.")

    def test_exam_date_bounds_the_plan(self):
        exam = MONDAY + timedelta(days=3)
        blocks, _ = build_plan(MONDAY, exam, [60] * 7, {}, [self.weak, self.strong])
        self.assertTrue(blocks)
        self.assertTrue(all(b.session_date < exam for b in blocks))
        self.assertEqual(build_plan(MONDAY, MONDAY, [60] * 7, {}, [self.weak])[0], [])
        _, notes = build_plan(MONDAY, MONDAY - timedelta(days=1), [60] * 7, {}, [self.weak])
        self.assertIn("passed", notes[0])

    def test_missed_sessions_are_rescheduled_first_once(self):
        missed_on = MONDAY - timedelta(days=2)
        carry = [(self.unknown.topic_id, missed_on), (self.unknown.topic_id, missed_on - timedelta(days=1)), (self.strong.topic_id, missed_on)]
        blocks, _ = build_plan(MONDAY, None, [60] * 7, {}, [self.weak, self.strong, self.unknown], carry)
        self.assertEqual(blocks[0].topic_id, self.unknown.topic_id)
        earliest = missed_on - timedelta(days=1)
        self.assertTrue(blocks[0].reason.startswith(f"Rescheduled from your missed session on {earliest:%a %d %b}."))
        self.assertEqual(sum(b.reason.startswith("Rescheduled") for b in blocks), 1)

    def test_difficulty_and_today_usage(self):
        even = [mastery("A", "developing", 0.7), mastery("B", "developing", 0.7)]
        hard_b = {even[1].topic_id: 3}
        # One block a day, so the daily interleaving rule does not force equal shares.
        totals = self.count(build_plan(MONDAY, None, [30] * 7, hard_b, even)[0])
        self.assertGreater(totals[even[1].topic_id], totals[even[0].topic_id])
        blocks, _ = build_plan(MONDAY, None, [60] * 7, {}, even, used_minutes_today=45)
        self.assertEqual(sum(b.minutes for b in blocks if b.session_date == MONDAY), 15)

    def test_deterministic_and_handles_empty_inputs(self):
        args = (MONDAY, MONDAY + timedelta(days=14), [60, 30, 0, 90, 45, 0, 20], {}, [self.weak, self.strong, self.unknown])
        self.assertEqual(build_plan(*args), build_plan(*args))
        self.assertIn("no topics", build_plan(MONDAY, None, [60] * 7, {}, [])[1][0])
        self.assertIn("No study time", build_plan(MONDAY, None, [0] * 7, {}, [self.weak])[1][0])


class StudyPlanAPITests(LearningAPITestCase):
    def setUp(self):
        super().setUp()
        self.today = planner_service.server_today()

    def settings(self, **changes):
        return {"exam_date": str(self.today + timedelta(days=10)), "weekday_minutes": [60] * 7, "topic_difficulty": {}, **changes}

    async def test_settings_validation_and_membership(self):
        url = f"/courses/{self.course.id}/study-plan/settings"
        for changes in [{"exam_date": str(self.today - timedelta(days=2))}, {"topic_difficulty": {str(uuid.uuid4()): 2}}]:
            response = await self.client.put(url, headers=self.headers(), json=self.settings(**changes))
            self.assertEqual(response.status_code, 400, changes)
        for changes in [{"weekday_minutes": [60] * 6}, {"weekday_minutes": [601] * 7}, {"topic_difficulty": {str(self.routing.id): 5}}]:
            response = await self.client.put(url, headers=self.headers(), json=self.settings(**changes))
            self.assertEqual(response.status_code, 422, changes)
        response = await self.client.put(url, headers=self.headers(self.other), json=self.settings())
        self.assertEqual(response.status_code, 403)
        response = await self.client.get(f"/courses/{self.course.id}/study-plan", headers=self.headers(), params={"today": str(self.today + timedelta(days=5))})
        self.assertEqual(response.status_code, 400)

    async def test_saved_settings_produce_plan_within_exam_horizon(self):
        response = await self.client.put(f"/courses/{self.course.id}/study-plan/settings", headers=self.headers(),
                                         json=self.settings(topic_difficulty={str(self.routing.id): 3}))
        self.assertEqual(response.status_code, 200, response.text)
        plan = response.json()
        self.assertTrue(plan["settings"]["saved"])
        self.assertEqual(len(plan["sessions"]), 20)
        self.assertTrue(all(self.today <= date.fromisoformat(s["session_date"]) < self.today + timedelta(days=10) for s in plan["sessions"]))
        self.assertTrue(all(s["kind"] == "quiz" for s in plan["sessions"]))
        self.assertIn("you marked it as difficult", next(s["reason"] for s in plan["sessions"] if s["topic_name"] == "Routing"))
        again = await self.client.post(f"/courses/{self.course.id}/study-plan/rebuild", headers=self.headers())
        self.assertEqual(len(again.json()["sessions"]), 20)

    async def test_new_quiz_result_changes_mastery_and_plan(self):
        await self.client.put(f"/courses/{self.course.id}/study-plan/settings", headers=self.headers(), json=self.settings(exam_date=None, weekday_minutes=[30] * 7))
        stems = ["What does a subnet mask separate?", "Which part identifies the device?", "What is split by the mask?", "Which portion is shared on a subnet?"]
        for stem in stems:
            await self.create_question(stem=stem)
        quiz = (await self.start_quiz(size=4)).json()
        response = await self.client.post(f"/quizzes/{quiz['id']}/submit", headers=self.headers(), json={"answers": {str(i): 3 for i in range(4)}})
        self.assertEqual(response.status_code, 200, response.text)
        plan = (await self.client.get(f"/courses/{self.course.id}/study-plan", headers=self.headers())).json()
        subnetting = next(m for m in plan["mastery"] if m["topic_name"] == "Subnetting")
        self.assertEqual((subnetting["level"], subnetting["score"], subnetting["answers"]), ("weak", 0.0, 4))
        planned = [s for s in plan["sessions"] if s["status"] == "planned"]
        totals = {name: sum(s["topic_name"] == name for s in planned) for name in ["Subnetting", "Routing"]}
        self.assertGreater(totals["Subnetting"], totals["Routing"])
        self.assertIn("Subnetting is a weak topic (0% across 4 recent answers).", [s["reason"] for s in planned])
        mastery_response = await self.client.get(f"/courses/{self.course.id}/mastery", headers=self.headers())
        self.assertEqual(mastery_response.json(), plan["mastery"])

    async def test_missed_sessions_are_marked_and_rescheduled_and_completion_counts(self):
        yesterday = self.today - timedelta(days=1)
        self.db.add(StudySession(user_id=self.owner.id, course_id=self.course.id, topic_id=self.routing.id, session_date=yesterday,
                                 minutes=30, kind="quiz", reason="Earlier plan.", status="planned"))
        self.db.commit()
        plan = (await self.client.post(f"/courses/{self.course.id}/study-plan/rebuild", headers=self.headers())).json()
        missed = [s for s in plan["sessions"] if s["status"] == "missed"]
        self.assertEqual(len(missed), 1)
        first = next(s for s in plan["sessions"] if s["status"] == "planned")
        self.assertEqual(first["topic_name"], "Routing")
        self.assertTrue(first["reason"].startswith("Rescheduled from your missed session"))
        response = await self.client.post(f"/study-sessions/{first['id']}/complete", headers=self.headers(self.other))
        self.assertEqual(response.status_code, 404)
        response = await self.client.post(f"/study-sessions/{first['id']}/complete", headers=self.headers())
        self.assertEqual(response.json()["status"], "completed")
        response = await self.client.post(f"/study-sessions/{first['id']}/complete", headers=self.headers())
        self.assertEqual(response.status_code, 409)
        plan = (await self.client.post(f"/courses/{self.course.id}/study-plan/rebuild", headers=self.headers())).json()
        self.assertFalse(any(s["reason"].startswith("Rescheduled") for s in plan["sessions"] if s["status"] == "planned"))
        today_minutes = sum(s["minutes"] for s in plan["sessions"] if s["session_date"] == str(self.today) and s["status"] != "missed")
        self.assertLessEqual(today_minutes, plan["settings"]["weekday_minutes"][self.today.weekday()])


if __name__ == "__main__":
    unittest.main()
