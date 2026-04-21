"""Unit tests for individual scan_pipeline stages (v14.7 refactor).

Before v14.7, `_process_file_inner` was a 120-line monolith. Testing
one step (e.g. "what happens when the skip-on-no-content gate fires
but warnings are already populated?") required mocking every upstream
call. Now each stage is a small function taking a `_ScanContext`, so
stages are testable in isolation with plain dataclass inputs.
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import scan_pipeline as sp  # noqa: E402
import file_type_handlers  # noqa: E402


def _ctx(**overrides):
    """Build a _ScanContext with sane defaults for testing."""
    defaults = dict(
        source_path="/tmp/x.pdf",
        workdir="/tmp/wd",
        vault_project_path="dom/proj/sub",
        event_date="2024-08-01",
        vault_name="V",
        throughput_bps=None,
        skip_on_no_content=True,
        dry_run=True,
        att_index=None,
        strict_handlers=False,
    )
    defaults.update(overrides)
    return sp._ScanContext(**defaults)


# ---------------------------------------------------------------------------
# _stage_handler_lookup
# ---------------------------------------------------------------------------

class TestStageHandlerLookup:
    def test_unknown_extension_sets_done_with_unknown_reason(self, tmp_path):
        ctx = _ctx(source_path=str(tmp_path / "mystery.xyzzy"))
        sp._stage_handler_lookup(ctx)
        assert ctx.done is True
        assert ctx.skip_reason == "unknown file type"
        assert ctx.handler is None

    def test_video_category_sets_skip_with_category(self, tmp_path):
        ctx = _ctx(source_path=str(tmp_path / "clip.mp4"))
        sp._stage_handler_lookup(ctx)
        assert ctx.done is True
        assert ctx.skip_category == "video"
        assert "no extractable content" in ctx.skip_reason

    def test_audio_category_sets_skip(self, tmp_path):
        ctx = _ctx(source_path=str(tmp_path / "track.mp3"))
        sp._stage_handler_lookup(ctx)
        assert ctx.done is True
        assert ctx.skip_category == "audio"

    def test_readable_pdf_leaves_pipeline_running(self, tmp_path):
        ctx = _ctx(source_path=str(tmp_path / "x.pdf"))
        sp._stage_handler_lookup(ctx)
        assert ctx.done is False
        assert ctx.handler is not None
        assert ctx.handler.category == "document-pdf"


# ---------------------------------------------------------------------------
# _stage_extract_text
# ---------------------------------------------------------------------------

class TestStageExtractText:
    def test_handler_with_no_text_is_no_op(self, tmp_path):
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8")
        ctx = _ctx(source_path=str(img))
        ctx.handler = file_type_handlers.get_handler(str(img))
        sp._stage_extract_text(ctx)
        assert ctx.text == ""
        assert ctx.sources_read == 0
        assert ctx.errors == []

    def test_successful_text_populates_read_bytes(self, tmp_path):
        md = tmp_path / "doc.md"
        md.write_text("Hello world " * 20, encoding="utf-8")
        ctx = _ctx(source_path=str(md))
        ctx.handler = file_type_handlers.get_handler(str(md))
        sp._stage_extract_text(ctx)
        assert "Hello world" in ctx.text
        assert ctx.read_bytes > 0
        assert ctx.sources_read == 1

    def test_exception_records_error_but_does_not_propagate(self, tmp_path):
        md = tmp_path / "doc.md"
        md.write_text("x", encoding="utf-8")
        ctx = _ctx(source_path=str(md))
        ctx.handler = file_type_handlers.get_handler(str(md))
        with mock.patch(
            "scan_pipeline.file_type_handlers.read_text",
            side_effect=RuntimeError("boom"),
        ):
            sp._stage_extract_text(ctx)
        assert ctx.text == ""
        assert any("boom" in e for e in ctx.errors)

    def test_slow_read_warning_emitted_when_throughput_says_so(self, tmp_path):
        md = tmp_path / "big.md"
        md.write_text("x" * 200, encoding="utf-8")
        ctx = _ctx(
            source_path=str(md),
            throughput_bps=1,  # 1 byte/sec → 200 seconds read, > 30s warn threshold
        )
        ctx.handler = file_type_handlers.get_handler(str(md))
        sp._stage_extract_text(ctx)
        assert any("large file" in w for w in ctx.warnings)


# ---------------------------------------------------------------------------
# _stage_skip_on_no_content
# ---------------------------------------------------------------------------

class TestStageSkipOnNoContent:
    def test_sets_done_when_both_text_and_images_empty(self):
        ctx = _ctx(skip_on_no_content=True)
        ctx.handler = file_type_handlers.get_handler("/tmp/x.pdf")
        ctx.text = ""
        ctx.images_embedded = 0
        sp._stage_skip_on_no_content(ctx)
        assert ctx.done is True
        assert ctx.skip_reason == "no_content"
        assert ctx.skip_category == "document-pdf"

    def test_does_not_fire_when_text_present(self):
        ctx = _ctx()
        ctx.handler = file_type_handlers.get_handler("/tmp/x.pdf")
        ctx.text = "real content"
        sp._stage_skip_on_no_content(ctx)
        assert ctx.done is False

    def test_does_not_fire_when_images_present(self):
        ctx = _ctx()
        ctx.handler = file_type_handlers.get_handler("/tmp/x.pdf")
        ctx.images_embedded = 3
        sp._stage_skip_on_no_content(ctx)
        assert ctx.done is False

    def test_preserves_warnings_and_errors_on_skip(self):
        ctx = _ctx()
        ctx.handler = file_type_handlers.get_handler("/tmp/x.pdf")
        ctx.warnings = ["size gate: tiny.jpg"]
        ctx.errors = ["extract failed"]
        sp._stage_skip_on_no_content(ctx)
        result = sp._build_result(ctx)
        # _make_skipped surfaces warnings/errors into the result so the
        # memory report can see why the file was skipped.
        assert "size gate: tiny.jpg" in result.warnings
        assert "extract failed" in result.errors

    def test_disabled_by_skip_on_no_content_false(self):
        ctx = _ctx(skip_on_no_content=False)
        ctx.handler = file_type_handlers.get_handler("/tmp/x.pdf")
        sp._stage_skip_on_no_content(ctx)
        assert ctx.done is False


# ---------------------------------------------------------------------------
# _build_result
# ---------------------------------------------------------------------------

class TestBuildResult:
    def test_sets_content_confidence_from_text_length(self):
        ctx = _ctx()
        ctx.handler = file_type_handlers.get_handler("/tmp/x.pdf")
        ctx.text = "x" * 200
        ctx.read_bytes = 200
        ctx.sources_read = 1
        result = sp._build_result(ctx)
        assert result.content_confidence == "high"

    def test_done_context_yields_skipped_result(self):
        ctx = _ctx()
        ctx.done = True
        ctx.skip_reason = "no_content"
        ctx.skip_category = "document-pdf"
        result = sp._build_result(ctx)
        assert result.skipped is True
        assert result.skip_reason == "no_content"
        assert result.handler_category == "document-pdf"


# ---------------------------------------------------------------------------
# Pipeline composition
# ---------------------------------------------------------------------------

class TestPipelineOrder:
    def test_pipeline_is_public_list_of_callables(self):
        """Stages are exposed so downstream code and tests can introspect."""
        assert isinstance(sp._PIPELINE, list)
        assert all(callable(s) for s in sp._PIPELINE)

    def test_handler_lookup_is_first(self):
        assert sp._PIPELINE[0] is sp._stage_handler_lookup

    def test_skip_on_no_content_is_last(self):
        assert sp._PIPELINE[-1] is sp._stage_skip_on_no_content
