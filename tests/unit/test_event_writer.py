"""Tests for scripts/event_writer.py — v16.0.0 slim shim.

Pre-v16 this module had ~400 lines of template rendering, metadata-stub
skeletons, word-count bounds, and verbatim-paste detection. All of that
was deleted in v16.0.0. What remains:

- `extract_abstract_callout` — MOC hint extractor, falls back to first
  prose sentence.
- `assemble_note_body` — theme-plumbing for the Minimal image grid.
- `compose_body` — minimal shim that returns a ComposedBody with a
  prompt telling the LLM to write directly from the evidence. No
  validator constraints, no stub rendering; unreadable files map to
  `note_kind="skip"` and the caller skips writing.
- `validate_event_note_body` — non-empty body check + opt-in
  `STOP_WORDS` list (empty by default). No verbatim-paste detection,
  no word-count bounds.
"""
import pytest

from scripts import event_writer


class _FakeResult:
    """Minimal stand-in for ScanResult — only the fields event_writer reads."""

    def __init__(
        self,
        text="",
        attachments=None,
        images_embedded=0,
        image_captions=None,
        image_candidate_paths=None,
        skipped=False,
        skip_reason="",
        handler_category="document-pdf",
    ):
        self.text = text
        self.attachments = attachments or []
        self.images_embedded = images_embedded
        self.image_captions = image_captions or []
        self.image_candidate_paths = image_candidate_paths or []
        self.skipped = skipped
        self.skip_reason = skip_reason
        self.handler_category = handler_category


def _meta(
    source_path="/nas/arch/2408 Sample/SD/kickoff.pdf",
    event_date="2024-08-01",
    domain="arch-projects",
    project="2408 Sample",
    subfolder="SD",
    file_type="pdf",
):
    return {
        "source_path": source_path,
        "event_date": event_date,
        "domain": domain,
        "project": project,
        "subfolder": subfolder,
        "file_type": file_type,
    }


class TestClassification:
    """v16: two note_kinds — 'event' (write a note) and 'skip' (don't write)."""

    def test_skipped_file_routes_to_skip(self):
        """Unreadable files produce note_kind='skip' — the caller does
        not write a stub note. Pre-v16 this was 'stub' with a fixed
        template; stubs were noise."""
        r = _FakeResult(skipped=True, skip_reason="no_content")
        assert event_writer.compose_body(r, _meta()).note_kind == "skip"

    def test_unreadable_type_routes_to_skip(self):
        r = _FakeResult(skipped=True, skip_reason="unsupported", handler_category="video")
        assert event_writer.compose_body(r, _meta(file_type="mp4")).note_kind == "skip"

    def test_empty_everything_routes_to_skip(self):
        r = _FakeResult(skipped=True, skip_reason="no_content")
        assert event_writer.compose_body(r, _meta()).note_kind == "skip"

    def test_text_present_routes_to_event(self):
        r = _FakeResult(text="Meeting with consultant on Aug 1.")
        assert event_writer.compose_body(r, _meta()).note_kind == "event"

    def test_images_only_routes_to_event(self):
        r = _FakeResult(
            attachments=["![[a.jpg]]"], images_embedded=1,
            image_candidate_paths=["/tmp/a.jpg"],
        )
        assert event_writer.compose_body(r, _meta()).note_kind == "event"


class TestSkipBehavior:
    def test_skip_has_empty_body_and_prompt(self):
        r = _FakeResult(skipped=True, skip_reason="no_content")
        out = event_writer.compose_body(r, _meta())
        assert out.note_kind == "skip"
        assert out.body_text == ""
        assert out.prompt_text == ""


class TestEventPrompt:
    def test_prompt_carries_event_metadata(self):
        r = _FakeResult(text="Design review — facade option B chosen.")
        m = _meta()
        out = event_writer.compose_body(r, m)
        assert m["event_date"] in out.prompt_text
        assert m["project"] in out.prompt_text
        assert "facade option B" in out.prompt_text

    def test_prompt_lists_image_file_paths_not_captions(self):
        """v16 change: the prompt hands the LLM image PATHS so it can
        Read them directly. Pre-v16 it passed a `captions_block` of
        one-line descriptions from a vision-runner pass — deleted."""
        r = _FakeResult(
            text="Site visit notes.",
            attachments=["![[a.jpg]]", "![[b.jpg]]"],
            images_embedded=2,
            image_candidate_paths=["/tmp/a.jpg", "/tmp/b.jpg"],
        )
        prompt = event_writer.compose_body(r, _meta()).prompt_text
        assert "/tmp/a.jpg" in prompt
        assert "/tmp/b.jpg" in prompt
        assert "Read tool" in prompt or "Read them" in prompt.lower() or "Read" in prompt

    def test_prompt_has_fabrication_firewall_language(self):
        r = _FakeResult(text="x")
        prompt = event_writer.compose_body(r, _meta()).prompt_text
        low = prompt.lower()
        assert "fabric" in low or "do not invent" in low

    def test_prompt_does_not_enforce_word_bounds(self):
        """v15.0.0 relaxed 100-200 word enforcement; v16 removes the
        guidance line too — shape is entirely the LLM's call."""
        r = _FakeResult(text="x")
        prompt = event_writer.compose_body(r, _meta()).prompt_text
        # Neither bound should appear as a constraint.
        assert "100-200" not in prompt
        assert "100–200" not in prompt


class TestValidator:
    def test_validator_passes_any_non_empty_prose(self):
        r = _FakeResult(text="raw")
        out = event_writer.compose_body(r, _meta())
        assert out.validator("Just a short note.").ok

    def test_validator_rejects_empty_body(self):
        r = _FakeResult(text="raw")
        out = event_writer.compose_body(r, _meta())
        result = out.validator("")
        assert not result.ok
        assert any("empty" in reason.lower() for reason in result.reasons)

    def test_validator_does_not_check_verbatim_paste(self):
        """v16: verbatim-paste detection deleted. The fabrication firewall
        is in the LLM prompt, not in Python string-matching."""
        raw = "The acoustic isolation strategy for the auditorium includes suspended ceilings."
        r = _FakeResult(text=raw)
        out = event_writer.compose_body(r, _meta())
        # A body that copy-pastes the raw text should NOT fail.
        result = out.validator("We discussed: " + raw)
        assert result.ok, f"v16 does not enforce verbatim-paste; got: {result.reasons}"

    def test_validator_stopwords_opt_in_still_works(self):
        r = _FakeResult(text="raw")
        out = event_writer.compose_body(r, _meta())
        body = "We moved on. The review came back with comments. We moved on."
        assert out.validator(body).ok  # default empty list
        event_writer.STOP_WORDS.append("the review came back")
        try:
            result = out.validator(body)
            assert not result.ok
            assert any("the review came back" in r.lower() for r in result.reasons)
        finally:
            event_writer.STOP_WORDS.pop()


class TestExtractAbstractCallout:
    def test_extracts_single_line_abstract(self):
        body = (
            "> [!abstract] Overview\n"
            "> SD 80% phase freeze with client on August 1.\n"
            "\nRest of the diary paragraph here."
        )
        assert event_writer.extract_abstract_callout(body) == (
            "SD 80% phase freeze with client on August 1."
        )

    def test_extracts_multi_line_abstract(self):
        body = (
            "> [!abstract] Overview\n"
            "> First half of the sentence,\n"
            "> and the second half after the line break.\n"
            "\nDiary paragraph."
        )
        hint = event_writer.extract_abstract_callout(body)
        assert "First half" in hint
        assert "second half" in hint
        assert "\n" not in hint

    def test_falls_back_to_first_sentence_when_no_abstract(self):
        body = "Met the client today at the SD review for the east wing."
        assert "Met the client" in event_writer.extract_abstract_callout(body)

    def test_first_sentence_fallback_ignores_headings_and_embeds(self):
        body = (
            "# 2024-08-15 SD meeting\n"
            "\n"
            "![[photo1.jpg]]\n"
            "\n"
            "Walked through floor plans with the consultant at the afternoon review.\n"
        )
        hint = event_writer.extract_abstract_callout(body)
        assert "floor plans" in hint
        assert "#" not in hint

    def test_first_sentence_fallback_skips_short_lines(self):
        body = "Met today.\n\nThen went home."
        assert event_writer.extract_abstract_callout(body) == ""

    def test_returns_empty_on_empty_body(self):
        assert event_writer.extract_abstract_callout("") == ""


class TestAssembleNoteBody:
    def test_no_attachments_returns_prose(self):
        assert event_writer.assemble_note_body("Hello.", []) == "Hello."

    def test_single_attachment_no_grid(self):
        out = event_writer.assemble_note_body("Hello.", ["![[a.jpg]]"])
        assert out == "Hello.\n\n![[a.jpg]]"

    def test_multiple_attachments_chunk_into_rows(self):
        out = event_writer.assemble_note_body(
            "Hello.",
            ["![[a.jpg]]", "![[b.jpg]]", "![[c.jpg]]", "![[d.jpg]]"],
            row_size=3,
        )
        # First 3 on consecutive lines, then blank line, then last one.
        assert "![[a.jpg]]\n![[b.jpg]]\n![[c.jpg]]\n\n![[d.jpg]]" in out

    def test_no_prose_returns_grid_only(self):
        out = event_writer.assemble_note_body("", ["![[a.jpg]]", "![[b.jpg]]"])
        assert "Hello" not in out
        assert "![[a.jpg]]" in out
        assert "![[b.jpg]]" in out
