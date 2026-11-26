"""Milestone 6: lecture recordings, timestamped transcripts, playback links and notes."""
import io
import logging
import math
import shutil
import struct
import tempfile
import unittest
import uuid
import wave
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import jwt
from sqlalchemy import select

from embedding_fixtures import CharacterTokenizer, embedding_model
from test_foundation import APITestCase
from app.core.config import settings
from app.core.security import ALGORITHM
from app.models.course import Course, Enrollment
from app.models.document import Document
from app.models.processing_job import ProcessingJob
from app.models.recording import Note, TranscriptSegment
from app.models.role import Role
from app.models.user import User
from app.services import media_service, processing_service
from app.services.chunking_service import located_chunks, recording_windows
from app.services.transcript_service import CaptionError, parse_captions
from app.services.transcription_quality import word_errors

RECORDING_TABLES = [TranscriptSegment.__table__, Note.__table__]
QUIET_LOGGERS = ["app.services.extraction_service", "app.services.processing_service", "app.services.document_service"]
VTT = """WEBVTT - Week 3 lecture

NOTE This file was exported from the lecture capture system.

STYLE
::cue { color: white }

intro
00:00:01.000 --> 00:00:04.500 align:start position:10%
<v Lecturer>A subnet mask separates the <b>network</b> portion

00:04.500 --> 00:09.000
from the host portion &amp; the router uses it.

00:09.000 --> 00:12.000

"""
SRT = """1
00:00:05,000 --> 00:00:07,250
Routers forward packets.

2
00:00:01,000 --> 00:00:04,000
A default route is used last.
"""


def wav_bytes(seconds=1.0, rate=8000):
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(rate)
        audio.writeframes(b"".join(struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * i / rate)))
                                   for i in range(int(seconds * rate))))
    return buffer.getvalue()


class CaptionParsingTests(unittest.TestCase):
    def test_webvtt_headers_notes_styles_settings_tags_and_entities(self):
        cues = parse_captions(VTT.encode(), "vtt")
        self.assertEqual([(c["start"], c["end"]) for c in cues], [(1.0, 4.5), (4.5, 9.0)])
        self.assertEqual(cues[0]["text"], "A subnet mask separates the network portion")
        self.assertEqual(cues[1]["text"], "from the host portion & the router uses it.")

    def test_subrip_with_comma_times_is_sorted(self):
        cues = parse_captions(("﻿" + SRT).encode(), "srt")
        self.assertEqual([c["text"] for c in cues], ["A default route is used last.", "Routers forward packets."])
        self.assertEqual(cues[1]["end"], 7.25)

    def test_repeated_rolling_captions_merge_and_times_clamp_to_the_recording(self):
        data = "WEBVTT\n\n00:01.000 --> 00:02.000\nSame line\n\n00:02.000 --> 00:03.000\nSame line\n\n00:03.000 --> 00:30.000\nLast\n"
        cues = parse_captions(data.encode(), "vtt", duration=10.0)
        self.assertEqual([(c["start"], c["end"], c["text"]) for c in cues], [(1.0, 3.0, "Same line"), (3.0, 10.0, "Last")])

    def test_invalid_caption_files_are_rejected_with_reasons(self):
        cases = [
            (b"00:01.000 --> 00:02.000\nNo header\n", "vtt", None, "WEBVTT"),
            (b"WEBVTT\n\n00:01 --> 00:02.000\nBad time\n", "vtt", None, "Unreadable caption time"),
            (b"WEBVTT\n\n00:05.000 --> 00:02.000\nBackwards\n", "vtt", None, "ends before it starts"),
            (b"WEBVTT\n\nJust text without timing\n", "vtt", None, "timing line"),
            (b"WEBVTT\n\nNOTE only a note\n", "vtt", None, "no timed captions"),
            (b"1\n00:00:01,000 --> 00:00:02,000\n\xff\xfe bad\n", "srt", None, "UTF-8"),
            (b"WEBVTT\n\n01:00.000 --> 01:05.000\nToo late\n", "vtt", 30.0, "beyond the end"),
            (b"WEBVTT\n\n00:01.000 --> 00:02.000\n" + b"x" * 2001 + b"\n", "vtt", None, "2000 characters"),
            (b"anything", "txt", None, ".vtt or .srt"),
        ]
        for data, file_type, duration, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(CaptionError, message):
                parse_captions(data, file_type, duration)


class RecordingChunkTests(unittest.TestCase):
    @staticmethod
    def line(text, start, end):
        return {"text": text, "location": {"kind": "recording", "start_seconds": start, "end_seconds": end}}

    def test_short_lines_group_into_passages_that_keep_their_time_span(self):
        lines = [self.line(f"Sentence number {i} about routing.", i * 5.0, i * 5.0 + 4) for i in range(10)]
        pieces = recording_windows(lines, chunk_size=120)
        self.assertGreater(len(pieces), 1)
        self.assertTrue(all(len(p["content"]) <= 120 for p in pieces))
        self.assertEqual(" ".join(p["content"] for p in pieces), " ".join(l["text"] for l in lines))
        self.assertEqual(pieces[0]["source_location"]["start_seconds"], 0.0)
        self.assertEqual(pieces[-1]["source_location"]["end_seconds"], 49.0)
        for before, after in zip(pieces, pieces[1:]):
            self.assertLessEqual(before["source_location"]["end_seconds"], after["source_location"]["start_seconds"])

    def test_token_budget_applies_and_an_overlong_line_is_split_with_its_times(self):
        tokenizer = CharacterTokenizer()
        lines = [self.line("short opening", 0, 2), self.line("long " * 60, 2, 40), self.line("short ending", 40, 42)]
        pieces = recording_windows(lines, tokenizer=tokenizer, max_tokens=64)
        self.assertTrue(all(len(tokenizer.encode(p["content"])) <= 64 for p in pieces))
        middle = [p for p in pieces if p["content"].startswith("long")]
        self.assertGreater(len(middle), 1)
        self.assertTrue(all(p["source_location"] == {"kind": "recording", "start_seconds": 2, "end_seconds": 40} for p in middle))
        self.assertEqual(pieces[-1]["content"], "short ending")

    def test_recordings_cannot_be_mixed_with_document_sources(self):
        with self.assertRaises(ValueError):
            located_chunks([self.line("a", 0, 1), {"text": "b", "location": {"kind": "pdf_page", "page": 1}}])


class TranscriptionQualityTests(unittest.TestCase):
    def test_word_error_rate_counts_each_error_type(self):
        result = word_errors("The router forwards packets between networks.", "the router forward packets between the networks")
        self.assertEqual((result["substitutions"], result["deletions"], result["insertions"]), (1, 0, 1))
        self.assertAlmostEqual(result["wer"], 2 / 6)
        self.assertEqual(word_errors("A subnet mask.", "a subnet")["deletions"], 1)
        self.assertEqual(word_errors("Don't panic!", "don’t panic")["wer"], 0.0)
        self.assertEqual(word_errors("", "")["wer"], 0.0)
        self.assertEqual(word_errors("", "noise")["wer"], 1.0)


class RecordingAPITestCase(APITestCase):
    def setUp(self):
        super().setUp()
        RECORDING_TABLES[0].metadata.create_all(self.engine, tables=RECORDING_TABLES)
        # Failures here are expected; their tracebacks would only bury the results. Restored afterwards.
        for name in QUIET_LOGGERS:
            logger = logging.getLogger(name)
            self.addCleanup(logger.setLevel, logger.level)
            logger.setLevel(logging.CRITICAL)
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.upload_dir = Path(temporary.name)
        for target in ["app.services.document_service.UPLOAD_DIR", "app.services.extraction_service.UPLOAD_DIR"]:
            patcher = patch(target, str(self.upload_dir))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.probe = patch("app.services.media_service.probe", return_value={"duration": 600.0, "kind": "audio"})
        self.probe_mock = self.probe.start()
        self.addCleanup(self.probe.stop)
        lecturer_role = self.db.scalar(select(Role).where(Role.name == "LECTURER"))
        self.instructor = User(full_name="Lecturer", email="lecturer@example.com", hashed_password=self.fixture_hash, role_id=lecturer_role.id)
        self.db.add(self.instructor)
        self.db.flush()
        self.course = Course(code="NET101", title="Networking", instructor_id=self.instructor.id)
        self.db.add(self.course)
        self.db.flush()
        self.db.add(Enrollment(course_id=self.course.id, user_id=self.owner.id))
        self.db.commit()

    async def upload(self, user=None, name="week3.wav", content=None, captions=VTT, caption_name="week3.vtt", shared=True):
        files = {"file": (name, content if content is not None else wav_bytes())}
        if captions is not None:
            files["captions"] = (caption_name, captions.encode() if isinstance(captions, str) else captions)
        data = {"course_id": str(self.course.id), "visibility": "course"} if shared else {}
        return await self.client.post("/documents/upload", headers=self.headers(user or self.instructor), files=files, data=data)

    async def recording(self, **kwargs):
        response = await self.upload(**kwargs)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def process(self, document_id, user=None):
        headers = self.headers(user or self.instructor)
        with patch("app.services.embedding_service.get_model", return_value=embedding_model()):
            for step in ["extract", "chunk", "embed"]:
                response = await self.client.post(f"/documents/{document_id}/{step}", headers=headers)
                self.assertEqual(response.status_code, 200, (step, response.text))
        return response.json()


class RecordingUploadTests(RecordingAPITestCase):
    async def test_recording_with_captions_is_shared_with_the_course(self):
        document = await self.recording()
        self.assertEqual((document["file_type"], document["media_kind"], document["media_duration_seconds"]), ("wav", "audio", 600.0))
        response = await self.client.get(f"/documents/{document['id']}/transcript", headers=self.headers(self.owner))
        self.assertEqual(response.status_code, 200, response.text)
        transcript = response.json()
        self.assertEqual((transcript["source"], transcript["editable"], transcript["stale"]), ("captions", False, False))
        self.assertEqual([s["start_seconds"] for s in transcript["segments"]], [1.0, 4.5])
        hidden = await self.client.get(f"/documents/{document['id']}/transcript", headers=self.headers(self.other))
        self.assertEqual(hidden.status_code, 404)

    async def test_mismatched_unusable_and_oversized_recordings_leave_no_file(self):
        cases = [
            ({"name": "lecture.mp3", "content": b"plain text pretending to be audio"}, 400, "does not match"),
            ({"captions": "not captions"}, 400, "WEBVTT"),
            ({"captions": VTT, "caption_name": "captions.txt"}, 400, ".vtt or .srt"),
            ({"name": "notes.txt", "content": b"Some notes"}, 400, "only accompany a recording"),
        ]
        for kwargs, code, message in cases:
            with self.subTest(message=message):
                response = await self.upload(**kwargs)
                self.assertEqual(response.status_code, code, response.text)
                self.assertIn(message, response.json()["detail"])
        self.probe_mock.side_effect = media_service.MediaError("The recording has no audio track")
        response = await self.upload(captions=None)
        self.assertEqual((response.status_code, response.json()["detail"]), (400, "The recording has no audio track."))
        self.probe_mock.side_effect = media_service.MediaToolMissing("missing")
        response = await self.upload(captions=None)
        self.assertEqual(response.status_code, 503)
        self.probe_mock.side_effect = None
        with patch.object(settings, "MAX_RECORDING_MB", 1):
            response = await self.upload(content=b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * (1024 * 1024))
        self.assertEqual(response.status_code, 400)
        self.assertIn("1 MB for recordings", response.json()["detail"])
        self.assertEqual(list(self.upload_dir.iterdir()), [])
        self.assertEqual(self.db.query(Document).filter(Document.file_type == "wav").count(), 0)

    async def test_recordings_have_their_own_size_limit(self):
        # Larger than the 20 MB document limit, within the recording limit.
        content = b"RIFF\x00\x00\x00\x00WAVE" + b"\x00" * (21 * 1024 * 1024)
        response = await self.upload(content=content, captions=None)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["file_size_bytes"], len(content))


class TranscriptProcessingTests(RecordingAPITestCase):
    async def test_captions_become_timestamped_searchable_passages(self):
        document = await self.recording()
        chunks = await self.process(document["id"])
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0]["source_location"], {
            "kind": "recording", "start_seconds": 1.0, "end_seconds": 9.0, "page": None, "slide": None,
            "paragraph": None, "table": None, "section": None, "line_start": None, "line_end": None})
        self.assertIn("network portion from the host portion", chunks[0]["content"])
        stored = self.db.get(Document, uuid.UUID(document["id"]))
        self.db.refresh(stored)
        self.assertEqual(stored.processing_status, "embedded")

    async def test_without_a_transcript_whisper_runs_once_and_its_lines_are_kept(self):
        document = await self.recording(captions=None)
        cues = [{"start": 0.0, "end": 3.2, "text": "Welcome to week three."}, {"start": 3.2, "end": 7.9, "text": "Today: subnetting."}]
        with patch("app.services.transcript_service.transcribe", return_value=cues) as recognizer:
            await self.process(document["id"])
            await self.process(document["id"])
        recognizer.assert_called_once()
        transcript = (await self.client.get(f"/documents/{document['id']}/transcript", headers=self.headers(self.instructor))).json()
        self.assertEqual(transcript["source"], f"whisper:{settings.WHISPER_MODEL}")
        self.assertEqual([s["text"] for s in transcript["segments"]], ["Welcome to week three.", "Today: subnetting."])

    async def test_unavailable_transcription_explains_how_to_continue(self):
        document = await self.recording(captions=None)
        with patch.object(settings, "TRANSCRIPTION_PROVIDER", "disabled"):
            response = await self.client.post(f"/documents/{document['id']}/extract", headers=self.headers(self.instructor))
            self.assertEqual(response.status_code, 503)
            self.assertIn("caption file", response.json()["detail"])
            job = ProcessingJob(document_id=uuid.UUID(document["id"]))
            self.db.add(job)
            self.db.commit()
            processing_service.process_claimed_job(self.db, job)
        self.db.refresh(job)
        self.assertEqual((job.status, job.stage), ("failed", "extract"))
        self.assertIn("Add a caption file", job.error_message)
        self.assertEqual(self.db.get(Document, uuid.UUID(document["id"])).processing_status, "failed")

    async def test_owner_corrections_mark_the_index_stale_until_reprocessed(self):
        document = await self.recording()
        await self.process(document["id"])
        url = f"/documents/{document['id']}/transcript"
        segment = (await self.client.get(url, headers=self.headers(self.instructor))).json()["segments"][0]
        edit = f"{url}/segments/{segment['id']}"
        self.assertEqual((await self.client.patch(edit, headers=self.headers(self.owner), json={"text": "Changed"})).status_code, 403)
        self.assertEqual((await self.client.patch(edit, headers=self.headers(self.other), json={"text": "Changed"})).status_code, 404)
        self.assertEqual((await self.client.patch(edit, headers=self.headers(self.instructor), json={"text": "  "})).status_code, 422)
        missing = f"{url}/segments/{uuid.uuid4()}"
        self.assertEqual((await self.client.patch(missing, headers=self.headers(self.instructor), json={"text": "x"})).status_code, 404)
        response = await self.client.patch(edit, headers=self.headers(self.instructor), json={"text": "A subnet mask splits an address."})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.json()["stale"])
        self.assertTrue(response.json()["segments"][0]["edited"])
        chunks = await self.process(document["id"])
        self.assertTrue(chunks[0]["content"].startswith("A subnet mask splits an address."))
        self.assertFalse((await self.client.get(url, headers=self.headers(self.owner))).json()["stale"])

    async def test_replacing_and_clearing_the_transcript(self):
        document = await self.recording()
        url = f"/documents/{document['id']}/transcript"
        response = await self.client.put(url, headers=self.headers(self.instructor), files={"captions": ("fixed.srt", SRT.encode())})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([s["text"] for s in response.json()["segments"]], ["A default route is used last.", "Routers forward packets."])
        denied = await self.client.put(url, headers=self.headers(self.owner), files={"captions": ("fixed.srt", SRT.encode())})
        self.assertEqual(denied.status_code, 403)
        self.db.add(ProcessingJob(document_id=uuid.UUID(document["id"])))
        self.db.commit()
        busy = await self.client.put(url, headers=self.headers(self.instructor), files={"captions": ("fixed.srt", SRT.encode())})
        self.assertEqual(busy.status_code, 409)
        self.assertEqual((await self.client.delete(url, headers=self.headers(self.instructor))).status_code, 409)
        self.db.query(ProcessingJob).delete()
        self.db.commit()
        self.assertEqual((await self.client.delete(url, headers=self.headers(self.instructor))).status_code, 204)
        cleared = (await self.client.get(url, headers=self.headers(self.instructor))).json()
        self.assertEqual((cleared["source"], cleared["segments"]), (None, []))

    async def test_transcript_endpoints_reject_documents_that_are_not_recordings(self):
        response = await self.client.get(f"/documents/{self.document.id}/transcript", headers=self.headers())
        self.assertEqual((response.status_code, response.json()["detail"]), (400, "This material is not a recording"))


class PlaybackTests(RecordingAPITestCase):
    async def link(self, document_id, user=None):
        response = await self.client.post(f"/documents/{document_id}/media-link", headers=self.headers(user or self.owner))
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    async def test_enrolled_student_streams_and_seeks_with_a_scoped_link(self):
        content = wav_bytes()
        document = await self.recording(content=content)
        link = await self.link(document["id"])
        self.assertEqual(link["media_type"], "audio/wav")
        self.assertNotIn(str(self.owner.id), link["url"].split("?")[0])
        response = await self.client.get(link["url"])
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual((response.content, response.headers["content-type"]), (content, "audio/wav"))
        self.assertEqual(response.headers["accept-ranges"], "bytes")
        self.assertEqual(response.headers["cache-control"], "private, no-store")
        partial = await self.client.get(link["url"], headers={"Range": "bytes=100-149"})
        self.assertEqual((partial.status_code, partial.content), (206, content[100:150]))

    async def test_links_are_scoped_revocable_and_never_sign_in_tokens(self):
        document = await self.recording()
        link = await self.link(document["id"])
        token = link["url"].split("token=", 1)[1]
        self.assertEqual((await self.client.get("/users/me", headers={"Authorization": f"Bearer {token}"})).status_code, 401)
        sign_in = self.headers(self.owner)["Authorization"].split()[1]
        self.assertEqual((await self.client.get(f"/documents/{document['id']}/media?token={sign_in}")).status_code, 401)
        other = await self.recording(captions=None)
        self.assertEqual((await self.client.get(f"/documents/{other['id']}/media?token={token}")).status_code, 401)
        expired = jwt.encode({"sub": str(self.owner.id), "doc": document["id"], "typ": "media", "aud": "uniknowledge-media",
                              "exp": datetime.now(timezone.utc) - timedelta(seconds=1)}, settings.SECRET_KEY, algorithm=ALGORITHM)
        self.assertEqual((await self.client.get(f"/documents/{document['id']}/media?token={expired}")).status_code, 401)
        # Leaving the course stops playback at once, even with an unexpired link.
        self.assertEqual((await self.client.delete(f"/courses/{self.course.id}/enroll", headers=self.headers(self.owner))).status_code, 204)
        self.assertEqual((await self.client.get(link["url"])).status_code, 404)
        # Signing out revokes links issued before it.
        mine = await self.recording(user=self.owner, shared=False)
        own_link = await self.link(mine["id"])
        self.assertEqual((await self.client.get(own_link["url"])).status_code, 200)
        self.assertEqual((await self.client.post("/auth/logout", headers=self.headers(self.owner))).status_code, 204)
        self.assertEqual((await self.client.get(own_link["url"])).status_code, 401)

    async def test_only_readable_recordings_get_links(self):
        document = await self.recording()
        self.assertEqual((await self.client.post(f"/documents/{document['id']}/media-link", headers=self.headers(self.other))).status_code, 404)
        self.assertEqual((await self.client.post(f"/documents/{self.document.id}/media-link", headers=self.headers())).status_code, 400)
        self.assertEqual((await self.client.get(f"/documents/{document['id']}/media")).status_code, 422)


class NoteTests(RecordingAPITestCase):
    async def test_private_timestamped_notes_follow_access_to_the_material(self):
        document = await self.recording()
        url = f"/documents/{document['id']}/notes"
        later = await self.client.post(url, headers=self.headers(self.owner), json={"body": " Mask = network bits ", "timestamp_seconds": 95.5})
        self.assertEqual(later.status_code, 201, later.text)
        self.assertEqual(later.json()["body"], "Mask = network bits")
        await self.client.post(url, headers=self.headers(self.owner), json={"body": "Ask about /30 links"})
        await self.client.post(url, headers=self.headers(self.owner), json={"body": "Intro", "timestamp_seconds": 2})
        notes = (await self.client.get(url, headers=self.headers(self.owner))).json()
        self.assertEqual([n["body"] for n in notes], ["Intro", "Mask = network bits", "Ask about /30 links"])
        self.assertEqual((await self.client.get(url, headers=self.headers(self.instructor))).json(), [])
        self.assertEqual((await self.client.get(url, headers=self.headers(self.other))).status_code, 404)

        note = f"/notes/{later.json()['id']}"
        self.assertEqual((await self.client.patch(note, headers=self.headers(self.instructor), json={"body": "x"})).status_code, 404)
        edited = await self.client.patch(note, headers=self.headers(self.owner), json={"body": "Mask marks the network bits", "timestamp_seconds": None})
        self.assertEqual(edited.status_code, 200, edited.text)
        self.assertEqual((edited.json()["body"], edited.json()["timestamp_seconds"]), ("Mask marks the network bits", None))
        self.assertEqual((await self.client.patch(note, headers=self.headers(self.owner), json={"body": None})).status_code, 422)
        past_end = await self.client.post(url, headers=self.headers(self.owner), json={"body": "Too late", "timestamp_seconds": 900})
        self.assertEqual(past_end.status_code, 400)
        untimed_only = await self.client.post(f"/documents/{self.document.id}/notes", headers=self.headers(), json={"body": "x", "timestamp_seconds": 3})
        self.assertEqual(untimed_only.status_code, 400)

        await self.client.delete(f"/courses/{self.course.id}/enroll", headers=self.headers(self.owner))
        self.assertEqual((await self.client.get(url, headers=self.headers(self.owner))).status_code, 404)
        self.assertEqual((await self.client.patch(note, headers=self.headers(self.owner), json={"body": "y"})).status_code, 404)
        self.assertEqual((await self.client.delete(note, headers=self.headers(self.owner))).status_code, 204)

    async def test_deleting_a_recording_removes_its_transcript_and_notes(self):
        document = await self.recording()
        await self.client.post(f"/documents/{document['id']}/notes", headers=self.headers(self.owner), json={"body": "Keep?"})
        response = await self.client.delete(f"/documents/{document['id']}", headers=self.headers(self.instructor))
        self.assertEqual(response.status_code, 204, response.text)
        self.assertEqual(self.db.query(TranscriptSegment).count(), 0)
        self.assertEqual(self.db.query(Note).count(), 0)
        self.assertEqual(list(self.upload_dir.iterdir()), [])


@unittest.skipUnless(shutil.which(settings.FFPROBE_PATH) and shutil.which(settings.FFMPEG_PATH), "FFmpeg is not installed")
class RealFFmpegTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)

    def test_probe_and_decode_a_real_recording(self):
        path = self.folder / "tone.wav"
        path.write_bytes(wav_bytes(seconds=2.0))
        info = media_service.probe(str(path))
        self.assertEqual(info["kind"], "audio")
        self.assertAlmostEqual(info["duration"], 2.0, places=2)
        samples = media_service.decode_audio(str(path), info["duration"])
        self.assertAlmostEqual(len(samples) / media_service.SAMPLE_RATE, 2.0, places=2)
        with open(path, "rb") as file:
            media_service.check_signature(file, "wav")

    def test_probe_rejects_files_that_are_not_media(self):
        path = self.folder / "fake.mp4"
        path.write_bytes(b"\x00\x00\x00\x18ftypisom" + b"not really a movie" * 10)
        with self.assertRaises(media_service.MediaError):
            media_service.probe(str(path))

    def test_recording_length_limit(self):
        path = self.folder / "tone.wav"
        path.write_bytes(wav_bytes(seconds=2.0))
        with patch.object(settings, "MAX_RECORDING_MINUTES", 0.01), self.assertRaisesRegex(media_service.MediaError, "limited"):
            media_service.probe(str(path))


if __name__ == "__main__":
    unittest.main()
