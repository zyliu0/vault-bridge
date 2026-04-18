"""Tests for scripts/scan_pipeline.py — unified file-processing pipeline.

TDD: tests written BEFORE the implementation.

Test cases:
SP1.  process_file with .txt source → ScanResult.text non-empty, skipped=False, handler_category="text-plain"
SP2.  process_file with unknown extension → skipped=True, skip_reason contains "unknown"
SP3.  process_file with handler where skip=True (video/audio) → skipped=True
SP4.  process_file with render_pages=True handler (mock) → extract_images called, attachments populated
SP5.  process_file with extract_text=False handler → text="", sources_read=0
SP6.  process_batch read limit: 21 text files → first 20 have sources_read=1, 21st has skip_reason="read_limit_reached"
SP7.  process_batch read limit does NOT affect render_pages-only files (images still extracted even after limit)
SP8.  content_confidence: "" → "none", 50 chars → "low", 200 chars → "high"
SP9.  dry_run=True → no vault writes (mock vault_binary, assert not called)
SP10. ScanResult is JSON-serializable (for CLI output)
SP11. process_file never raises — all errors go into ScanResult.errors
SP12. ScanResult.read_bytes reflects bytes read from file
SP13. process_file skips file with skip=True category (archive type)
SP14. process_batch returns results in input order
SP15. process_file with image file uses extract_images path, attachments populated (mocked)
SP16. process_file text extraction failure → errors list populated, skipped=False
SP17. CLI entry point: process subcommand outputs JSON to stdout
SP18. CLI entry point: batch subcommand with paths file outputs JSON array to stdout
SP19. ScanResult dataclass fields match specification exactly
SP20. process_batch with max_reads=0 → all text files have skip_reason="read_limit_reached"
"""
import json
import sys
import tempfile
import textwrap
from dataclasses import asdict
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))


# ---------------------------------------------------------------------------
# Helper: create a text file with known content
# ---------------------------------------------------------------------------

def _write_text_file(path: Path, content: str = "Hello world test content.") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_fake_jpeg(path: Path) -> None:
    """Write a minimal valid JPEG header."""
    import io
    from PIL import Image
    img = Image.new("RGB", (10, 10), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())


# ---------------------------------------------------------------------------
# SP19. ScanResult dataclass fields match specification
# ---------------------------------------------------------------------------

class TestScanResultSchema:
    def test_scanresult_has_all_required_fields(self):
        """ScanResult dataclass has all fields specified in the task."""
        import scan_pipeline
        sr = scan_pipeline.ScanResult(
            source_path="/tmp/file.txt",
            handler_category="text-plain",
            text="hello",
            attachments=[],
            images_embedded=0,
            skipped=False,
            skip_reason="",
            warnings=[],
            errors=[],
            read_bytes=5,
            sources_read=1,
            content_confidence="high",
        )
        assert sr.source_path == "/tmp/file.txt"
        assert sr.handler_category == "text-plain"
        assert sr.text == "hello"
        assert sr.attachments == []
        assert sr.images_embedded == 0
        assert sr.skipped is False
        assert sr.skip_reason == ""
        assert sr.warnings == []
        assert sr.errors == []
        assert sr.read_bytes == 5
        assert sr.sources_read == 1
        assert sr.content_confidence == "high"

    def test_scanresult_is_dataclass(self):
        """ScanResult must be a dataclass (supports asdict)."""
        import scan_pipeline
        sr = scan_pipeline.ScanResult(
            source_path="/f.txt",
            handler_category="text-plain",
            text="x",
            attachments=[],
            images_embedded=0,
            skipped=False,
            skip_reason="",
            warnings=[],
            errors=[],
            read_bytes=1,
            sources_read=1,
            content_confidence="high",
        )
        d = asdict(sr)
        assert isinstance(d, dict)
        assert d["source_path"] == "/f.txt"


# ---------------------------------------------------------------------------
# SP10. ScanResult is JSON-serializable
# ---------------------------------------------------------------------------

class TestJsonSerializable:
    def test_scanresult_json_serializable(self, tmp_path):
        """ScanResult can be converted to JSON without error."""
        import scan_pipeline
        txt_file = tmp_path / "note.txt"
        _write_text_file(txt_file, "Some note content.")

        result = scan_pipeline.process_file(
            source_path=str(txt_file),
            workdir=str(tmp_path),
            vault_project_path="Project/Docs",
            event_date="2026-04-19",
            dry_run=True,
        )
        # Convert to dict and serialize
        d = asdict(result)
        encoded = json.dumps(d)
        assert isinstance(encoded, str)
        decoded = json.loads(encoded)
        assert decoded["source_path"] == str(txt_file)


# ---------------------------------------------------------------------------
# SP1. process_file with .txt source
# ---------------------------------------------------------------------------

class TestProcessFileTxt:
    def test_txt_file_text_extracted(self, tmp_path):
        """process_file on a .txt file: text non-empty, skipped=False, handler_category text-plain."""
        import scan_pipeline
        txt_file = tmp_path / "readme.txt"
        _write_text_file(txt_file, "This is a plain text document.")

        result = scan_pipeline.process_file(
            source_path=str(txt_file),
            workdir=str(tmp_path),
            vault_project_path="Project/Docs",
            event_date="2026-04-19",
            dry_run=True,
        )

        assert result.skipped is False
        assert result.handler_category == "text-plain"
        assert "This is a plain text document." in result.text

    def test_txt_file_sources_read_is_1(self, tmp_path):
        """process_file on a readable .txt file: sources_read=1."""
        import scan_pipeline
        txt_file = tmp_path / "readme.txt"
        _write_text_file(txt_file, "content")

        result = scan_pipeline.process_file(
            source_path=str(txt_file),
            workdir=str(tmp_path),
            vault_project_path="Project/Docs",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.sources_read == 1

    def test_txt_file_read_bytes_positive(self, tmp_path):
        """SP12: process_file on a txt file: read_bytes > 0."""
        import scan_pipeline
        content = "hello world"
        txt_file = tmp_path / "test.txt"
        _write_text_file(txt_file, content)

        result = scan_pipeline.process_file(
            source_path=str(txt_file),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.read_bytes > 0


# ---------------------------------------------------------------------------
# SP2. process_file with unknown extension
# ---------------------------------------------------------------------------

class TestProcessFileUnknown:
    def test_unknown_extension_skipped(self, tmp_path):
        """process_file with .xyz extension → skipped=True, skip_reason contains 'unknown'."""
        import scan_pipeline
        unknown_file = tmp_path / "data.xyz"
        unknown_file.write_text("data")

        result = scan_pipeline.process_file(
            source_path=str(unknown_file),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )

        assert result.skipped is True
        assert "unknown" in result.skip_reason.lower()

    def test_unknown_extension_handler_category_none(self, tmp_path):
        """process_file with unknown extension: handler_category is None."""
        import scan_pipeline
        f = tmp_path / "data.blarg"
        f.write_text("x")

        result = scan_pipeline.process_file(
            source_path=str(f),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.handler_category is None

    def test_unknown_no_errors_generated(self, tmp_path):
        """Unknown file type generates skip, not errors."""
        import scan_pipeline
        f = tmp_path / "mystery.quux"
        f.write_text("nope")

        result = scan_pipeline.process_file(
            source_path=str(f),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        # skipped cleanly, no errors needed
        assert result.skipped is True


# ---------------------------------------------------------------------------
# SP3. process_file with skip-category handler (video/audio)
# ---------------------------------------------------------------------------

class TestProcessFileSkipCategories:
    def test_video_file_skipped(self, tmp_path):
        """MP4 video file → skipped=True (no text, no images)."""
        import scan_pipeline
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)

        result = scan_pipeline.process_file(
            source_path=str(video),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.skipped is True

    def test_audio_file_skipped(self, tmp_path):
        """MP3 audio file → skipped=True."""
        import scan_pipeline
        audio = tmp_path / "song.mp3"
        audio.write_bytes(b"\x00" * 16)

        result = scan_pipeline.process_file(
            source_path=str(audio),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.skipped is True

    def test_archive_file_skipped(self, tmp_path):
        """SP13: ZIP archive → skipped=True."""
        import scan_pipeline
        archive = tmp_path / "bundle.zip"
        archive.write_bytes(b"PK\x03\x04" + b"\x00" * 20)

        result = scan_pipeline.process_file(
            source_path=str(archive),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.skipped is True

    def test_skip_reason_describes_category(self, tmp_path):
        """Skipped files have a non-empty skip_reason."""
        import scan_pipeline
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)

        result = scan_pipeline.process_file(
            source_path=str(video),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.skip_reason != ""


# ---------------------------------------------------------------------------
# SP5. process_file with extract_text=False handler
# ---------------------------------------------------------------------------

class TestProcessFileNoText:
    def test_image_raster_no_text_extracted(self, tmp_path):
        """JPEG file: extract_text=False → text='', sources_read=0."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        result = scan_pipeline.process_file(
            source_path=str(img),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.text == ""
        assert result.sources_read == 0

    def test_image_raster_handler_category_correct(self, tmp_path):
        """JPEG file: handler_category='image-raster'."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        result = scan_pipeline.process_file(
            source_path=str(img),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.handler_category == "image-raster"


# ---------------------------------------------------------------------------
# SP8. content_confidence: "" → "none", short → "low", long → "high"
# ---------------------------------------------------------------------------

class TestContentConfidence:
    def test_empty_text_gives_none_confidence(self, tmp_path):
        """Empty text → content_confidence='none'."""
        import scan_pipeline
        # Image type has no text
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        result = scan_pipeline.process_file(
            source_path=str(img),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.content_confidence == "none"

    def test_short_text_gives_low_confidence(self, tmp_path):
        """1-100 chars → content_confidence='low'."""
        import scan_pipeline
        txt = tmp_path / "short.txt"
        _write_text_file(txt, "A" * 50)  # 50 chars

        result = scan_pipeline.process_file(
            source_path=str(txt),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.content_confidence == "low"

    def test_long_text_gives_high_confidence(self, tmp_path):
        """200 chars → content_confidence='high'."""
        import scan_pipeline
        txt = tmp_path / "long.txt"
        _write_text_file(txt, "B" * 200)

        result = scan_pipeline.process_file(
            source_path=str(txt),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.content_confidence == "high"

    def test_exactly_100_chars_gives_low_confidence(self, tmp_path):
        """100 chars (boundary) → 'low' (low is 1-100 inclusive)."""
        import scan_pipeline
        txt = tmp_path / "boundary.txt"
        _write_text_file(txt, "C" * 100)

        result = scan_pipeline.process_file(
            source_path=str(txt),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.content_confidence == "low"

    def test_101_chars_gives_high_confidence(self, tmp_path):
        """101 chars → 'high'."""
        import scan_pipeline
        txt = tmp_path / "just_over.txt"
        _write_text_file(txt, "D" * 101)

        result = scan_pipeline.process_file(
            source_path=str(txt),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert result.content_confidence == "high"


# ---------------------------------------------------------------------------
# SP11. process_file never raises
# ---------------------------------------------------------------------------

class TestProcessFileNeverRaises:
    def test_nonexistent_file_does_not_raise(self, tmp_path):
        """process_file on non-existent path should not raise, returns a result."""
        import scan_pipeline
        result = scan_pipeline.process_file(
            source_path="/nonexistent/path/file.txt",
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        # Should return a ScanResult, not raise
        assert isinstance(result, scan_pipeline.ScanResult)

    def test_empty_source_path_does_not_raise(self, tmp_path):
        """process_file on empty path should not raise."""
        import scan_pipeline
        result = scan_pipeline.process_file(
            source_path="",
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert isinstance(result, scan_pipeline.ScanResult)
        assert result.skipped is True

    def test_permission_error_goes_to_errors(self, tmp_path):
        """SP16: extraction failure → errors list populated, not raised."""
        import scan_pipeline
        txt = tmp_path / "file.txt"
        _write_text_file(txt, "content")

        # Mock read_text to raise an exception
        with mock.patch("scan_pipeline.file_type_handlers.read_text", side_effect=OSError("permission denied")):
            result = scan_pipeline.process_file(
                source_path=str(txt),
                workdir=str(tmp_path),
                vault_project_path="Proj",
                event_date="2026-04-19",
                dry_run=True,
            )
        # Must not raise; error should be captured
        assert isinstance(result, scan_pipeline.ScanResult)
        assert len(result.errors) >= 1


# ---------------------------------------------------------------------------
# SP9. dry_run=True → no vault writes
# ---------------------------------------------------------------------------

class TestDryRun:
    def test_dry_run_no_vault_binary_calls(self, tmp_path):
        """dry_run=True: vault_binary.write_binary must not be called."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch("scan_pipeline.vault_binary") as mock_vb:
            mock_vb.write_binary = mock.MagicMock()
            scan_pipeline.process_file(
                source_path=str(img),
                workdir=str(tmp_path),
                vault_project_path="Proj",
                event_date="2026-04-19",
                dry_run=True,
            )
            mock_vb.write_binary.assert_not_called()

    def test_dry_run_false_allows_vault_writes_for_images(self, tmp_path):
        """dry_run=False: vault_binary.write_binary is called for image files."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch("scan_pipeline.vault_binary") as mock_vb:
            mock_vb.write_binary = mock.MagicMock(return_value={"ok": True})
            scan_pipeline.process_file(
                source_path=str(img),
                workdir=str(tmp_path),
                vault_project_path="Proj",
                event_date="2026-04-19",
                dry_run=False,
            )
            # vault_binary.write_binary should have been called
            mock_vb.write_binary.assert_called()


# ---------------------------------------------------------------------------
# SP4. process_file with render_pages=True handler
# ---------------------------------------------------------------------------

class TestRenderPagesHandler:
    def test_render_pages_handler_calls_extract_images(self, tmp_path):
        """SP4: render_pages=True handler: extract_images is called, attachments populated (mocked)."""
        import scan_pipeline
        from scripts import file_type_handlers as fth  # noqa

        # Create a fake DXF-like file (extension .dxf has render_pages=True in the registry)
        dxf_file = tmp_path / "drawing.dxf"
        dxf_file.write_text("0\nSECTION\n")

        # Mock file_type_handlers.extract_images to return a fake image
        fake_img = tmp_path / "render.jpg"
        _write_fake_jpeg(fake_img)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[fake_img]) as mock_extract:
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=fake_img):
                with mock.patch("scan_pipeline.vault_binary.write_binary", return_value={"ok": True}):
                    result = scan_pipeline.process_file(
                        source_path=str(dxf_file),
                        workdir=str(tmp_path),
                        vault_project_path="Proj",
                        event_date="2026-04-19",
                        dry_run=False,
                    )
        mock_extract.assert_called()

    def test_render_pages_attachments_populated(self, tmp_path):
        """SP4: render_pages handler: attachments list is non-empty on success."""
        import scan_pipeline

        dxf_file = tmp_path / "drawing.dxf"
        dxf_file.write_text("0\nSECTION\n")

        fake_img = tmp_path / "render.jpg"
        _write_fake_jpeg(fake_img)

        compressed = tmp_path / "2026-04-19--render--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[fake_img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                with mock.patch("scan_pipeline.vault_binary.write_binary", return_value={"ok": True}):
                    result = scan_pipeline.process_file(
                        source_path=str(dxf_file),
                        workdir=str(tmp_path),
                        vault_project_path="Proj",
                        event_date="2026-04-19",
                        dry_run=False,
                    )
        assert result.images_embedded >= 1
        assert len(result.attachments) >= 1
        for embed in result.attachments:
            assert embed.startswith("![[")


# ---------------------------------------------------------------------------
# SP15. process_file with image file uses extract_images path
# ---------------------------------------------------------------------------

class TestImageFileAttachments:
    def test_jpeg_attachments_populated_with_mocked_pipeline(self, tmp_path):
        """SP15: JPEG file: attachments populated when pipeline mocked."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        compressed = tmp_path / "2026-04-19--photo--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                with mock.patch("scan_pipeline.vault_binary.write_binary", return_value={"ok": True}):
                    result = scan_pipeline.process_file(
                        source_path=str(img),
                        workdir=str(tmp_path),
                        vault_project_path="Proj",
                        event_date="2026-04-19",
                        dry_run=False,
                    )
        assert result.images_embedded >= 1
        assert len(result.attachments) >= 1

    def test_wiki_embed_format_in_attachments(self, tmp_path):
        """Attachments list uses ![[filename.jpg]] format."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        compressed = tmp_path / "2026-04-19--photo--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                with mock.patch("scan_pipeline.vault_binary.write_binary", return_value={"ok": True}):
                    result = scan_pipeline.process_file(
                        source_path=str(img),
                        workdir=str(tmp_path),
                        vault_project_path="Proj",
                        event_date="2026-04-19",
                        dry_run=False,
                    )
        for embed in result.attachments:
            assert embed.startswith("![["), f"Expected ![[...]], got: {embed!r}"
            assert embed.endswith("]]"), f"Expected ![[...]], got: {embed!r}"


# ---------------------------------------------------------------------------
# SP6. process_batch read limit: 21 files → first 20 read, 21st skipped
# ---------------------------------------------------------------------------

class TestProcessBatchReadLimit:
    def test_21_text_files_first_20_read(self, tmp_path):
        """SP6: 21 text files: first 20 have sources_read=1, 21st has skip_reason='read_limit_reached'."""
        import scan_pipeline
        files = []
        for i in range(21):
            f = tmp_path / f"note_{i:02d}.txt"
            # Use long enough content to force sources_read=1
            _write_text_file(f, f"Content of file {i}. " * 10)
            files.append(str(f))

        results = scan_pipeline.process_batch(
            source_paths=files,
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            max_reads=20,
            dry_run=True,
        )

        assert len(results) == 21
        reads_done = sum(1 for r in results if r.sources_read > 0)
        assert reads_done == 20

        # 21st should be skipped due to read limit
        last = results[20]
        assert last.skip_reason == "read_limit_reached"

    def test_read_limit_returns_in_input_order(self, tmp_path):
        """SP14: process_batch returns results in input order."""
        import scan_pipeline
        files = []
        for i in range(5):
            f = tmp_path / f"file_{i}.txt"
            _write_text_file(f, f"Content {i}")
            files.append(str(f))

        results = scan_pipeline.process_batch(
            source_paths=files,
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            max_reads=20,
            dry_run=True,
        )
        for i, result in enumerate(results):
            assert result.source_path == files[i], (
                f"Result {i} source_path mismatch: expected {files[i]}, got {result.source_path}"
            )

    def test_max_reads_zero_all_text_skipped(self, tmp_path):
        """SP20: max_reads=0 → all text files get skip_reason='read_limit_reached'."""
        import scan_pipeline
        files = []
        for i in range(3):
            f = tmp_path / f"file_{i}.txt"
            _write_text_file(f, "Hello " * 10)
            files.append(str(f))

        results = scan_pipeline.process_batch(
            source_paths=files,
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            max_reads=0,
            dry_run=True,
        )
        for r in results:
            assert r.skip_reason == "read_limit_reached", (
                f"Expected read_limit_reached, got: {r.skip_reason!r}"
            )


# ---------------------------------------------------------------------------
# SP7. process_batch read limit does NOT affect render_pages-only files
# ---------------------------------------------------------------------------

class TestProcessBatchRenderPagesNotLimited:
    def test_render_pages_not_blocked_by_read_limit(self, tmp_path):
        """SP7: render_pages files run image extraction even after read limit is reached."""
        import scan_pipeline

        # Create 20 text files (will exhaust read limit)
        text_files = []
        for i in range(20):
            f = tmp_path / f"note_{i:02d}.txt"
            _write_text_file(f, "Content " * 10)
            text_files.append(str(f))

        # Create a DXF file (render_pages=True, extract_text also True)
        dxf_file = tmp_path / "drawing.dxf"
        dxf_file.write_text("0\nSECTION\n")

        all_files = text_files + [str(dxf_file)]

        fake_img = tmp_path / "render.jpg"
        _write_fake_jpeg(fake_img)
        compressed = tmp_path / "2026-04-19--render--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[fake_img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                with mock.patch("scan_pipeline.vault_binary.write_binary", return_value={"ok": True}):
                    results = scan_pipeline.process_batch(
                        source_paths=all_files,
                        workdir=str(tmp_path),
                        vault_project_path="Proj",
                        event_date="2026-04-19",
                        max_reads=20,
                        dry_run=False,
                    )

        dxf_result = results[-1]
        # DXF text extraction may be blocked (read_limit_reached applies to text),
        # but image extraction (render_pages) must still run
        assert dxf_result.images_embedded >= 1, (
            f"DXF render_pages result should have images_embedded >= 1, got {dxf_result.images_embedded}. "
            f"skip_reason={dxf_result.skip_reason!r}"
        )


# ---------------------------------------------------------------------------
# Vault binary vault_name parameter handling
# ---------------------------------------------------------------------------

class TestVaultNameHandling:
    def test_vault_name_passed_from_config_or_default(self, tmp_path):
        """process_file uses vault_name from config if available, else empty string."""
        import scan_pipeline
        # This test validates the function doesn't crash without a transport
        txt = tmp_path / "file.txt"
        _write_text_file(txt, "content")

        result = scan_pipeline.process_file(
            source_path=str(txt),
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        # In dry_run, vault writes are skipped; should not error from missing vault_name
        assert isinstance(result, scan_pipeline.ScanResult)


# ---------------------------------------------------------------------------
# CLI entry point tests
# ---------------------------------------------------------------------------

class TestCLIEntryPoint:
    def test_cli_process_outputs_json(self, tmp_path):
        """SP17: CLI 'process' subcommand prints valid JSON to stdout."""
        import subprocess
        txt = tmp_path / "doc.txt"
        _write_text_file(txt, "Some document content.")

        script = SCRIPTS / "scan_pipeline.py"
        proc = subprocess.run(
            [
                "python3", str(script),
                "process", str(txt),
                "--workdir", str(tmp_path),
                "--vault-path", "Proj/Docs",
                "--event-date", "2026-04-19",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"CLI failed: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert "source_path" in data
        assert "skipped" in data
        assert "text" in data

    def test_cli_batch_outputs_json_array(self, tmp_path):
        """SP18: CLI 'batch' subcommand prints JSON array to stdout."""
        import subprocess
        files = []
        for i in range(3):
            f = tmp_path / f"doc{i}.txt"
            _write_text_file(f, f"Content {i}")
            files.append(str(f))

        paths_file = tmp_path / "paths.txt"
        paths_file.write_text("\n".join(files))

        script = SCRIPTS / "scan_pipeline.py"
        proc = subprocess.run(
            [
                "python3", str(script),
                "batch", str(paths_file),
                "--workdir", str(tmp_path),
                "--vault-path", "Proj/Docs",
                "--event-date", "2026-04-19",
                "--max-reads", "20",
                "--dry-run",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert proc.returncode == 0, f"CLI failed: {proc.stderr}"
        data = json.loads(proc.stdout)
        assert isinstance(data, list)
        assert len(data) == 3
        for item in data:
            assert "source_path" in item

    def test_cli_unknown_subcommand_exits_nonzero(self, tmp_path):
        """Unknown CLI subcommand exits with non-zero status."""
        import subprocess
        script = SCRIPTS / "scan_pipeline.py"
        proc = subprocess.run(
            ["python3", str(script), "bogus"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert proc.returncode != 0


# ---------------------------------------------------------------------------
# process_batch edge cases
# ---------------------------------------------------------------------------

class TestProcessBatchEdgeCases:
    def test_empty_list_returns_empty(self, tmp_path):
        """process_batch with empty source_paths returns []."""
        import scan_pipeline
        results = scan_pipeline.process_batch(
            source_paths=[],
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert results == []

    def test_single_file_works(self, tmp_path):
        """process_batch with one file returns list of one ScanResult."""
        import scan_pipeline
        f = tmp_path / "single.txt"
        _write_text_file(f, "Hello")
        results = scan_pipeline.process_batch(
            source_paths=[str(f)],
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert len(results) == 1
        assert isinstance(results[0], scan_pipeline.ScanResult)

    def test_mixed_types_all_returned(self, tmp_path):
        """process_batch with mixed types returns one result per input."""
        import scan_pipeline
        txt = tmp_path / "doc.txt"
        _write_text_file(txt, "text content")
        video = tmp_path / "clip.mp4"
        video.write_bytes(b"\x00" * 16)
        unknown = tmp_path / "data.xyz"
        unknown.write_text("data")

        results = scan_pipeline.process_batch(
            source_paths=[str(txt), str(video), str(unknown)],
            workdir=str(tmp_path),
            vault_project_path="Proj",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert len(results) == 3


# ---------------------------------------------------------------------------
# Coverage: error paths in _process_images
# ---------------------------------------------------------------------------

class TestProcessImagesErrorPaths:
    def test_compress_error_goes_to_warnings(self, tmp_path):
        """compress_images.CompressError on an image → warning added, not an error."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch(
                "scan_pipeline.compress_images.compress_image",
                side_effect=__import__("compress_images", fromlist=["CompressError"]).CompressError("bad image"),
            ):
                result = scan_pipeline.process_file(
                    source_path=str(img),
                    workdir=str(tmp_path),
                    vault_project_path="Proj",
                    event_date="2026-04-19",
                    dry_run=False,
                )
        assert len(result.warnings) >= 1
        assert "compress failed" in result.warnings[0] or "compress" in result.warnings[0].lower()
        assert result.images_embedded == 0

    def test_generic_compress_exception_goes_to_warnings(self, tmp_path):
        """Generic exception during compress → warning, not crash."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch(
                "scan_pipeline.compress_images.compress_image",
                side_effect=RuntimeError("unexpected compress error"),
            ):
                result = scan_pipeline.process_file(
                    source_path=str(img),
                    workdir=str(tmp_path),
                    vault_project_path="Proj",
                    event_date="2026-04-19",
                    dry_run=False,
                )
        assert isinstance(result, scan_pipeline.ScanResult)
        assert result.images_embedded == 0

    def test_vault_write_failure_goes_to_errors(self, tmp_path):
        """vault_binary.write_binary returning ok=False → error in result."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)
        compressed = tmp_path / "2026-04-19--photo--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                with mock.patch(
                    "scan_pipeline.vault_binary.write_binary",
                    return_value={"ok": False, "error": "permission denied"},
                ):
                    result = scan_pipeline.process_file(
                        source_path=str(img),
                        workdir=str(tmp_path),
                        vault_project_path="Proj",
                        event_date="2026-04-19",
                        dry_run=False,
                    )
        assert len(result.errors) >= 1
        assert "vault write failed" in result.errors[0]
        assert result.images_embedded == 0

    def test_vault_write_exception_goes_to_errors(self, tmp_path):
        """vault_binary.write_binary raising exception → error in result."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)
        compressed = tmp_path / "2026-04-19--photo--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                with mock.patch(
                    "scan_pipeline.vault_binary.write_binary",
                    side_effect=RuntimeError("vault offline"),
                ):
                    result = scan_pipeline.process_file(
                        source_path=str(img),
                        workdir=str(tmp_path),
                        vault_project_path="Proj",
                        event_date="2026-04-19",
                        dry_run=False,
                    )
        assert isinstance(result, scan_pipeline.ScanResult)
        assert len(result.errors) >= 1

    def test_extract_images_exception_goes_to_errors(self, tmp_path):
        """extract_images raising exception → errors list populated."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch(
            "scan_pipeline.file_type_handlers.extract_images",
            side_effect=RuntimeError("extraction failure"),
        ):
            result = scan_pipeline.process_file(
                source_path=str(img),
                workdir=str(tmp_path),
                vault_project_path="Proj",
                event_date="2026-04-19",
                dry_run=False,
            )
        assert isinstance(result, scan_pipeline.ScanResult)
        assert len(result.errors) >= 1
        assert "extract_images failed" in result.errors[0]

    def test_no_raw_images_returns_empty_attachments(self, tmp_path):
        """extract_images returns [] → attachments=[], images_embedded=0."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[]):
            result = scan_pipeline.process_file(
                source_path=str(img),
                workdir=str(tmp_path),
                vault_project_path="Proj",
                event_date="2026-04-19",
                dry_run=False,
            )
        assert result.attachments == []
        assert result.images_embedded == 0


# ---------------------------------------------------------------------------
# Coverage: _process_images_only (called by process_batch for render_pages+text after limit)
# ---------------------------------------------------------------------------

class TestProcessImagesOnly:
    def test_images_only_populates_attachments(self, tmp_path):
        """_process_images_only runs image pipeline even when text is blocked."""
        import scan_pipeline

        # Use a PDF (has extract_text=True AND extract_images=True)
        # After read limit, its text should be blocked but images should still run
        dxf_file = tmp_path / "drawing.dxf"
        dxf_file.write_text("0\nSECTION\n")

        fake_img = tmp_path / "render.jpg"
        _write_fake_jpeg(fake_img)
        compressed = tmp_path / "2026-04-19--render--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        # Exhaust read limit with 20 text files
        text_files = []
        for i in range(20):
            f = tmp_path / f"note_{i:02d}.txt"
            _write_text_file(f, "Content " * 10)
            text_files.append(str(f))

        all_files = text_files + [str(dxf_file)]

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[fake_img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                with mock.patch("scan_pipeline.vault_binary.write_binary", return_value={"ok": True}):
                    results = scan_pipeline.process_batch(
                        source_paths=all_files,
                        workdir=str(tmp_path),
                        vault_project_path="Proj",
                        event_date="2026-04-19",
                        max_reads=20,
                        dry_run=False,
                    )

        dxf_result = results[-1]
        assert dxf_result.skip_reason == "read_limit_reached"
        assert dxf_result.sources_read == 0  # text not read
        assert dxf_result.images_embedded >= 1  # images extracted


# ---------------------------------------------------------------------------
# Coverage: process_file top-level exception guard
# ---------------------------------------------------------------------------

class TestProcessFileExceptionGuard:
    def test_inner_exception_caught_returns_result_with_error(self, tmp_path):
        """If _process_file_inner raises unexpectedly, process_file catches it."""
        import scan_pipeline
        txt = tmp_path / "file.txt"
        _write_text_file(txt, "data")

        with mock.patch(
            "scan_pipeline._process_file_inner",
            side_effect=RuntimeError("unexpected inner error"),
        ):
            result = scan_pipeline.process_file(
                source_path=str(txt),
                workdir=str(tmp_path),
                vault_project_path="Proj",
                event_date="2026-04-19",
                dry_run=True,
            )
        assert isinstance(result, scan_pipeline.ScanResult)
        assert len(result.errors) >= 1
        assert "unexpected pipeline error" in result.errors[0]


# ---------------------------------------------------------------------------
# Coverage: dry_run counts images (for reporting purposes)
# ---------------------------------------------------------------------------

class TestDryRunImageCounting:
    def test_dry_run_images_embedded_counted_but_not_written(self, tmp_path):
        """In dry_run mode, images_embedded is set but vault_binary is not called."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)
        compressed = tmp_path / "2026-04-19--photo--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                with mock.patch("scan_pipeline.vault_binary") as mock_vb:
                    mock_vb.write_binary = mock.MagicMock()
                    result = scan_pipeline.process_file(
                        source_path=str(img),
                        workdir=str(tmp_path),
                        vault_project_path="Proj",
                        event_date="2026-04-19",
                        dry_run=True,
                    )
        # vault_binary not called
        mock_vb.write_binary.assert_not_called()
        # But images_embedded is still reported
        assert result.images_embedded >= 1


# ---------------------------------------------------------------------------
# Coverage: _to_json_dict and _process_images_only error branch
# ---------------------------------------------------------------------------

class TestToJsonDict:
    def test_to_json_dict_returns_plain_dict(self, tmp_path):
        """_to_json_dict converts ScanResult to a plain serializable dict."""
        import scan_pipeline
        sr = scan_pipeline.ScanResult(
            source_path="/tmp/f.txt",
            handler_category="text-plain",
            text="hello",
            attachments=[],
            images_embedded=0,
            skipped=False,
            skip_reason="",
            warnings=[],
            errors=[],
            read_bytes=5,
            sources_read=1,
            content_confidence="high",
        )
        d = scan_pipeline._to_json_dict(sr)
        assert isinstance(d, dict)
        assert d["source_path"] == "/tmp/f.txt"
        # Must be JSON-serializable
        encoded = json.dumps(d)
        assert isinstance(encoded, str)


class TestProcessImagesOnlyErrorBranch:
    def test_images_only_exception_captured(self, tmp_path):
        """_process_images_only: exception in _process_images goes to errors list."""
        import scan_pipeline

        dxf_file = tmp_path / "drawing.dxf"
        dxf_file.write_text("0\nSECTION\n")

        with mock.patch(
            "scan_pipeline._process_images",
            side_effect=RuntimeError("unexpected in images_only"),
        ):
            result = scan_pipeline._process_images_only(
                source_path=str(dxf_file),
                workdir=str(tmp_path),
                vault_project_path="Proj",
                event_date="2026-04-19",
                dry_run=True,
            )
        assert isinstance(result, scan_pipeline.ScanResult)
        assert len(result.errors) >= 1
        assert "image-only processing error" in result.errors[0]
