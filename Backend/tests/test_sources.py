import io
import tempfile
import uuid
from pathlib import Path
from unittest.mock import patch
from docx import Document as DocxDocument
from pptx import Presentation
from pptx.util import Inches
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject, NameObject, DecodedStreamObject

from test_foundation import APITestCase
from embedding_fixtures import embedding_model
from app.models.chunk import DocumentChunk
from app.models.document import Document
from app.models.course import Course, Enrollment
from app.services.chunking_service import chunk_spans, located_chunks, split_into_chunks


def pdf_bytes():
    writer = PdfWriter()
    font = writer._add_object(DictionaryObject({
        NameObject("/Type"): NameObject("/Font"),
        NameObject("/Subtype"): NameObject("/Type1"),
        NameObject("/BaseFont"): NameObject("/Helvetica"),
    }))
    for content in [b"Page one material", b"", b"Page three material"]:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject({NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})})
        stream = DecodedStreamObject()
        stream.set_data(b"BT /F1 12 Tf 72 720 Td (" + content + b") Tj ET")
        page[NameObject("/Contents")] = writer._add_object(stream)
    output = io.BytesIO()
    writer.write(output)
    return output.getvalue()


def pptx_bytes():
    presentation = Presentation()
    for text in ["First slide material", "", "Third slide material"]:
        slide = presentation.slides.add_slide(presentation.slide_layouts[6])
        slide.shapes.add_textbox(Inches(1), Inches(1), Inches(6), Inches(1)).text = text
    table = slide.shapes.add_table(1, 2, Inches(1), Inches(3), Inches(6), Inches(1)).table
    table.cell(0, 0).text = "Table fact"
    table.cell(0, 1).text = "Table value"
    group = slide.shapes.add_group_shape()
    group.shapes.add_textbox(Inches(1), Inches(5), Inches(6), Inches(1)).text = "Grouped text"
    output = io.BytesIO()
    presentation.save(output)
    return output.getvalue()


def docx_bytes():
    doc = DocxDocument()
    doc.add_heading("Programming", level=1)
    doc.add_paragraph("Paragraph before the table")
    table = doc.add_table(rows=1, cols=2)
    table.cell(0, 0).text = "Variable"
    table.cell(0, 1).text = "A named value"
    doc.add_paragraph("Paragraph after the table")
    doc.add_heading("Networking", level=1)
    doc.add_paragraph("Networking paragraph")
    output = io.BytesIO()
    doc.save(output)
    return output.getvalue()


class SourceAPITests(APITestCase):
    def setUp(self):
        super().setUp()
        model_patch = patch("app.services.embedding_service.get_model", return_value=embedding_model())
        model_patch.start()
        self.addCleanup(model_patch.stop)
        temporary = tempfile.TemporaryDirectory()
        self.upload_dir = Path(temporary.name)
        self.addCleanup(temporary.cleanup)
        for target in ["app.services.document_service.UPLOAD_DIR", "app.services.extraction_service.UPLOAD_DIR"]:
            patcher = patch(target, str(self.upload_dir))
            patcher.start()
            self.addCleanup(patcher.stop)

    async def process(self, filename, data):
        uploaded = await self.client.post("/documents/upload", headers=self.headers(), files={"file": (filename, data)})
        self.assertEqual(uploaded.status_code, 200, uploaded.text)
        document_id = uploaded.json()["id"]
        extracted = await self.client.post(f"/documents/{document_id}/extract", headers=self.headers())
        self.assertEqual(extracted.status_code, 200, extracted.text)
        chunked = await self.client.post(f"/documents/{document_id}/chunk", headers=self.headers())
        self.assertEqual(chunked.status_code, 200, chunked.text)
        return document_id, chunked.json()

    async def test_pdf_pages_survive_empty_pages_and_api_chunking(self):
        document_id, chunks = await self.process("lecture.pdf", pdf_bytes())
        self.assertEqual([c["source_location"]["page"] for c in chunks], [1, 3])
        self.assertEqual([c["content"] for c in chunks], ["Page one material", "Page three material"])
        read = await self.client.get(f"/documents/{document_id}/chunks", headers=self.headers())
        self.assertEqual(read.json(), chunks)

    async def test_pptx_slides_tables_and_groups_are_extracted(self):
        _, chunks = await self.process("lecture.pptx", pptx_bytes())
        self.assertEqual([c["source_location"]["slide"] for c in chunks], [1, 3])
        self.assertIn("Table fact | Table value", chunks[1]["content"])
        self.assertIn("Grouped text", chunks[1]["content"])

    async def test_docx_paragraph_sections_and_tables_preserve_order(self):
        _, chunks = await self.process("lecture.docx", docx_bytes())
        self.assertEqual([c["source_location"]["kind"] for c in chunks], ["docx_paragraph", "docx_paragraph", "docx_table", "docx_paragraph", "docx_paragraph", "docx_paragraph"])
        self.assertIn("Variable | A named value", chunks[2]["content"])
        self.assertEqual(chunks[2]["source_location"]["section"], "Programming")
        self.assertEqual(chunks[-1]["source_location"]["section"], "Networking")
        self.assertEqual(chunks[-1]["source_location"]["paragraph"], 5)

    async def test_txt_line_ranges_preserve_blank_lines_and_utf8_bom(self):
        lines = ["", ""] + [f"LINE{i:03d} " + "content " * 8 for i in range(1, 61)]
        _, chunks = await self.process("lecture.txt", ("\ufeff" + "\r\n".join(lines)).encode("utf-8"))
        self.assertGreater(len(chunks), 1)
        for chunk in chunks:
            location = chunk["source_location"]
            self.assertEqual(location["kind"], "txt_lines")
            for word in chunk["content"].split():
                if word.startswith("LINE"):
                    line_number = int(word[4:]) + 2
                    self.assertLessEqual(location["line_start"], line_number)
                    self.assertGreaterEqual(location["line_end"], line_number)

    async def test_reextraction_invalidates_old_chunks_and_vectors(self):
        document_id, _ = await self.process("lecture.txt", b"Original content")
        old_chunk = self.db.query(DocumentChunk).one()
        old_chunk.embedding = [0.0] * 384
        self.db.commit()
        extracted = await self.client.post(f"/documents/{document_id}/extract", headers=self.headers())
        self.assertEqual(extracted.status_code, 200)
        self.assertEqual(self.db.query(DocumentChunk).count(), 0)
        chunked = await self.client.post(f"/documents/{document_id}/chunk", headers=self.headers())
        self.assertEqual(chunked.status_code, 200)
        self.assertIsNone(self.db.query(DocumentChunk).one().embedding)

    async def test_failed_reextraction_blocks_chunking_and_embedding(self):
        document_id, _ = await self.process("lecture.txt", b"Original content")
        document = self.db.query(Document).filter(Document.id == uuid.UUID(document_id)).one()
        (self.upload_dir / document.stored_filename).write_bytes(b"  \n ")
        with self.assertLogs("app.services.extraction_service", level="ERROR"):
            response = await self.client.post(f"/documents/{document_id}/extract", headers=self.headers())
        self.assertEqual(response.status_code, 422)
        self.assertEqual(document.processing_status, "failed")
        for operation in ["chunk", "embed"]:
            response = await self.client.post(f"/documents/{document_id}/{operation}", headers=self.headers())
            self.assertEqual(response.status_code, 400)

    async def test_empty_scanned_or_corrupt_files_fail_without_leaking_paths(self):
        blank_pdf = PdfWriter()
        blank_pdf.add_blank_page(width=100, height=100)
        data = io.BytesIO()
        blank_pdf.write(data)
        for filename, content in [("blank.pdf", data.getvalue()), ("corrupt.pdf", b"not pdf"), ("bad.txt", b"\xff\xfe\x80"), ("blank.txt", b" \n\n ")]:
            uploaded = await self.client.post("/documents/upload", headers=self.headers(), files={"file": (filename, content)})
            with self.assertLogs("app.services.extraction_service", level="ERROR"):
                extracted = await self.client.post(f"/documents/{uploaded.json()['id']}/extract", headers=self.headers())
            self.assertEqual(extracted.status_code, 422)
            self.assertNotIn(str(self.upload_dir), extracted.text)

    async def test_legacy_text_uses_unknown_location(self):
        self.document.extracted_text = "Old extraction with no provenance"
        self.document.processing_status = "extracted"
        self.db.commit()
        response = await self.client.post(f"/documents/{self.document.id}/chunk", headers=self.headers())
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()[0]["source_location"]["kind"], "unknown")

    async def test_chunk_read_permissions_and_pagination(self):
        document_id, _ = await self.process("lecture.pdf", pdf_bytes())
        response = await self.client.get(f"/documents/{document_id}/chunks", headers=self.headers(self.other))
        self.assertEqual(response.status_code, 404)
        response = await self.client.get(f"/documents/{document_id}/chunks?offset=1&limit=1", headers=self.headers())
        self.assertEqual(len(response.json()), 1)
        self.assertEqual(response.json()[0]["source_location"]["page"], 3)

    async def test_shared_chunk_locations_follow_enrollment_permissions(self):
        document_id, chunks = await self.process("lecture.pdf", pdf_bytes())
        course = Course(code="SOURCE101", title="Source fixture", instructor_id=self.owner.id)
        self.db.add(course)
        self.db.flush()
        document = self.db.get(Document, uuid.UUID(document_id))
        document.course_id = course.id
        document.visibility = "course"
        enrollment = Enrollment(course_id=course.id, user_id=self.other.id)
        self.db.add(enrollment)
        self.db.commit()
        response = await self.client.get(f"/documents/{document_id}/chunks", headers=self.headers(self.other))
        self.assertEqual(response.json(), chunks)
        self.db.delete(enrollment)
        self.db.commit()
        response = await self.client.get(f"/documents/{document_id}/chunks", headers=self.headers(self.other))
        self.assertEqual(response.status_code, 404)

    def test_chunks_do_not_cross_pages_or_split_normal_words(self):
        text = " ".join(f"word{i:04d}" for i in range(400))
        pieces = located_chunks([{"text": text, "location": {"kind": "pdf_page", "page": 7}}])
        self.assertGreater(len(pieces), 1)
        found = set()
        for piece in pieces:
            self.assertLessEqual(len(piece["content"]), 1000)
            self.assertEqual(piece["source_location"]["page"], 7)
            for word in piece["content"].split():
                self.assertIn(word, text.split())
                found.add(word)
        self.assertEqual(found, set(text.split()))

    def test_invalid_chunk_parameters_and_long_tokens_terminate(self):
        for size, overlap in [(0, 0), (10, 10), (10, -1)]:
            with self.assertRaises(ValueError):
                list(chunk_spans("content", size, overlap))
        self.assertEqual(split_into_chunks(""), [])
        self.assertEqual("".join(split_into_chunks("x" * 2500)), "x" * 2500)
