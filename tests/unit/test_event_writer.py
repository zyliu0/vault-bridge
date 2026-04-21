"""Tests for scripts/event_writer.py.

The event-writer is the keystone layer that turns raw scan output into an
event diary note. It classifies event note (prose, grounded in content)
vs metadata stub (fixed bullets, fallback when no content was read),
renders the stub deterministically in Python, and emits a structured
prompt for the event note that the invoking Claude runs.

Contract:
  compose_body(result, meta) -> ComposedBody
    .note_kind: "event" | "stub"
    .body_text: str         # rendered for stub; empty for event until LLM fills it
    .prompt_text: str       # non-empty for event; empty for stub
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
    def test_skipped_no_content_routes_to_stub(self):
        r = _FakeResult(skipped=True, skip_reason="no_content", handler_category="document-pdf")
        body = event_writer.compose_body(r, _meta())
        assert body.note_kind == "stub"

    def test_skipped_unreadable_type_routes_to_stub(self):
        r = _FakeResult(skipped=True, skip_reason="unsupported", handler_category="video")
        assert event_writer.compose_body(r, _meta(file_type="mp4")).note_kind == "stub"

    def test_text_present_routes_to_event_note(self):
        r = _FakeResult(text="Meeting with consultant on Aug 1. Discussed structural plan.", content_confidence="full")
        assert event_writer.compose_body(r, _meta()).note_kind == "event"

    def test_images_only_with_captions_routes_to_event_note(self):
        r = _FakeResult(
            attachments=["![[a.jpg]]"],
            images_embedded=1,
            image_captions=["A sketch of the elevation showing the entry canopy."],
            content_confidence="partial",
        )
        assert event_writer.compose_body(r, _meta()).note_kind == "event"

    def test_empty_everything_routes_to_stub(self):
        r = _FakeResult(skipped=True, skip_reason="no_content")
        assert event_writer.compose_body(r, _meta()).note_kind == "stub"


class TestMetadataStub:
    """The metadata stub is deterministic — rendered in Python, never calls an LLM."""

    def test_stub_body_contains_fixed_bullets(self):
        r = _FakeResult(skipped=True, skip_reason="no_content", handler_category="video")
        m = _meta(file_type="mp4", source_path="/nas/vids/opening.mp4")
        body = event_writer.compose_body(r, m)
        assert body.note_kind == "stub"
        assert body.body_text.strip() != ""
        # Metadata-only note must not make claims; it lists the source.
        assert "opening.mp4" in body.body_text or "source" in body.body_text.lower()

    def test_stub_body_never_fabricates(self):
        """Metadata stub body must not contain stop-words or quotes."""
        r = _FakeResult(skipped=True, skip_reason="unsupported")
        body = event_writer.compose_body(r, _meta()).body_text
        # Fabrication firewall stop-words
        for phrase in ["the team said", "the review came back", "pulled the back wall in"]:
            assert phrase.lower() not in body.lower()

    def test_stub_prompt_is_empty(self):
        r = _FakeResult(skipped=True, skip_reason="no_content")
        body = event_writer.compose_body(r, _meta())
        assert body.prompt_text == ""


class TestEventNote:
    def test_event_prompt_contains_event_metadata(self):
        r = _FakeResult(text="Design review meeting. Decision: proceed with facade option B.")
        m = _meta()
        body = event_writer.compose_body(r, m)
        assert body.note_kind == "event"
        assert body.body_text == ""  # filled by invoking Claude
        assert body.prompt_text != ""
        # Prompt must carry enough context for the model
        assert m["event_date"] in body.prompt_text
        assert m["project"] in body.prompt_text
        assert "Decision: proceed with facade option B." in body.prompt_text

    def test_event_prompt_includes_captions_when_images_embedded(self):
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

    def test_event_prompt_includes_fabrication_firewall_rules(self):
        r = _FakeResult(text="Short note.")
        prompt = event_writer.compose_body(r, _meta()).prompt_text
        # Must remind the writer to not fabricate
        low = prompt.lower()
        assert "fabric" in low or "grounded" in low or "only what" in low
        # Must specify word range
        assert "100" in prompt and "200" in prompt


_ABSTRACT_CALLOUT = (
    "> [!abstract] Overview\n"
    "> Met the consultant to confirm SD revisions and schedule next review.\n"
    "\n"
)


class TestValidator:
    def test_validator_passes_clean_prose(self):
        r = _FakeResult(text="raw content")
        body = event_writer.compose_body(r, _meta())
        clean = _ABSTRACT_CALLOUT + (
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
        bad = _ABSTRACT_CALLOUT + (
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
        pasted = _ABSTRACT_CALLOUT + (
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
        result = body.validator(_ABSTRACT_CALLOUT + "Too short.")
        assert not result.ok
        assert any("word" in reason.lower() for reason in result.reasons)

    def test_validator_rejects_too_long(self):
        r = _FakeResult(text="raw")
        body = event_writer.compose_body(r, _meta())
        long_text = _ABSTRACT_CALLOUT + " ".join(["word"] * 400)
        result = body.validator(long_text)
        assert not result.ok
        assert any("word" in reason.lower() for reason in result.reasons)

    def test_validator_rejects_missing_abstract_callout(self):
        """Event notes MUST start with > [!abstract] Overview (v14.4)."""
        r = _FakeResult(text="raw")
        body = event_writer.compose_body(r, _meta())
        # Same prose as the clean-prose test, BUT without the abstract callout.
        no_abstract = (
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
        result = body.validator(no_abstract)
        assert not result.ok
        assert any("abstract" in reason.lower() for reason in result.reasons)

    def test_validator_rejects_abstract_callout_too_short(self):
        r = _FakeResult(text="raw")
        body = event_writer.compose_body(r, _meta())
        # Abstract has 3 words — below ABSTRACT_CALLOUT_MIN_WORDS (5)
        too_short_abs = (
            "> [!abstract] Overview\n"
            "> Met client today.\n"
            "\n"
            + " ".join(["word"] * 150)
        )
        result = body.validator(too_short_abs)
        assert not result.ok
        assert any("abstract" in r.lower() and "short" in r.lower() for r in result.reasons)


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
        # Joined into one line
        assert "\n" not in hint

    def test_returns_empty_when_no_abstract(self):
        body = "Just a diary paragraph with no callout at the top."
        assert event_writer.extract_abstract_callout(body) == ""

    def test_returns_empty_on_empty_body(self):
        assert event_writer.extract_abstract_callout("") == ""

    def test_ignores_non_abstract_callouts(self):
        body = (
            "> [!important] Something else\n"
            "> Not an abstract callout.\n"
            "\n"
            "Diary."
        )
        assert event_writer.extract_abstract_callout(body) == ""


class TestRenderFinalNote:
    """After the LLM fills in an event-note body, the event-writer assembles
    the final note body (body text + image block). Embeds are chunked into
    rows of IMAGE_GRID_ROW_SIZE so Minimal's img-grid CSS produces one grid
    row per paragraph instead of a single flat strip (v14.3, F5)."""

    def test_assemble_within_row_has_no_blank_lines(self):
        """≤ row_size embeds all live in one paragraph."""
        body_text = "Diary paragraph about the meeting."
        attachments = ["![[a.jpg]]", "![[b.jpg]]", "![[c.jpg]]"]
        out = event_writer.assemble_note_body(body_text, attachments)
        # No blank line between consecutive embeds within one row
        embeds_block = "![[a.jpg]]\n![[b.jpg]]\n![[c.jpg]]"
        assert embeds_block in out
        # Blank line before the embed block separates prose from grid
        assert "meeting.\n\n![[a.jpg]]" in out

    def test_assemble_breaks_embeds_into_rows(self):
        """> row_size embeds are split into paragraphs of row_size each.

        This is the F5 fix: a single paragraph of 10 embeds would render
        as one 10-column strip under Minimal's img-grid CSS. Blank lines
        between rows open new paragraphs, each styled as its own grid row.
        """
        attachments = [f"![[{c}.jpg]]" for c in "abcdef"]
        out = event_writer.assemble_note_body("Prose.", attachments, row_size=3)
        # First row: a, b, c together
        assert "![[a.jpg]]\n![[b.jpg]]\n![[c.jpg]]" in out
        # Blank line breaks into the second row
        assert "![[c.jpg]]\n\n![[d.jpg]]" in out
        # Second row: d, e, f together
        assert "![[d.jpg]]\n![[e.jpg]]\n![[f.jpg]]" in out

    def test_assemble_row_size_honoured(self):
        """Custom row_size produces matching chunks."""
        attachments = [f"![[{i}.jpg]]" for i in range(8)]
        out = event_writer.assemble_note_body("", attachments, row_size=2)
        # 8 embeds at row_size=2 → 4 rows → 3 blank-line separators
        assert out.count("\n\n") == 3

    def test_assemble_uneven_last_row(self):
        """The last row holds the remainder when len(attachments) % row_size != 0."""
        attachments = [f"![[{i}.jpg]]" for i in range(7)]
        out = event_writer.assemble_note_body("", attachments, row_size=3)
        # 7 at row_size=3 → rows of 3, 3, 1
        assert out.count("\n\n") == 2
        assert out.endswith("![[6.jpg]]")

    def test_assemble_without_images(self):
        out = event_writer.assemble_note_body("Prose only.", [])
        assert out.strip() == "Prose only."

    def test_assemble_single_image(self):
        """A single embed has no rows to break into."""
        out = event_writer.assemble_note_body("Prose.", ["![[only.jpg]]"])
        assert "![[only.jpg]]" in out
        # No spurious blank lines
        assert "\n\n\n" not in out
