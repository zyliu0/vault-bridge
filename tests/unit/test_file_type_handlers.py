"""Tests for scripts/file_type_handlers.py — per-extension handler registry.

TDD: tests written BEFORE the implementation.

--- HandlerConfig / HANDLERS registry ---
H1.  HANDLERS is a dict mapping str → HandlerConfig
H2.  .pdf extension maps to category 'document-pdf'
H3.  .docx extension maps to category 'document-office'
H4.  .pptx extension maps to category 'document-office'
H5.  .jpg extension maps to category 'image-raster'
H6.  .png extension maps to category 'image-raster'
H7.  .svg extension maps to category 'image-vector'
H8.  .mp4 extension maps to category 'video'
H9.  .mp3 extension maps to category 'audio'
H10. .txt extension maps to category 'text-plain'
H11. .md extension maps to category 'text-plain'
H12. .zip extension maps to category 'archive'

--- HandlerConfig fields ---
HC1. HandlerConfig has: category, extract_text (bool), extract_images (bool),
         compress (bool), run_vision (bool)
HC2. document-pdf: extract_text=True, extract_images=True, compress=True, run_vision=True
HC3. document-office: extract_text=True, extract_images=True, compress=True, run_vision=False
HC4. image-raster: extract_text=False, extract_images=True, compress=True, run_vision=True
HC5. image-vector: extract_text=False, extract_images=True, compress=False, run_vision=False
HC6. video: extract_text=False, extract_images=False, compress=False, run_vision=False
HC7. audio: extract_text=False, extract_images=False, compress=False, run_vision=False
HC8. text-plain: extract_text=True, extract_images=False, compress=False, run_vision=False
HC9. archive: extract_text=False, extract_images=False, compress=False, run_vision=False

--- get_handler ---
GH1. get_handler('path/to/file.pdf') returns HandlerConfig for document-pdf
GH2. get_handler('FILE.PDF') is case-insensitive (returns document-pdf)
GH3. get_handler('photo.JPG') returns image-raster
GH4. get_handler('report.DOCX') returns document-office
GH5. get_handler('/abs/path/note.md') returns text-plain
GH6. get_handler('unknown.xyz') returns None
GH7. get_handler('') returns None
GH8. get_handler('noextension') returns None
GH9. get_handler('.hidden') returns None (dot-files with no real ext)
GH10. get_handler('archive.tar.gz') → uses last extension .gz → None (not in registry)
GH11. get_handler('data.xlsx') returns document-office

--- HandlerResult dataclass ---
HR1. HandlerResult has: text (str|None), images (list[Path]), skipped (bool), category (str)
HR2. HandlerResult default: text=None, images=[], skipped=False, category=''
HR3. HandlerResult is a dataclass (not a dict) — field access by attribute

--- read_text function ---
RT1. read_text(path_to_txt) returns file contents as str
RT2. read_text(nonexistent_file) returns '' (never raises)
RT3. read_text on pdf with readable text returns non-empty str (mocked PyPDF2)
RT4. read_text on pdf with no pages returns ''
RT5. read_text on docx returns paragraph text joined by newlines
RT6. read_text on pptx returns slide text
RT7. read_text on image file (.jpg) returns '' (images have no text)
RT8. read_text on video (.mp4) returns ''
RT9. read_text on audio (.mp3) returns ''
RT10. read_text on archive (.zip) returns ''
RT11. read_text on corrupt PDF returns '' (never raises)
RT12. read_text on .md file returns raw markdown contents

--- extract_images function ---
EI1. extract_images on .jpg returns [Path(path)] (passthrough)
EI2. extract_images on .png returns [Path(path)] (passthrough)
EI3. extract_images on pdf with 0 pages returns [] (mocked)
EI4. extract_images on docx with embedded images uses extract_embedded_images.extract
EI5. extract_images on .mp4 returns []
EI6. extract_images on .txt returns []
EI7. extract_images on nonexistent file returns []

--- handle function ---
HF1. handle('file.txt') → HandlerResult(text='...', images=[], skipped=False, category='text-plain')
HF2. handle('file.jpg') → HandlerResult(text='', images=[...], skipped=False, category='image-raster')
HF3. handle('file.mp4') → HandlerResult(text='', images=[], skipped=False, category='video')
HF4. handle('unknown.xyz') → HandlerResult(text=None, images=[], skipped=True, category='unknown')
HF5. handle sets skipped=True for unknown extensions
HF6. handle('') → HandlerResult skipped=True
HF7. handle with extract_text=False category → text is '' not None

--- Edge cases ---
EC1. Extensions with dots: '.tar.gz' last ext = '.gz' — not in registry → None
EC2. HANDLERS covers all 8 categories (no duplicates within category)
EC3. Every registered extension maps to a valid category name (one of the 8)
EC4. HandlerConfig is immutable (frozen dataclass)
EC5. All extensions in HANDLERS are lowercase and start with no dot
"""
import io
import sys
from dataclasses import fields as dc_fields
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import file_type_handlers as fth  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_CATEGORIES = {
    "document-pdf",
    "document-office",
    "image-raster",
    "image-vector",
    "video",
    "audio",
    "text-plain",
    "archive",
}


def _make_txt(tmp_path: Path, content: str = "hello world") -> Path:
    p = tmp_path / "note.txt"
    p.write_text(content, encoding="utf-8")
    return p


def _make_md(tmp_path: Path, content: str = "# Title\nBody text.") -> Path:
    p = tmp_path / "note.md"
    p.write_text(content, encoding="utf-8")
    return p


def _make_jpg(tmp_path: Path) -> Path:
    from PIL import Image
    img = Image.new("RGB", (10, 10), color=(255, 0, 0))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    p = tmp_path / "photo.jpg"
    p.write_bytes(buf.getvalue())
    return p


# ---------------------------------------------------------------------------
# H — HANDLERS registry
# ---------------------------------------------------------------------------

class TestHandlersRegistry:
    def test_handlers_is_dict(self):
        """H1. HANDLERS is a dict."""
        assert isinstance(fth.HANDLERS, dict)

    def test_pdf_category(self):
        """H2. .pdf → document-pdf."""
        assert fth.HANDLERS["pdf"].category == "document-pdf"

    def test_docx_category(self):
        """H3. .docx → document-office."""
        assert fth.HANDLERS["docx"].category == "document-office"

    def test_pptx_category(self):
        """H4. .pptx → document-office."""
        assert fth.HANDLERS["pptx"].category == "document-office"

    def test_jpg_category(self):
        """H5. .jpg → image-raster."""
        assert fth.HANDLERS["jpg"].category == "image-raster"

    def test_png_category(self):
        """H6. .png → image-raster."""
        assert fth.HANDLERS["png"].category == "image-raster"

    def test_svg_category(self):
        """H7. .svg → image-vector."""
        assert fth.HANDLERS["svg"].category == "image-vector"

    def test_mp4_category(self):
        """H8. .mp4 → video."""
        assert fth.HANDLERS["mp4"].category == "video"

    def test_mp3_category(self):
        """H9. .mp3 → audio."""
        assert fth.HANDLERS["mp3"].category == "audio"

    def test_txt_category(self):
        """H10. .txt → text-plain."""
        assert fth.HANDLERS["txt"].category == "text-plain"

    def test_md_category(self):
        """H11. .md → text-plain."""
        assert fth.HANDLERS["md"].category == "text-plain"

    def test_zip_category(self):
        """H12. .zip → archive."""
        assert fth.HANDLERS["zip"].category == "archive"


# ---------------------------------------------------------------------------
# HC — HandlerConfig fields per category
# ---------------------------------------------------------------------------

class TestHandlerConfigFields:
    def test_handler_config_has_required_fields(self):
        """HC1. HandlerConfig has category, extract_text, extract_images, compress, run_vision."""
        cfg = fth.HANDLERS["pdf"]
        assert hasattr(cfg, "category")
        assert hasattr(cfg, "extract_text")
        assert hasattr(cfg, "extract_images")
        assert hasattr(cfg, "compress")
        assert hasattr(cfg, "run_vision")

    def test_document_pdf_flags(self):
        """HC2. document-pdf: extract_text=T, extract_images=T, compress=T, run_vision=T."""
        cfg = fth.HANDLERS["pdf"]
        assert cfg.extract_text is True
        assert cfg.extract_images is True
        assert cfg.compress is True
        assert cfg.run_vision is True

    def test_document_office_flags(self):
        """HC3. document-office: extract_text=T, extract_images=T, compress=T, run_vision=F."""
        cfg = fth.HANDLERS["docx"]
        assert cfg.extract_text is True
        assert cfg.extract_images is True
        assert cfg.compress is True
        assert cfg.run_vision is False

    def test_image_raster_flags(self):
        """HC4. image-raster: extract_text=F, extract_images=T, compress=T, run_vision=T."""
        cfg = fth.HANDLERS["jpg"]
        assert cfg.extract_text is False
        assert cfg.extract_images is True
        assert cfg.compress is True
        assert cfg.run_vision is True

    def test_image_vector_flags(self):
        """HC5. image-vector: extract_text=F, extract_images=T, compress=F, run_vision=F."""
        cfg = fth.HANDLERS["svg"]
        assert cfg.extract_text is False
        assert cfg.extract_images is True
        assert cfg.compress is False
        assert cfg.run_vision is False

    def test_video_flags(self):
        """HC6. video: all False."""
        cfg = fth.HANDLERS["mp4"]
        assert cfg.extract_text is False
        assert cfg.extract_images is False
        assert cfg.compress is False
        assert cfg.run_vision is False

    def test_audio_flags(self):
        """HC7. audio: all False."""
        cfg = fth.HANDLERS["mp3"]
        assert cfg.extract_text is False
        assert cfg.extract_images is False
        assert cfg.compress is False
        assert cfg.run_vision is False

    def test_text_plain_flags(self):
        """HC8. text-plain: extract_text=T, rest False."""
        cfg = fth.HANDLERS["txt"]
        assert cfg.extract_text is True
        assert cfg.extract_images is False
        assert cfg.compress is False
        assert cfg.run_vision is False

    def test_archive_flags(self):
        """HC9. archive: all False."""
        cfg = fth.HANDLERS["zip"]
        assert cfg.extract_text is False
        assert cfg.extract_images is False
        assert cfg.compress is False
        assert cfg.run_vision is False


# ---------------------------------------------------------------------------
# GH — get_handler
# ---------------------------------------------------------------------------

class TestGetHandler:
    def test_get_handler_pdf_lowercase(self):
        """GH1. get_handler('file.pdf') → HandlerConfig for document-pdf."""
        result = fth.get_handler("path/to/file.pdf")
        assert result is not None
        assert result.category == "document-pdf"

    def test_get_handler_case_insensitive_pdf(self):
        """GH2. get_handler('FILE.PDF') → document-pdf (case insensitive)."""
        result = fth.get_handler("FILE.PDF")
        assert result is not None
        assert result.category == "document-pdf"

    def test_get_handler_case_insensitive_jpg(self):
        """GH3. get_handler('photo.JPG') → image-raster."""
        result = fth.get_handler("photo.JPG")
        assert result is not None
        assert result.category == "image-raster"

    def test_get_handler_case_insensitive_docx(self):
        """GH4. get_handler('report.DOCX') → document-office."""
        result = fth.get_handler("report.DOCX")
        assert result is not None
        assert result.category == "document-office"

    def test_get_handler_absolute_path_md(self):
        """GH5. get_handler('/abs/path/note.md') → text-plain."""
        result = fth.get_handler("/abs/path/note.md")
        assert result is not None
        assert result.category == "text-plain"

    def test_get_handler_unknown_returns_none(self):
        """GH6. get_handler('unknown.xyz') → None."""
        assert fth.get_handler("unknown.xyz") is None

    def test_get_handler_empty_string_returns_none(self):
        """GH7. get_handler('') → None."""
        assert fth.get_handler("") is None

    def test_get_handler_no_extension_returns_none(self):
        """GH8. get_handler('noextension') → None."""
        assert fth.get_handler("noextension") is None

    def test_get_handler_hidden_file_returns_none(self):
        """GH9. get_handler('.hidden') → None (dot-file, no real ext)."""
        assert fth.get_handler(".hidden") is None

    def test_get_handler_double_extension_uses_last(self):
        """GH10. get_handler('archive.tar.gz') last ext = 'gz' → None (not in registry)."""
        # gz is not in the registry; tar.gz → gz → not registered
        assert fth.get_handler("archive.tar.gz") is None

    def test_get_handler_xlsx(self):
        """GH11. get_handler('data.xlsx') → document-office."""
        result = fth.get_handler("data.xlsx")
        assert result is not None
        assert result.category == "document-office"


# ---------------------------------------------------------------------------
# HR — HandlerResult dataclass
# ---------------------------------------------------------------------------

class TestHandlerResult:
    def test_handler_result_has_required_fields(self):
        """HR1. HandlerResult has text, images, skipped, category."""
        result = fth.HandlerResult(text=None, images=[], skipped=False, category="")
        assert hasattr(result, "text")
        assert hasattr(result, "images")
        assert hasattr(result, "skipped")
        assert hasattr(result, "category")

    def test_handler_result_defaults(self):
        """HR2. HandlerResult default values."""
        result = fth.HandlerResult()
        assert result.text is None
        assert result.images == []
        assert result.skipped is False
        assert result.category == ""

    def test_handler_result_is_dataclass(self):
        """HR3. HandlerResult is a dataclass — attribute access works."""
        result = fth.HandlerResult(text="hello", images=[Path("/tmp/a.jpg")], skipped=True, category="image-raster")
        assert result.text == "hello"
        assert result.images == [Path("/tmp/a.jpg")]
        assert result.skipped is True
        assert result.category == "image-raster"
        # Must be a proper dataclass (has __dataclass_fields__)
        assert hasattr(fth.HandlerResult, "__dataclass_fields__")


# ---------------------------------------------------------------------------
# RT — read_text
# ---------------------------------------------------------------------------

class TestReadText:
    def test_read_text_txt_file(self, tmp_path):
        """RT1. read_text on .txt file returns its contents."""
        p = _make_txt(tmp_path, "hello world")
        result = fth.read_text(str(p))
        assert result == "hello world"

    def test_read_text_nonexistent_returns_empty(self, tmp_path):
        """RT2. read_text on nonexistent file returns '' (never raises)."""
        result = fth.read_text(str(tmp_path / "ghost.txt"))
        assert result == ""

    def test_read_text_pdf_mocked(self, tmp_path):
        """RT3. read_text on PDF uses PyPDF2; returns extracted text."""
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-fake")

        fake_page = mock.MagicMock()
        fake_page.extract_text.return_value = "extracted text from pdf"
        fake_reader = mock.MagicMock()
        fake_reader.pages = [fake_page]

        with mock.patch("file_type_handlers._pdf_read_text", return_value="extracted text from pdf"):
            result = fth.read_text(str(p))
        assert result == "extracted text from pdf"

    def test_read_text_pdf_no_pages_returns_empty(self, tmp_path):
        """RT4. read_text on PDF with no pages returns ''."""
        p = tmp_path / "empty.pdf"
        p.write_bytes(b"%PDF-fake")

        with mock.patch("file_type_handlers._pdf_read_text", return_value=""):
            result = fth.read_text(str(p))
        assert result == ""

    def test_read_text_docx_mocked(self, tmp_path):
        """RT5. read_text on .docx returns paragraph text joined by newlines."""
        p = tmp_path / "report.docx"
        p.write_bytes(b"PK\x03\x04fake-docx")

        with mock.patch("file_type_handlers._docx_read_text", return_value="para1\npara2"):
            result = fth.read_text(str(p))
        assert result == "para1\npara2"

    def test_read_text_pptx_mocked(self, tmp_path):
        """RT6. read_text on .pptx returns slide text."""
        p = tmp_path / "slides.pptx"
        p.write_bytes(b"PK\x03\x04fake-pptx")

        with mock.patch("file_type_handlers._pptx_read_text", return_value="Slide 1 text"):
            result = fth.read_text(str(p))
        assert result == "Slide 1 text"

    def test_read_text_jpg_returns_empty(self, tmp_path):
        """RT7. read_text on .jpg returns '' (images have no text)."""
        p = _make_jpg(tmp_path)
        result = fth.read_text(str(p))
        assert result == ""

    def test_read_text_mp4_returns_empty(self, tmp_path):
        """RT8. read_text on .mp4 returns ''."""
        p = tmp_path / "video.mp4"
        p.write_bytes(b"\x00\x01video-bytes")
        result = fth.read_text(str(p))
        assert result == ""

    def test_read_text_mp3_returns_empty(self, tmp_path):
        """RT9. read_text on .mp3 returns ''."""
        p = tmp_path / "audio.mp3"
        p.write_bytes(b"\x00\x01audio-bytes")
        result = fth.read_text(str(p))
        assert result == ""

    def test_read_text_zip_returns_empty(self, tmp_path):
        """RT10. read_text on .zip returns ''."""
        p = tmp_path / "bundle.zip"
        p.write_bytes(b"PK\x05\x06")
        result = fth.read_text(str(p))
        assert result == ""

    def test_read_text_corrupt_pdf_returns_empty(self, tmp_path):
        """RT11. read_text on corrupt PDF returns '' (never raises)."""
        p = tmp_path / "corrupt.pdf"
        p.write_bytes(b"NOT A PDF AT ALL \xff\xfe")
        result = fth.read_text(str(p))
        assert result == ""
        assert isinstance(result, str)

    def test_read_text_md_returns_raw_markdown(self, tmp_path):
        """RT12. read_text on .md returns raw markdown contents."""
        content = "# Title\n\nBody text here.\n"
        p = _make_md(tmp_path, content)
        result = fth.read_text(str(p))
        assert result == content


# ---------------------------------------------------------------------------
# EI — extract_images
# ---------------------------------------------------------------------------

class TestExtractImages:
    def test_extract_images_jpg_passthrough(self, tmp_path):
        """EI1. extract_images on .jpg returns [Path(path)] (passthrough)."""
        p = _make_jpg(tmp_path)
        result = fth.extract_images(str(p))
        assert result == [p]

    def test_extract_images_png_passthrough(self, tmp_path):
        """EI2. extract_images on .png returns [Path(path)] (passthrough)."""
        from PIL import Image
        img = Image.new("RGB", (4, 4), color=(0, 255, 0))
        buf = io.BytesIO()
        img.save(buf, "PNG")
        p = tmp_path / "img.png"
        p.write_bytes(buf.getvalue())
        result = fth.extract_images(str(p))
        assert result == [p]

    def test_extract_images_pdf_delegates_to_extract_embedded_images(self, tmp_path):
        """EI3. extract_images on .pdf delegates to extract_embedded_images.extract."""
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-1.4 fake")
        extracted = [tmp_path / "page1.jpg"]
        with mock.patch("file_type_handlers._delegate_extract_images", return_value=extracted) as m:
            result = fth.extract_images(str(p))
        m.assert_called_once()
        assert result == extracted

    def test_extract_images_docx_delegates(self, tmp_path):
        """EI4. extract_images on .docx delegates to extract_embedded_images.extract."""
        p = tmp_path / "report.docx"
        p.write_bytes(b"PK\x03\x04fake")
        extracted = [tmp_path / "img1.png"]
        with mock.patch("file_type_handlers._delegate_extract_images", return_value=extracted) as m:
            result = fth.extract_images(str(p))
        m.assert_called_once()
        assert result == extracted

    def test_extract_images_mp4_returns_empty(self, tmp_path):
        """EI5. extract_images on .mp4 returns []."""
        p = tmp_path / "vid.mp4"
        p.write_bytes(b"\x00video")
        result = fth.extract_images(str(p))
        assert result == []

    def test_extract_images_txt_returns_empty(self, tmp_path):
        """EI6. extract_images on .txt returns []."""
        p = _make_txt(tmp_path)
        result = fth.extract_images(str(p))
        assert result == []

    def test_extract_images_nonexistent_returns_empty(self, tmp_path):
        """EI7. extract_images on nonexistent file returns []."""
        result = fth.extract_images(str(tmp_path / "ghost.jpg"))
        assert result == []


# ---------------------------------------------------------------------------
# HF — handle
# ---------------------------------------------------------------------------

class TestHandle:
    def test_handle_txt_file(self, tmp_path):
        """HF1. handle('file.txt') → HandlerResult with text, no images, not skipped."""
        p = _make_txt(tmp_path, "some content")
        result = fth.handle(str(p))
        assert isinstance(result, fth.HandlerResult)
        assert result.text == "some content"
        assert result.images == []
        assert result.skipped is False
        assert result.category == "text-plain"

    def test_handle_jpg_file(self, tmp_path):
        """HF2. handle('file.jpg') → HandlerResult with images, empty text."""
        p = _make_jpg(tmp_path)
        result = fth.handle(str(p))
        assert result.images == [p]
        assert result.text == ""
        assert result.skipped is False
        assert result.category == "image-raster"

    def test_handle_mp4_file(self, tmp_path):
        """HF3. handle('file.mp4') → HandlerResult all empty, not skipped."""
        p = tmp_path / "clip.mp4"
        p.write_bytes(b"\x00video")
        result = fth.handle(str(p))
        assert result.text == ""
        assert result.images == []
        assert result.skipped is False
        assert result.category == "video"

    def test_handle_unknown_extension(self, tmp_path):
        """HF4. handle('file.xyz') → HandlerResult skipped=True, category='unknown'."""
        p = tmp_path / "data.xyz"
        p.write_bytes(b"bytes")
        result = fth.handle(str(p))
        assert result.skipped is True
        assert result.category == "unknown"
        assert result.text is None

    def test_handle_unknown_sets_skipped_true(self, tmp_path):
        """HF5. handle sets skipped=True for unknown extensions."""
        p = tmp_path / "data.abc"
        p.write_bytes(b"bytes")
        result = fth.handle(str(p))
        assert result.skipped is True

    def test_handle_empty_string(self):
        """HF6. handle('') → HandlerResult skipped=True."""
        result = fth.handle("")
        assert result.skipped is True

    def test_handle_no_text_category_returns_empty_string_not_none(self, tmp_path):
        """HF7. For categories with extract_text=False, text is '' not None."""
        p = tmp_path / "clip.mp4"
        p.write_bytes(b"\x00video")
        result = fth.handle(str(p))
        # mp4 is video — extract_text=False — text should be '' not None
        assert result.text == ""
        assert result.text is not None


# ---------------------------------------------------------------------------
# PH — Private helper implementations (coverage for actual extraction code)
# ---------------------------------------------------------------------------

class TestPrivateHelpers:
    def test_pdf_read_text_returns_extracted_text(self, tmp_path):
        """PH1. _pdf_read_text calls into PyPDF2 and joins page text."""
        fake_page = mock.MagicMock()
        fake_page.extract_text.return_value = "page content here"
        fake_reader = mock.MagicMock()
        fake_reader.pages = [fake_page]

        p = tmp_path / "real.pdf"
        p.write_bytes(b"%PDF-1.4 fake")

        with mock.patch("PyPDF2.PdfReader", return_value=fake_reader):
            result = fth._pdf_read_text(str(p))
        assert result == "page content here"

    def test_pdf_read_text_skips_pages_with_no_text(self, tmp_path):
        """PH2. _pdf_read_text skips pages where extract_text returns None."""
        p1 = mock.MagicMock()
        p1.extract_text.return_value = None
        p2 = mock.MagicMock()
        p2.extract_text.return_value = "second page"
        fake_reader = mock.MagicMock()
        fake_reader.pages = [p1, p2]

        p = tmp_path / "real.pdf"
        p.write_bytes(b"%PDF-fake")

        with mock.patch("PyPDF2.PdfReader", return_value=fake_reader):
            result = fth._pdf_read_text(str(p))
        assert result == "second page"

    def test_pdf_read_text_returns_empty_on_exception(self, tmp_path):
        """PH3. _pdf_read_text returns '' when PyPDF2 raises."""
        p = tmp_path / "broken.pdf"
        p.write_bytes(b"not a pdf")
        with mock.patch("PyPDF2.PdfReader", side_effect=RuntimeError("parse error")):
            result = fth._pdf_read_text(str(p))
        assert result == ""

    def test_pdf_read_text_page_extract_raises_returns_partial(self, tmp_path):
        """PH4. _pdf_read_text swallows per-page errors and keeps good pages."""
        good_page = mock.MagicMock()
        good_page.extract_text.return_value = "good text"
        bad_page = mock.MagicMock()
        bad_page.extract_text.side_effect = RuntimeError("page error")
        fake_reader = mock.MagicMock()
        fake_reader.pages = [bad_page, good_page]

        p = tmp_path / "partial.pdf"
        p.write_bytes(b"%PDF-fake")

        with mock.patch("PyPDF2.PdfReader", return_value=fake_reader):
            result = fth._pdf_read_text(str(p))
        assert result == "good text"

    def test_docx_read_text_joins_paragraphs(self, tmp_path):
        """PH5. _docx_read_text joins non-empty paragraphs with newlines."""
        para1 = mock.MagicMock()
        para1.text = "First paragraph"
        para2 = mock.MagicMock()
        para2.text = ""  # empty — should be skipped
        para3 = mock.MagicMock()
        para3.text = "Third paragraph"
        fake_doc = mock.MagicMock()
        fake_doc.paragraphs = [para1, para2, para3]

        p = tmp_path / "doc.docx"
        p.write_bytes(b"PK\x03\x04fake")

        with mock.patch("docx.Document", return_value=fake_doc):
            result = fth._docx_read_text(str(p))
        assert result == "First paragraph\nThird paragraph"

    def test_docx_read_text_returns_empty_on_exception(self, tmp_path):
        """PH6. _docx_read_text returns '' when python-docx raises."""
        p = tmp_path / "bad.docx"
        p.write_bytes(b"not docx")
        with mock.patch("docx.Document", side_effect=Exception("corrupt")):
            result = fth._docx_read_text(str(p))
        assert result == ""

    def test_pptx_read_text_joins_shapes(self, tmp_path):
        """PH7. _pptx_read_text joins shape text across slides."""
        shape1 = mock.MagicMock()
        shape1.text = "Title slide"
        shape2 = mock.MagicMock()
        shape2.text = ""  # empty — skip
        shape3 = mock.MagicMock()
        shape3.text = "Content slide"
        slide1 = mock.MagicMock()
        slide1.shapes = [shape1, shape2]
        slide2 = mock.MagicMock()
        slide2.shapes = [shape3]
        fake_prs = mock.MagicMock()
        fake_prs.slides = [slide1, slide2]

        p = tmp_path / "deck.pptx"
        p.write_bytes(b"PK\x03\x04fake")

        with mock.patch("pptx.Presentation", return_value=fake_prs):
            result = fth._pptx_read_text(str(p))
        assert result == "Title slide\nContent slide"

    def test_pptx_read_text_returns_empty_on_exception(self, tmp_path):
        """PH8. _pptx_read_text returns '' when python-pptx raises."""
        p = tmp_path / "bad.pptx"
        p.write_bytes(b"not pptx")
        with mock.patch("pptx.Presentation", side_effect=Exception("corrupt")):
            result = fth._pptx_read_text(str(p))
        assert result == ""

    def test_plain_read_text_reads_file(self, tmp_path):
        """PH9. _plain_read_text reads file content directly."""
        p = tmp_path / "note.txt"
        p.write_text("plain text content", encoding="utf-8")
        result = fth._plain_read_text(str(p))
        assert result == "plain text content"

    def test_plain_read_text_returns_empty_on_missing(self, tmp_path):
        """PH10. _plain_read_text returns '' for nonexistent file."""
        result = fth._plain_read_text(str(tmp_path / "ghost.txt"))
        assert result == ""

    def test_read_text_xlsx_returns_empty(self, tmp_path):
        """PH11. read_text on .xlsx returns '' (no text extraction for spreadsheets)."""
        p = tmp_path / "data.xlsx"
        p.write_bytes(b"PK\x03\x04fake-xlsx")
        result = fth.read_text(str(p))
        assert result == ""

    def test_read_text_pptx_calls_pptx_helper(self, tmp_path):
        """PH12. read_text on existing .pptx calls _pptx_read_text."""
        p = tmp_path / "slides.pptx"
        p.write_bytes(b"PK\x03\x04fake")
        with mock.patch("file_type_handlers._pptx_read_text", return_value="slide text") as m:
            result = fth.read_text(str(p))
        m.assert_called_once_with(str(p))
        assert result == "slide text"

    def test_read_text_docx_calls_docx_helper(self, tmp_path):
        """PH13. read_text on existing .docx calls _docx_read_text."""
        p = tmp_path / "report.docx"
        p.write_bytes(b"PK\x03\x04fake")
        with mock.patch("file_type_handlers._docx_read_text", return_value="doc text") as m:
            result = fth.read_text(str(p))
        m.assert_called_once_with(str(p))
        assert result == "doc text"

    def test_delegate_extract_images_uses_extract_embedded_images(self, tmp_path):
        """PH14. _delegate_extract_images calls extract_embedded_images.extract."""
        p = tmp_path / "doc.pdf"
        p.write_bytes(b"%PDF-fake")
        extracted = [tmp_path / "img.jpg"]

        fake_module = mock.MagicMock()
        fake_module.extract.return_value = extracted

        with mock.patch.dict("sys.modules", {"extract_embedded_images": fake_module}):
            result = fth._delegate_extract_images(p, "pdf")
        assert result == extracted
        fake_module.extract.assert_called_once()

    def test_delegate_extract_images_returns_empty_on_exception(self, tmp_path):
        """PH15. _delegate_extract_images returns [] when extract_embedded_images raises."""
        p = tmp_path / "bad.pdf"
        p.write_bytes(b"%PDF-fake")

        fake_module = mock.MagicMock()
        fake_module.extract.side_effect = RuntimeError("extraction failed")

        with mock.patch.dict("sys.modules", {"extract_embedded_images": fake_module}):
            result = fth._delegate_extract_images(p, "pdf")
        assert result == []

    def test_handle_exception_in_read_text_returns_empty(self, tmp_path):
        """PH16. handle() catches exceptions from read_text and returns empty strings."""
        p = _make_txt(tmp_path, "content")
        with mock.patch("file_type_handlers.read_text", side_effect=RuntimeError("fail")):
            result = fth.handle(str(p))
        # skipped=False (known extension), but text='' due to error
        assert result.skipped is False
        assert result.text == ""
        assert result.images == []


# ---------------------------------------------------------------------------
# EC — Edge cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_double_extension_tar_gz_not_in_registry(self):
        """EC1. 'archive.tar.gz' → last ext 'gz' not in registry → None."""
        assert fth.get_handler("archive.tar.gz") is None

    def test_all_eight_categories_present(self):
        """EC2. HANDLERS covers all original 8 categories (plus new visual/CAD ones)."""
        actual_categories = {cfg.category for cfg in fth.HANDLERS.values()}
        # All original 8 categories must still be present
        for cat in _VALID_CATEGORIES:
            assert cat in actual_categories, f"Original category '{cat}' missing from HANDLERS"

    def test_every_extension_maps_to_valid_category(self):
        """EC3. Every registered extension maps to a known category."""
        for ext, cfg in fth.HANDLERS.items():
            assert cfg.category in _EXTENDED_VALID_CATEGORIES, (
                f"Extension '{ext}' maps to unknown category '{cfg.category}'"
            )

    def test_handler_config_is_frozen_dataclass(self):
        """EC4. HandlerConfig is immutable (frozen dataclass)."""
        cfg = fth.HANDLERS["pdf"]
        with pytest.raises((AttributeError, TypeError)):
            cfg.category = "mutated"  # type: ignore[misc]

    def test_all_handlers_keys_are_lowercase_no_dot(self):
        """EC5. All extensions in HANDLERS are lowercase and have no leading dot."""
        for ext in fth.HANDLERS:
            assert ext == ext.lower(), f"Key '{ext}' is not lowercase"
            assert not ext.startswith("."), f"Key '{ext}' starts with a dot"


# ---------------------------------------------------------------------------
# New Visual/CAD extensions in HandlerConfig
# ---------------------------------------------------------------------------

# Extended valid categories including new CAD/visual ones
_EXTENDED_VALID_CATEGORIES = {
    "document-pdf",
    "document-office",
    "document-office-legacy",
    "spreadsheet-legacy",
    "image-raster",
    "image-vector",
    "video",
    "audio",
    "text-plain",
    "archive",
    "cad-dxf",
    "cad-dwg",
    "vector-ai",
    "raster-psd",
    "cad-3dm",
}


class TestVisualCadHandlers:
    """Tests for new Visual/CAD handler entries.

    VCH1.  HandlerConfig has render_pages field (bool, default False)
    VCH2.  get_handler("x.dxf").render_pages is True
    VCH3.  get_handler("x.dwg").render_pages is True
    VCH4.  get_handler("x.ai").render_pages is True
    VCH5.  get_handler("x.psd").render_pages is True
    VCH6.  get_handler("x.3dm").render_pages is False
    VCH7.  get_handler("x.doc") maps to a legacy category (not document-office)
    VCH8.  get_handler("x.ppt") maps to a legacy category (not document-office)
    VCH9.  get_handler("x.xls") maps to spreadsheet-legacy
    VCH10. get_handler("x.ai").category == "vector-ai"
    VCH11. get_handler("x.psd") is not None
    VCH12. get_handler("x.3dm") is not None
    VCH13. get_handler("x.dxf").category == "cad-dxf"
    VCH14. get_handler("x.dwg").category == "cad-dwg"
    VCH15. _CAD_DXF constant exists with render_pages=True
    VCH16. _CAD_DWG constant exists with render_pages=True
    VCH17. _VECTOR_AI constant exists with render_pages=True
    VCH18. _RASTER_PSD constant exists with render_pages=True
    VCH19. _OFFICE_LEGACY constant exists with render_pages=False
    VCH20. _SPREADSHEET_LEGACY constant exists with render_pages=False
    VCH21. _CAD_3DM constant exists with render_pages=False
    VCH22. All HANDLERS keys are still lowercase (invariant holds)
    VCH23. HandlerConfig is still frozen/immutable after adding render_pages
    VCH24. Existing handlers (pdf, docx, jpg, etc.) retain render_pages=False
           (backward compat)
    """

    def test_vch1_handler_config_has_render_pages(self):
        """VCH1. HandlerConfig has render_pages field."""
        cfg = fth.HANDLERS["pdf"]
        assert hasattr(cfg, "render_pages"), "HandlerConfig missing 'render_pages' field"
        assert isinstance(cfg.render_pages, bool)

    def test_vch2_dxf_render_pages_true(self):
        """VCH2. get_handler('x.dxf').render_pages is True."""
        cfg = fth.get_handler("drawing.dxf")
        assert cfg is not None, "get_handler('drawing.dxf') returned None"
        assert cfg.render_pages is True

    def test_vch3_dwg_render_pages_true(self):
        """VCH3. get_handler('x.dwg').render_pages is True."""
        cfg = fth.get_handler("plan.dwg")
        assert cfg is not None, "get_handler('plan.dwg') returned None"
        assert cfg.render_pages is True

    def test_vch4_ai_render_pages_true(self):
        """VCH4. get_handler('x.ai').render_pages is True."""
        cfg = fth.get_handler("logo.ai")
        assert cfg is not None, "get_handler('logo.ai') returned None"
        assert cfg.render_pages is True

    def test_vch5_psd_render_pages_true(self):
        """VCH5. get_handler('x.psd').render_pages is True."""
        cfg = fth.get_handler("design.psd")
        assert cfg is not None, "get_handler('design.psd') returned None"
        assert cfg.render_pages is True

    def test_vch6_3dm_render_pages_false(self):
        """VCH6. get_handler('x.3dm').render_pages is False."""
        cfg = fth.get_handler("model.3dm")
        assert cfg is not None, "get_handler('model.3dm') returned None"
        assert cfg.render_pages is False

    def test_vch7_doc_maps_to_legacy_category(self):
        """VCH7. get_handler('x.doc') maps to document-office-legacy."""
        cfg = fth.get_handler("letter.doc")
        assert cfg is not None, "get_handler('letter.doc') returned None"
        assert cfg.category == "document-office-legacy", (
            f"Expected 'document-office-legacy', got '{cfg.category}'"
        )

    def test_vch8_ppt_maps_to_legacy_category(self):
        """VCH8. get_handler('x.ppt') maps to document-office-legacy."""
        cfg = fth.get_handler("slides.ppt")
        assert cfg is not None, "get_handler('slides.ppt') returned None"
        assert cfg.category == "document-office-legacy", (
            f"Expected 'document-office-legacy', got '{cfg.category}'"
        )

    def test_vch9_xls_maps_to_spreadsheet_legacy(self):
        """VCH9. get_handler('x.xls') maps to spreadsheet-legacy."""
        cfg = fth.get_handler("data.xls")
        assert cfg is not None, "get_handler('data.xls') returned None"
        assert cfg.category == "spreadsheet-legacy", (
            f"Expected 'spreadsheet-legacy', got '{cfg.category}'"
        )

    def test_vch10_ai_category_vector_ai(self):
        """VCH10. get_handler('x.ai').category == 'vector-ai'."""
        cfg = fth.get_handler("logo.ai")
        assert cfg is not None
        assert cfg.category == "vector-ai"

    def test_vch11_psd_handler_not_none(self):
        """VCH11. get_handler('x.psd') is not None."""
        cfg = fth.get_handler("design.psd")
        assert cfg is not None

    def test_vch12_3dm_handler_not_none(self):
        """VCH12. get_handler('x.3dm') is not None."""
        cfg = fth.get_handler("model.3dm")
        assert cfg is not None

    def test_vch13_dxf_category(self):
        """VCH13. get_handler('x.dxf').category == 'cad-dxf'."""
        cfg = fth.get_handler("floor_plan.dxf")
        assert cfg is not None
        assert cfg.category == "cad-dxf"

    def test_vch14_dwg_category(self):
        """VCH14. get_handler('x.dwg').category == 'cad-dwg'."""
        cfg = fth.get_handler("model.dwg")
        assert cfg is not None
        assert cfg.category == "cad-dwg"

    def test_vch15_cad_dxf_constant_exists(self):
        """VCH15. _CAD_DXF constant exists with render_pages=True."""
        assert hasattr(fth, "_CAD_DXF"), "_CAD_DXF constant not found"
        assert fth._CAD_DXF.render_pages is True

    def test_vch16_cad_dwg_constant_exists(self):
        """VCH16. _CAD_DWG constant exists with render_pages=True."""
        assert hasattr(fth, "_CAD_DWG"), "_CAD_DWG constant not found"
        assert fth._CAD_DWG.render_pages is True

    def test_vch17_vector_ai_constant_exists(self):
        """VCH17. _VECTOR_AI constant exists with render_pages=True."""
        assert hasattr(fth, "_VECTOR_AI"), "_VECTOR_AI constant not found"
        assert fth._VECTOR_AI.render_pages is True

    def test_vch18_raster_psd_constant_exists(self):
        """VCH18. _RASTER_PSD constant exists with render_pages=True."""
        assert hasattr(fth, "_RASTER_PSD"), "_RASTER_PSD constant not found"
        assert fth._RASTER_PSD.render_pages is True

    def test_vch19_office_legacy_constant_exists(self):
        """VCH19. _OFFICE_LEGACY constant exists with render_pages=False."""
        assert hasattr(fth, "_OFFICE_LEGACY"), "_OFFICE_LEGACY constant not found"
        assert fth._OFFICE_LEGACY.render_pages is False

    def test_vch20_spreadsheet_legacy_constant_exists(self):
        """VCH20. _SPREADSHEET_LEGACY constant exists with render_pages=False."""
        assert hasattr(fth, "_SPREADSHEET_LEGACY"), "_SPREADSHEET_LEGACY constant not found"
        assert fth._SPREADSHEET_LEGACY.render_pages is False

    def test_vch21_cad_3dm_constant_exists(self):
        """VCH21. _CAD_3DM constant exists with render_pages=False."""
        assert hasattr(fth, "_CAD_3DM"), "_CAD_3DM constant not found"
        assert fth._CAD_3DM.render_pages is False

    def test_vch22_all_handlers_keys_lowercase_after_extensions(self):
        """VCH22. All HANDLERS keys are still lowercase (invariant holds after extensions)."""
        for ext in fth.HANDLERS:
            assert ext == ext.lower()
            assert not ext.startswith(".")

    def test_vch23_handler_config_still_frozen_after_render_pages(self):
        """VCH23. HandlerConfig is still frozen/immutable with render_pages field."""
        cfg = fth.HANDLERS["dxf"]
        with pytest.raises((AttributeError, TypeError)):
            cfg.render_pages = False  # type: ignore[misc]

    def test_vch24_existing_handlers_have_render_pages_false(self):
        """VCH24. Existing handlers retain render_pages=False (backward compat)."""
        for ext in ("pdf", "docx", "pptx", "xlsx", "jpg", "png", "svg", "mp4", "mp3", "txt", "zip"):
            cfg = fth.HANDLERS.get(ext)
            if cfg is not None:
                assert cfg.render_pages is False, (
                    f"Extension '{ext}' unexpectedly has render_pages=True"
                )

    def test_vch_dxf_extract_text_true(self):
        """DXF handler has extract_text=True."""
        cfg = fth.get_handler("plan.dxf")
        assert cfg is not None
        assert cfg.extract_text is True

    def test_vch_dxf_extract_images_true(self):
        """DXF handler has extract_images=True."""
        cfg = fth.get_handler("plan.dxf")
        assert cfg is not None
        assert cfg.extract_images is True

    def test_vch_3dm_extract_images_false(self):
        """3DM handler has extract_images=False (no rendering)."""
        cfg = fth.get_handler("model.3dm")
        assert cfg is not None
        assert cfg.extract_images is False

    def test_vch_psd_extract_text_true(self):
        """PSD handler has extract_text=True (text layers)."""
        cfg = fth.get_handler("design.psd")
        assert cfg is not None
        assert cfg.extract_text is True

    def test_vch_psd_extract_images_true(self):
        """PSD handler has extract_images=True (composite)."""
        cfg = fth.get_handler("design.psd")
        assert cfg is not None
        assert cfg.extract_images is True
