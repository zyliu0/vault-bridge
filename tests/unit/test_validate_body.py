"""Tests for scripts/validate_body.py — body-quality detectors.

Two detectors:
  is_stale_legacy_stub(body) — pre-v16 abstract-stub pattern (SSS-A)
  is_garbled_extract(text)   — CID-font extract garbage (SSS-B)

Python 3.9 compatible.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import validate_body as vb  # noqa: E402


# ---------------------------------------------------------------------------
# is_stale_legacy_stub — SSS-A signature: legacy abstract + lead/garbage
# ---------------------------------------------------------------------------

class TestIsStaleLegacyStub:
    """SSS field-report sample (verbatim from a 2018 SD/DD note)."""

    SSS_SAMPLE = (
        "> [!abstract] 摘要 — 源文档摘录\n"
        "> 来自源文档：\n"
        "\n"
        "i=10%\n"
        "\n"
        "2CBVVVVVVVVVVVVVVVVVVVVVVVVVVVVVVBB\n"
    )

    def test_matches_field_report_sample(self):
        assert vb.is_stale_legacy_stub(self.SSS_SAMPLE) is True

    def test_matches_with_garbage_run_only(self):
        body = (
            "> [!abstract] 摘要 — 源文档摘录\n"
            "> Body...\n\n"
            "ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZ\n"
        )
        assert vb.is_stale_legacy_stub(body) is True

    def test_matches_with_lead_only(self):
        """Just the abstract + 来自源文档 lead is enough."""
        body = (
            "> [!abstract] 摘要 — 源文档摘录\n"
            "> 来自源文档：\n"
            "\n"
            "Some prose without garbage runs.\n"
        )
        assert vb.is_stale_legacy_stub(body) is True

    def test_abstract_alone_not_enough(self):
        """An abstract callout without the legacy 来自源文档 lead and
        without garbage is just a normal note's abstract."""
        body = (
            "> [!abstract] 摘要\n"
            "> Project memo from 2024 — meeting between client and architect.\n"
        )
        # Note: this test is for false-positive guard. The abstract alone
        # without 摘要 — 源文档摘录 OR a lead/garbage trait should NOT trip.
        # `摘要` alone is too generic; require 来自源文档 lead OR garbage.
        # Updated body to NOT include the legacy lead/garbage:
        assert vb.is_stale_legacy_stub(body) is False

    def test_normal_v16_note_not_flagged(self):
        body = (
            "The architect met with the client on 2024-09-09 to review the "
            "schematic design. Three options were presented; option B was "
            "selected pending material review.\n"
        )
        assert vb.is_stale_legacy_stub(body) is False

    def test_empty_body_not_flagged(self):
        assert vb.is_stale_legacy_stub("") is False
        assert vb.is_stale_legacy_stub(None) is False  # type: ignore[arg-type]

    def test_normal_chinese_prose_not_flagged(self):
        """Real Chinese prose without the legacy header must pass."""
        body = (
            "项目方在 2024 年 9 月与客户开会，讨论了三个方案。最终选择"
            "方案 B，但需要对材料进行进一步审查。\n"
        )
        assert vb.is_stale_legacy_stub(body) is False

    def test_legacy_abstract_with_cmap_debug_string(self):
        """PyPDF2's CMap-debug emission alongside the abstract should also trip."""
        body = (
            "> [!abstract] 源文档摘录\n"
            "> body\n\n"
            "Advanced encoding /90ms-RKSJ-H not implemented yet\n"
        )
        assert vb.is_stale_legacy_stub(body) is True


# ---------------------------------------------------------------------------
# is_garbled_extract — SSS-B signature: long unbroken digit/cap runs
# ---------------------------------------------------------------------------

class TestIsGarbledExtract:
    """SSS field-report sample of a Stage equipment meeting PDF extract."""

    SSS_SAMPLE = (
        "Stage equipment meeting notes\n"
        "567884008400520084006750371505678840084005200840067503715"
        "0840084008400840084008400 UVWXY T4700700 some other text "
        "and lots of digits 91728273837473848484747474848484848 T4700700 "
        "9384839483948394839483948394839483948394839483948394839483948394\n"
    )

    def test_matches_field_report_sample(self):
        assert vb.is_garbled_extract(self.SSS_SAMPLE) is True

    def test_cmap_debug_string_short_circuits(self):
        """Any /Adobe-Japan1 or 'Advanced encoding ... not implemented yet'
        is a definitive signal — short or long, it's garbage."""
        text = (
            "Some prose here.\n"
            "Advanced encoding /90ms-RKSJ-H not implemented yet\n"
        )
        assert vb.is_garbled_extract(text) is True

    def test_normal_prose_not_garbled(self):
        text = (
            "The meeting on 2024-09-09 covered the schematic design phase. "
            "Three alternatives were presented; the team chose option B "
            "for further development. Action items: confirm structural "
            "review by next week, prepare facade material samples, and "
            "schedule a follow-up review with the lighting consultant.\n"
        )
        assert vb.is_garbled_extract(text) is False

    def test_short_text_not_judged(self):
        """Very short text shouldn't trigger — too easy to false-positive."""
        text = "OK\n"
        assert vb.is_garbled_extract(text) is False

    def test_url_alone_not_flagged(self):
        """URLs are long unbroken runs but legitimate — coverage ratio
        keeps them clean."""
        text = (
            "See https://example.com/very/long/path/with/lots/of/segments "
            "and then some explanatory prose that makes the long URL "
            "a small fraction of the total content of this paragraph.\n"
        )
        assert vb.is_garbled_extract(text) is False

    def test_base64_blob_in_legitimate_context_not_flagged(self):
        """Even a chunk of mixed base64/hash in legitimate prose passes
        unless it dominates."""
        text = (
            "Configuration: api-key=AbCdEfGhIjKlMnOpQrStUvWx and the rest "
            "of the configuration covers retry policy, timeout, and "
            "logging level. The integration test suite verifies all "
            "three pathways under load.\n"
        )
        assert vb.is_garbled_extract(text) is False

    def test_empty_text(self):
        assert vb.is_garbled_extract("") is False
        assert vb.is_garbled_extract(None) is False  # type: ignore[arg-type]

    def test_pure_caps_block_alone_does_not_flag(self):
        """A single homogeneous block of capitals is too ambiguous —
        could be a `D * 101` content-confidence test fixture or a real
        ALL-CAPS slogan. CID garbage always has multiple distinct runs
        OR includes digits. Refuse to flag this case."""
        text = "D" * 101
        assert vb.is_garbled_extract(text) is False

    def test_single_long_digit_run_dominant(self):
        """A 60-char unbroken digit run + 25% coverage check — real
        prose almost never produces this."""
        text = "123456789012345678901234567890123456789012345678901234567890\nx\n"
        assert vb.is_garbled_extract(text) is True

    def test_multiple_digit_runs_flag(self):
        """Multiple ≥40-char digit runs is the strongest signal of
        CID-font extract garbage."""
        text = (
            "1234567890123456789012345678901234567890 some prose "
            "9876543210987654321098765432109876543210 more prose\n"
        )
        assert vb.is_garbled_extract(text) is True

    def test_single_100_char_digit_run_alone_flags(self):
        """100+ char unbroken digit run is essentially never legit."""
        text = "1" * 100 + "\nsome other content here.\n"
        assert vb.is_garbled_extract(text) is True


# ---------------------------------------------------------------------------
# garbled_extract_reasons
# ---------------------------------------------------------------------------

class TestGarbledExtractReasons:
    def test_clean_text_returns_empty(self):
        assert vb.garbled_extract_reasons("normal prose here") == []

    def test_cmap_signature_first_priority(self):
        text = "stuff\nAdvanced encoding /90ms-RKSJ-H not implemented yet\n"
        reasons = vb.garbled_extract_reasons(text)
        assert any("CMap" in r for r in reasons)

    def test_digit_runs_reported(self):
        text = "x" * 10 + "1" * 60 + "y" * 10
        reasons = vb.garbled_extract_reasons(text)
        assert any("digit run" in r for r in reasons)

    def test_multiple_digit_runs_reported(self):
        text = (
            "1234567890123456789012345678901234567890 prose "
            "9876543210987654321098765432109876543210 more\n"
        )
        reasons = vb.garbled_extract_reasons(text)
        assert any("digit run" in r for r in reasons)
