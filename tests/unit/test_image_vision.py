"""Tests for scripts/image_vision.py — vision-caption prompt builder and
relevance-based selection of the top-k images to embed.

Vision is not executed in-process: vault-bridge has no LLM client. Instead
this module emits prompts the invoking Claude runs, and then ranks the
returned captions by textual relevance to the event metadata.
"""
import pytest

from scripts import image_vision


class TestCaptionPrompt:
    def test_prompt_references_the_image_path(self):
        p = image_vision.caption_prompt_for(
            "/tmp/compressed/2024-08-01--site--abc12345.jpg",
            event_meta={"event_date": "2024-08-01", "project": "2408 Sample"},
        )
        assert "/tmp/compressed/2024-08-01--site--abc12345.jpg" in p
        assert "one sentence" in p.lower() or "single sentence" in p.lower()

    def test_prompt_instructs_no_hedging(self):
        p = image_vision.caption_prompt_for("/x.jpg", event_meta={})
        low = p.lower()
        # Must instruct against speculation.
        assert "do not" in low or "avoid" in low

    def test_prompt_includes_event_context(self):
        p = image_vision.caption_prompt_for(
            "/x.jpg",
            event_meta={"event_date": "2024-08-01", "project": "2408 Sample", "source_basename": "kickoff.pdf"},
        )
        assert "2408 Sample" in p


class TestRelevanceScoring:
    def test_empty_captions_returns_empty(self):
        assert image_vision.score_relevance([], event_meta={"project": "x"}) == []

    def test_caption_matching_project_scores_higher(self):
        captions = [
            "A photo of a potted plant unrelated to anything.",
            "Site plan for the 2408 Sample residence showing the north facade.",
        ]
        scores = image_vision.score_relevance(
            captions,
            event_meta={"project": "2408 Sample", "source_basename": "facade.pdf"},
        )
        assert scores[1] > scores[0]

    def test_caption_matching_source_stem_scores_higher(self):
        captions = [
            "Random shelf photo.",
            "The mechanical schematic detailing HVAC layout.",
        ]
        scores = image_vision.score_relevance(
            captions,
            event_meta={"project": "Office Fit-out", "source_basename": "mechanical-schematic.pdf"},
        )
        assert scores[1] > scores[0]

    def test_scores_are_floats_between_zero_and_one(self):
        scores = image_vision.score_relevance(
            ["anything", "whatever", "nothing"],
            event_meta={"project": "p"},
        )
        for s in scores:
            assert isinstance(s, float)
            assert 0.0 <= s <= 1.0


class TestSelectTopK:
    def test_selects_top_k_by_score(self):
        captions = [
            "irrelevant shelf",                      # 0
            "the 2408 Sample facade elevation",      # 1 — should rank high
            "a blank wall",                          # 2
            "ceiling detail for 2408 Sample",        # 3 — should rank high
        ]
        idx = image_vision.select_top_k(
            captions, event_meta={"project": "2408 Sample"}, k=2
        )
        assert set(idx) == {1, 3}
        assert len(idx) == 2

    def test_k_greater_than_captions_returns_all_in_original_order(self):
        caps = ["a", "b", "c"]
        idx = image_vision.select_top_k(caps, event_meta={"project": "p"}, k=10)
        assert sorted(idx) == [0, 1, 2]

    def test_zero_k_returns_empty(self):
        assert image_vision.select_top_k(["a", "b"], event_meta={}, k=0) == []

    def test_tie_break_is_stable_by_original_index(self):
        """When all captions score identically (no keywords hit), pick the
        first k in original order — deterministic."""
        caps = ["x", "y", "z", "w"]
        idx = image_vision.select_top_k(caps, event_meta={"project": "unmatched"}, k=2)
        assert idx == [0, 1]
