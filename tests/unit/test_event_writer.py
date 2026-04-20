"""Tests for scripts/event_writer.py.

The event-writer is the keystone layer that turns raw scan output into an
event diary note. It classifies Template A (prose, grounded in content) vs
Template B (fixed bullets, fallback when no content was read), renders B
deterministically in Python, and emits a structured prompt for A that the
invoking Claude runs.

Contract:
  compose_body(result, meta) -> ComposedBody
    .template_kind: "A" | "B"
    .body_text: str         # rendered for B; empty for A until LLM fills it
    .prompt_text: str       # non-empty for A; empty for B
    .validator: callable[[str], ValidationResult]
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
        skipped=False,
        skip_reason="",
        handler_category="document-pdf",
        content_confidence="full",
        sources_read=None,
        read_bytes=0,
        image_grid=False,
        attachments_subfolder="",
    ):
        self.text = text
        self.attachments = attachments or []
        self.images_embedded = images_embedded
        self.image_captions = image_captions or []
        self.skipped = skipped
        self.skip_reason = skip_reason
        self.handler_category = handler_category
        self.content_confidence = content_confidence
        self.sources_read = sources_read or []
        self.read_bytes = read_bytes
        self.image_grid = image_grid
        self.attachments_subfolder = attachments_subfolder


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
    def test_skipped_no_content_routes_to_template_b(self):
        r = _FakeResult(skipped=True, skip_reason="no_content", handler_category="document-pdf")
        body = event_writer.compose_body(r, _meta())
        assert body.template_kind == "B"

    def test_skipped_unreadable_type_routes_to_template_b(self):
        r = _FakeResult(skipped=True, skip_reason="unsupported", handler_category="video")
        assert event_writer.compose_body(r, _meta(file_type="mp4")).template_kind == "B"

    def test_text_present_routes_to_template_a(self):
        r = _FakeResult(text="Meeting with consultant on Aug 1. Discussed structural plan.", content_confidence="full")
        assert event_writer.compose_body(r, _meta()).template_kind == "A"

    def test_images_only_with_captions_routes_to_template_a(self):
        r = _FakeResult(
            attachments=["![[a.jpg]]"],
            images_embedded=1,
            image_captions=["A sketch of the elevation showing the entry canopy."],
            content_confidence="partial",
        )
        assert event_writer.compose_body(r, _meta()).template_kind == "A"

    def test_empty_everything_routes_to_template_b(self):
        r = _FakeResult(skipped=True, skip_reason="no_content")
        assert event_writer.compose_body(r, _meta()).template_kind == "B"


class TestTemplateB:
    """Template B is deterministic — rendered in Python, never calls an LLM."""

    def test_b_body_contains_fixed_bullets(self):
        r = _FakeResult(skipped=True, skip_reason="no_content", handler_category="video")
        m = _meta(file_type="mp4", source_path="/nas/vids/opening.mp4")
        body = event_writer.compose_body(r, m)
        assert body.template_kind == "B"
        assert body.body_text.strip() != ""
        # Metadata-only note must not make claims; it lists the source.
        assert "opening.mp4" in body.body_text or "source" in body.body_text.lower()

    def test_b_body_never_fabricates(self):
        """Template B body must not contain stop-words or quotes."""
        r = _FakeResult(skipped=True, skip_reason="unsupported")
        body = event_writer.compose_body(r, _meta()).body_text
        # Fabrication firewall stop-words
        for phrase in ["the team said", "the review came back", "pulled the back wall in"]:
            assert phrase.lower() not in body.lower()

    def test_b_prompt_is_empty(self):
        r = _FakeResult(skipped=True, skip_reason="no_content")
        body = event_writer.compose_body(r, _meta())
        assert body.prompt_text == ""


class TestTemplateA:
    def test_a_prompt_contains_event_metadata(self):
        r = _FakeResult(text="Design review meeting. Decision: proceed with facade option B.")
        m = _meta()
        body = event_writer.compose_body(r, m)
        assert body.template_kind == "A"
        assert body.body_text == ""  # filled by invoking Claude
        assert body.prompt_text != ""
        # Prompt must carry enough context for the model
        assert m["event_date"] in body.prompt_text
        assert m["project"] in body.prompt_text
        assert "Decision: proceed with facade option B." in body.prompt_text

    def test_a_prompt_includes_captions_when_images_embedded(self):
        r = _FakeResult(
            text="Site visit notes.",
            attachments=["![[a.jpg]]", "![[b.jpg]]"],
            images_embedded=2,
            image_captions=[
                "Rebar laid on the south wall.",
                "Timber formwork partially installed.",
            ],
        )
        prompt = event_writer.compose_body(r, _meta()).prompt_text
        assert "Rebar laid on the south wall." in prompt
        assert "Timber formwork partially installed." in prompt

    def test_a_prompt_includes_fabrication_firewall_rules(self):
        r = _FakeResult(text="Short note.")
        prompt = event_writer.compose_body(r, _meta()).prompt_text
        # Must remind the writer to not fabricate
        low = prompt.lower()
        assert "fabric" in low or "grounded" in low or "only what" in low
        # Must specify word range
        assert "100" in prompt and "200" in prompt


class TestValidator:
    def test_validator_passes_clean_prose(self):
        r = _FakeResult(text="raw content")
        body = event_writer.compose_body(r, _meta())
        clean = (
            "On August 1 we met with the consultant at the SD review in the afternoon. "
            "We walked through the latest floor-plan revision, agreed on moving the entry "
            "to the east elevation, and flagged the stair detail for further study. The "
            "mechanical scope stays unchanged from the prior package. Next step is to "
            "update the set before the next coordination meeting at the end of the month. "
            "This is a deliberately padded paragraph to cross the minimum-word floor "
            "cleanly and land safely inside the allowed range for a diary note body. "
            "We left the office feeling productive and agreed to circle back before "
            "Friday to finalise the dimensions and confirm the revised floor plate. "
            "Everyone seemed aligned; the owner sent a thumbs up after the call ended."
        )
        result = body.validator(clean)
        assert result.ok, f"Expected ok, got: {result.reasons}"

    def test_validator_rejects_stop_word(self):
        r = _FakeResult(text="raw content")
        body = event_writer.compose_body(r, _meta())
        bad = (
            "On August 1 the review came back with several changes. Everyone was happy. "
            "We moved on. " * 10
        )
        result = body.validator(bad)
        assert not result.ok
        assert any("the review came back" in r.lower() for r in result.reasons)

    def test_validator_rejects_verbatim_paste(self):
        """If body contains a long consecutive substring from raw_text, reject."""
        raw = (
            "The following pages describe in detail the acoustic isolation strategy for the "
            "auditorium, including suspended ceilings, floating floors, and heavy mass walls."
        )
        r = _FakeResult(text=raw)
        body = event_writer.compose_body(r, _meta())
        # Body contains a 60+ char run from the raw text — should be flagged as paste.
        pasted = (
            "At the meeting we discussed the design. "
            + raw[:120]
            + " And then we went for lunch."
        )
        result = body.validator(pasted)
        assert not result.ok
        assert any("paste" in reason.lower() or "verbatim" in reason.lower() for reason in result.reasons)

    def test_validator_rejects_too_short(self):
        r = _FakeResult(text="raw")
        body = event_writer.compose_body(r, _meta())
        result = body.validator("Too short.")
        assert not result.ok
        assert any("word" in reason.lower() for reason in result.reasons)

    def test_validator_rejects_too_long(self):
        r = _FakeResult(text="raw")
        body = event_writer.compose_body(r, _meta())
        long_text = " ".join(["word"] * 400)
        result = body.validator(long_text)
        assert not result.ok
        assert any("word" in reason.lower() for reason in result.reasons)


class TestRenderFinalNote:
    """After LLM fills in Template A body, the event-writer assembles the final
    note body (body text + image block) with no blank lines between consecutive
    image embeds (Minimal theme grid requirement)."""

    def test_assemble_with_images_no_blank_lines_between_embeds(self):
        body_text = "Diary paragraph about the meeting."
        attachments = ["![[a.jpg]]", "![[b.jpg]]", "![[c.jpg]]"]
        out = event_writer.assemble_note_body(body_text, attachments)
        # No blank line between consecutive embeds
        embeds_block = "![[a.jpg]]\n![[b.jpg]]\n![[c.jpg]]"
        assert embeds_block in out
        # Blank line before the embed block separates prose from grid
        assert "meeting.\n\n![[a.jpg]]" in out

    def test_assemble_without_images(self):
        out = event_writer.assemble_note_body("Prose only.", [])
        assert out.strip() == "Prose only."

    def test_assemble_single_image(self):
        out = event_writer.assemble_note_body("Prose.", ["![[only.jpg]]"])
        assert "![[only.jpg]]" in out
