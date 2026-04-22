"""Tests for scripts/link_strategy.py — orphaned note wikilink creation.

Covers find_orphaned_notes, find_linking_candidates, build_related_notes_section,
and append_related_notes.
All tests use tmp_path for isolation and mock obsidian CLI calls.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import link_strategy as ls  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def mock_vault():
    """Return a mock vault_name."""
    return "TestVault"


@pytest.fixture
def sample_orphan() -> dict:
    """Return a typical orphaned note frontmatter dict."""
    return {
        "project": "250507 COSMOS",
        "domain": "arch-projects",
        "source_path": "/nas/2505 COSMOS展厅/250508 概念方案.pdf",
        "file_type": "pdf",
        "event_date": "2025-05-08",
        "content_confidence": "metadata-only",
        "vault_path": "arch-projects/250507 COSMOS/SD/2025-05-08 concept scheme v1.md",
    }


@pytest.fixture
def sample_candidates() -> list:
    """Return a list of candidate link targets."""
    return [
        {
            "vault_path": "arch-projects/250507 COSMOS/SD/2025-05-09 site visit.md",
            "event_date": "2025-05-09",
            "title": "2025-05-09 site visit",
            "project": "250507 COSMOS",
            "file_type": "folder",
            "relevance_score": 3,
        },
        {
            "vault_path": "arch-projects/250507 COSMOS/DD/2025-05-12 structural notes.md",
            "event_date": "2025-05-12",
            "title": "2025-05-12 structural notes",
            "project": "250507 COSMOS",
            "file_type": "docx",
            "relevance_score": 2,
        },
        {
            "vault_path": "arch-projects/250507 COSMOS/SD/2025-05-08 concept scheme v1.md",
            "event_date": "2025-05-08",
            "title": "2025-05-08 concept scheme v1",
            "project": "250507 COSMOS",
            "file_type": "folder",
            "relevance_score": 3,
        },
    ]


# ---------------------------------------------------------------------------
# is_metadata_only_type
# ---------------------------------------------------------------------------

def test_is_metadata_only_type_true_for_dwg():
    assert ls.is_metadata_only_type("dwg") is True


def test_is_metadata_only_type_true_for_3dm():
    assert ls.is_metadata_only_type("3dm") is True


def test_is_metadata_only_type_true_for_rvt():
    assert ls.is_metadata_only_type("rvt") is True


def test_is_metadata_only_type_true_for_skp():
    assert ls.is_metadata_only_type("skp") is True


def test_is_metadata_only_type_false_for_pdf():
    assert ls.is_metadata_only_type("pdf") is False


def test_is_metadata_only_type_false_for_folder():
    assert ls.is_metadata_only_type("folder") is False


def test_is_metadata_only_type_false_for_docx():
    assert ls.is_metadata_only_type("docx") is False


def test_is_metadata_only_type_false_for_png():
    assert ls.is_metadata_only_type("png") is False


def test_is_metadata_only_type_false_for_image_folder():
    assert ls.is_metadata_only_type("image-folder") is False


# ---------------------------------------------------------------------------
# is_readable_type
# ---------------------------------------------------------------------------

def test_is_readable_type_true_for_pdf():
    assert ls.is_readable_type("pdf") is True


def test_is_readable_type_true_for_docx():
    assert ls.is_readable_type("docx") is True


def test_is_readable_type_true_for_pptx():
    assert ls.is_readable_type("pptx") is True


def test_is_readable_type_true_for_folder():
    assert ls.is_readable_type("folder") is True


def test_is_readable_type_true_for_image_folder():
    assert ls.is_readable_type("image-folder") is True


def test_is_readable_type_true_for_png():
    assert ls.is_readable_type("png") is True


def test_is_readable_type_true_for_jpg():
    assert ls.is_readable_type("jpg") is True


def test_is_readable_type_false_for_dwg():
    assert ls.is_readable_type("dwg") is False


def test_is_readable_type_false_for_3dm():
    assert ls.is_readable_type("3dm") is False


# ---------------------------------------------------------------------------
# build_related_notes_section
# ---------------------------------------------------------------------------

def test_build_related_notes_section_empty_candidates():
    result = ls.build_related_notes_section([])
    assert result == ""


def test_build_related_notes_section_single_candidate(sample_candidates):
    result = ls.build_related_notes_section(sample_candidates[:1])
    assert "## Related notes" in result
    assert "[[2025-05-09 site visit]]" in result


def test_build_related_notes_section_multiple_candidates_sorted_by_relevance(sample_candidates):
    """Candidates with higher relevance_score appear first."""
    result = ls.build_related_notes_section(sample_candidates)
    lines = result.strip().split("\n")
    # Find positions of each link
    positions = {line.strip("- "): i for i, line in enumerate(lines) if "[[" in line}
    # "2025-05-09 site visit" (score 3) and "2025-05-08 concept scheme v1" (score 3)
    # should appear before "2025-05-12 structural notes" (score 2)
    assert positions["[[2025-05-09 site visit]]"] < positions["[[2025-05-12 structural notes]]"]


def test_build_related_notes_section_respects_max_links(sample_candidates):
    result = ls.build_related_notes_section(sample_candidates, max_links=2)
    link_count = sum(1 for line in result.split("\n") if "[[" in line and "!" not in line)
    assert link_count <= 2


def test_build_related_notes_section_deduplicates(sample_candidates):
    """If same vault_path appears twice, only one link is emitted."""
    dup = sample_candidates + [sample_candidates[0]]
    result = ls.build_related_notes_section(dup)
    # Only 3 unique links should appear
    link_count = sum(1 for line in result.split("\n") if "[[" in line and "!" not in line)
    assert link_count == 3


# ---------------------------------------------------------------------------
# date_proximity
# ---------------------------------------------------------------------------

def test_date_proximity_same_day():
    """Same date = max proximity."""
    score = ls.date_proximity("2025-05-08", "2025-05-08")
    assert score == ls._MAX_DATE_PROXIMITY_SCORE


def test_date_proximity_within_window():
    """Dates within DATE_PROXIMITY_DAYS get positive score."""
    score = ls.date_proximity("2025-05-08", "2025-05-10")
    assert score > 0
    assert score < ls._MAX_DATE_PROXIMITY_SCORE


def test_date_proximity_outside_window():
    """Dates outside DATE_PROXIMITY_DAYS get zero score."""
    score = ls.date_proximity("2025-05-08", "2025-05-20")
    assert score == 0


def test_date_proximity_reverse_order():
    """Order doesn't matter."""
    a = ls.date_proximity("2025-05-10", "2025-05-08")
    b = ls.date_proximity("2025-05-08", "2025-05-10")
    assert a == b


# ---------------------------------------------------------------------------
# path_segment_overlap
# ---------------------------------------------------------------------------

def test_path_segment_overlap_deep_path():
    """Common path segments contribute to score."""
    score = ls.path_segment_overlap(
        "/nas/project/SubFolder/file.dwg",
        "/nas/project/SubFolder/other.pdf",
    )
    assert score > 0


def test_path_segment_overlap_no_overlap():
    """Different paths score zero."""
    score = ls.path_segment_overlap(
        "/nas/projectA/file.dwg",
        "/nas/projectB/file.dwg",
    )
    assert score == 0


def test_path_segment_overlap_parent_path():
    """Parent path overlap counts."""
    score = ls.path_segment_overlap(
        "/nas/project/SubFolder/file.dwg",
        "/nas/project/SubFolder",
    )
    assert score > 0


# ---------------------------------------------------------------------------
# compute_relevance_score
# ---------------------------------------------------------------------------

def test_compute_relevance_same_project_highest():
    """Same project gives base score plus date proximity bonus."""
    orphan = {"project": "250507 COSMOS", "event_date": "2025-05-08", "file_type": "pdf"}
    # Use far-apart dates to isolate project score
    candidate = {"project": "250507 COSMOS", "event_date": "2025-05-25", "source_path": "/nas/2505 COSMOS"}
    score = ls.compute_relevance_score(orphan, candidate)
    # Score = same_project(3) + date_proximity(0) + path_overlap(0) = 3
    assert score == ls._MAX_RELEVANCE_SCORE


def test_compute_relevance_different_project_lower():
    """Different project gives lower score than same project."""
    orphan = {"project": "250507 COSMOS", "event_date": "2025-05-08", "file_type": "pdf"}
    # Use far-apart dates so date_proximity=0, isolating project contribution
    candidate = {"project": "OtherProject", "event_date": "2025-05-25", "source_path": "/nas/other"}
    score = ls.compute_relevance_score(orphan, candidate)
    # Score = 0 (different project) + 0 (date too far) = 0 < 3
    assert score < ls._MAX_RELEVANCE_SCORE


def test_compute_relevance_adds_date_proximity():
    """Date proximity is added to base score."""
    orphan = {"project": "250507 COSMOS", "event_date": "2025-05-08", "file_type": "pdf"}
    same_day = {"project": "250507 COSMOS", "event_date": "2025-05-08", "source_path": "/nas/2505 COSMOS"}
    far_day = {"project": "250507 COSMOS", "event_date": "2025-05-20", "source_path": "/nas/2505 COSMOS"}
    same_score = ls.compute_relevance_score(orphan, same_day)
    far_score = ls.compute_relevance_score(orphan, far_day)
    assert same_score > far_score


def test_compute_relevance_adds_path_overlap_for_metadata_only():
    """Path overlap adds to score for metadata-only file types."""
    # Orphan needs source_path so path_overlap can be computed
    orphan = {"project": "250507 COSMOS", "event_date": "2025-05-08", "file_type": "dwg",
               "source_path": "/nas/2505 COSMOS展厅/DD/model.dwg"}
    # with_path shares same archive subfolder as orphan
    with_path = {"project": "250507 COSMOS", "event_date": "2025-05-25", "source_path": "/nas/2505 COSMOS展厅/DD/other.pdf"}
    # without_path is in a different archive subfolder
    without_path = {"project": "250507 COSMOS", "event_date": "2025-05-25", "source_path": "/nas/other"}
    with_score = ls.compute_relevance_score(orphan, with_path)
    without_score = ls.compute_relevance_score(orphan, without_path)
    # with_path: same_project(3) + date(0) + path_overlap(1) = 4
    # without_path: same_project(3) + date(0) + path_overlap(0) = 3
    assert with_score > without_score


# ---------------------------------------------------------------------------
# find_linking_candidates — mocked
# ---------------------------------------------------------------------------

def test_find_linking_candidates_returns_empty_for_valid_note(mock_vault, sample_orphan):
    """If obsidian search finds no orphans, candidates list is empty."""
    with patch("link_strategy.obsidian_search") as mock_search:
        mock_search.return_value = []  # no orphans at all
        result = ls.find_linking_candidates(sample_orphan, Path("/tmp"), mock_vault)
    assert result == []


def test_find_linking_candidates_excludes_self(mock_vault, sample_orphan):
    """The orphan itself is excluded from candidates."""
    with patch("link_strategy.obsidian_search") as mock_search:
        # obsidian_search returns list of dicts with vault_path
        mock_search.return_value = [sample_orphan]
        result = ls.find_linking_candidates(sample_orphan, Path("/tmp"), mock_vault)
    # Should exclude the orphan's own vault_path
    vault_paths = [c["vault_path"] for c in result]
    assert sample_orphan["vault_path"] not in vault_paths


def test_find_linking_candidates_respects_max_candidates(mock_vault, sample_orphan):
    """Returns at most max_candidates candidates."""
    many_candidates = [
        {**sample_orphan, "vault_path": f"arch-projects/250507 COSMOS/SD/note-{i}.md",
         "event_date": "2025-05-08", "title": f"Note {i}", "project": "250507 COSMOS",
         "file_type": "folder", "source_path": f"/nas/{i}"}
        for i in range(20)
    ]
    with patch("link_strategy.obsidian_search") as mock_search:
        mock_search.return_value = many_candidates
        result = ls.find_linking_candidates(sample_orphan, Path("/tmp"), mock_vault, max_candidates=5)
    assert len(result) <= 5


# ---------------------------------------------------------------------------
# append_related_notes — mocked
# ---------------------------------------------------------------------------

def test_append_related_notes_calls_obsidian_append(mock_vault, sample_orphan, sample_candidates):
    """append_related_notes calls obsidian append with correct args."""
    section = ls.build_related_notes_section(sample_candidates)
    with patch("link_strategy.run_obsidian") as mock_run:
        mock_run.return_value = ("", "", 0)
        ok = ls.append_related_notes(mock_vault, sample_orphan["vault_path"], section)
    assert ok is True
    mock_run.assert_called_once()
    args = mock_run.call_args[0][0]
    assert "append" in args


def test_append_related_notes_empty_section_is_noop(mock_vault, sample_orphan):
    """Empty section results in no call to obsidian."""
    with patch("link_strategy.run_obsidian") as mock_run:
        ok = ls.append_related_notes(mock_vault, sample_orphan["vault_path"], "")
    assert ok is True
    mock_run.assert_not_called()


def test_append_related_notes_failure_returns_false(mock_vault, sample_orphan, sample_candidates):
    """If obsidian append fails, returns False."""
    section = ls.build_related_notes_section(sample_candidates)
    with patch("link_strategy.run_obsidian") as mock_run:
        mock_run.return_value = ("error", "failed", 1)
        ok = ls.append_related_notes(mock_vault, sample_orphan["vault_path"], section)
    assert ok is False


# ---------------------------------------------------------------------------
# Metadata-stub wikilink injection — build_stub_with_links
# ---------------------------------------------------------------------------

def test_build_stub_with_links_adds_section(sample_orphan):
    """Metadata-stub body gets ## Related notes section appended."""
    section = ls.build_related_notes_section([
        {"vault_path": "arch-projects/250507 COSMOS/SD/2025-05-09 visit.md",
         "event_date": "2025-05-09", "title": "2025-05-09 visit",
         "project": "250507 COSMOS", "file_type": "folder", "source_path": "/nas/visit"}
    ])
    stub_body = ls.STUB_BODY.format(
        name="250508 概念方案.pdf",
        file_type="pdf",
        size="256.6 KB",
        date="2025-05-08",
        reason="read limit reached (cache guard)",
        source_path="/nas/2505 COSMOS展厅/250508 概念方案.pdf",
    )
    result = ls.build_stub_with_links(stub_body, section)
    assert "## Related notes" in result
    assert "[[2025-05-09 visit]]" in result


def test_build_stub_with_links_no_candidates():
    """Empty candidates leaves the stub unchanged."""
    stub_body = "**Metadata-only event.**\n\nNAS: `/path`"
    result = ls.build_stub_with_links(stub_body, "")
    assert result == stub_body


# ---------------------------------------------------------------------------
# LinkStrategyConfig defaults
# ---------------------------------------------------------------------------

def test_link_strategy_config_defaults():
    cfg = ls.LinkStrategyConfig()
    assert cfg.enabled is True
    assert cfg.max_links_per_note == 5
    assert cfg.date_proximity_days == 3
    assert "dwg" in cfg.metadata_only_types
    assert "pdf" in cfg.readable_types


def test_link_strategy_config_from_dict():
    d = {
        "enabled": False,
        "max_links_per_note": 3,
        "date_proximity_days": 7,
    }
    cfg = ls.LinkStrategyConfig.from_dict(d)
    assert cfg.enabled is False
    assert cfg.max_links_per_note == 3
    assert cfg.date_proximity_days == 7


# ---------------------------------------------------------------------------
# CLI: find-orphans subcommand
# ---------------------------------------------------------------------------

def test_cli_find_orphans_json_output(tmp_path, monkeypatch, capsys, mock_vault):
    """CLI find-orphans argument parsing produces valid JSON (or empty list when no vault)."""
    import runpy

    # When obsidian is not running, run_obsidian returns ("", error, 1),
    # so obsidian_search returns [], find_orphans returns [].
    # The CLI should still exit 0 and output valid JSON.
    with patch("link_strategy.run_obsidian") as mock_run:
        mock_run.return_value = ("", "Obsidian not running", 1)
        monkeypatch.setattr(sys, "argv", [
            "link_strategy.py", "find-orphans",
            "--workdir", str(tmp_path),
            "--vault", mock_vault,
        ])
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(SCRIPTS / "link_strategy.py"), run_name="__main__")

    assert exc.value.code == 0
    captured = capsys.readouterr()
    # Valid JSON (possibly empty list) should be printed
    data = json.loads(captured.out)
    assert isinstance(data, list)


def test_cli_find_orphans_project_filter(tmp_path, monkeypatch, capsys, mock_vault):
    """CLI find-orphans --project filters via obsidian search."""
    import runpy

    orphan_note = {
        "vault_path": "arch-projects/250507 COSMOS/SD/2025-05-08 orphan.md",
        "project": "OtherProject",
        "event_date": "2025-05-08",
        "file_type": "pdf",
        "content_confidence": "metadata-only",
    }
    call_count = [0]

    def fake_run_obsidian(args):
        call_count[0] += 1
        if "plugin: vault-bridge" in " ".join(args):
            return json.dumps([orphan_note]), "", 0
        return "[]", "", 0

    with patch("link_strategy.run_obsidian", side_effect=fake_run_obsidian):
        monkeypatch.setattr(sys, "argv", [
            "link_strategy.py", "find-orphans",
            "--workdir", str(tmp_path),
            "--vault", mock_vault,
            "--project", "OtherProject",
        ])
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(SCRIPTS / "link_strategy.py"), run_name="__main__")

    assert exc.value.code == 0


# ---------------------------------------------------------------------------
# Integration: full orphan → link workflow (mocked obsidian)
# ---------------------------------------------------------------------------

def test_full_orphan_fix_workflow(tmp_path, mock_vault, sample_orphan):
    """Simulate: find orphans → find candidates → append wikilinks."""
    candidates = [
        {
            "vault_path": "arch-projects/250507 COSMOS/SD/2025-05-09 visit.md",
            "event_date": "2025-05-09",
            "title": "2025-05-09 visit",
            "project": "250507 COSMOS",
            "file_type": "folder",
            "source_path": "/nas/visit",
        },
        {
            "vault_path": "arch-projects/250507 COSMOS/DD/2025-05-12 notes.md",
            "event_date": "2025-05-12",
            "title": "2025-05-12 notes",
            "project": "250507 COSMOS",
            "file_type": "docx",
            "source_path": "/nas/notes.docx",
        },
    ]

    with patch("link_strategy.obsidian_search") as mock_search:
        mock_search.return_value = candidates
        with patch("link_strategy.run_obsidian") as mock_run:
            mock_run.return_value = ("", "", 0)

            # Step 1: find candidates
            found = ls.find_linking_candidates(sample_orphan, tmp_path, mock_vault)
            assert len(found) == 2

            # Step 2: build section
            section = ls.build_related_notes_section(found, max_links=5)
            assert "## Related notes" in section
            assert "[[2025-05-09 visit]]" in section

            # Step 3: append
            ok = ls.append_related_notes(mock_vault, sample_orphan["vault_path"], section)
            assert ok is True


# ---------------------------------------------------------------------------
# Inter-event mesh (v15.0.0 — Issue 2 priorities 1c + 1d)
# ---------------------------------------------------------------------------


def _make_peer(date, name, subfolder, summary_hint="", parties=None):
    return {
        "event_date": date,
        "note_filename": name,
        "subfolder": subfolder,
        "summary_hint": summary_hint,
        "parties": list(parties or []),
    }


class TestTokenize:
    def test_strips_date_prefix(self):
        tokens = ls._tokenize_for_related("2024-08-15 SD review")
        assert "sd" not in tokens  # in the stop-token list
        assert "review" in tokens

    def test_keeps_cjk_characters_as_per_char_tokens(self):
        tokens = ls._tokenize_for_related("2023-02-27 施工图")
        assert "施" in tokens
        assert "工" in tokens
        assert "图" in tokens

    def test_drops_stop_tokens(self):
        tokens = ls._tokenize_for_related("2024-08-15 event meeting folder")
        # "event", "meeting", "folder" all stop-tokens → dropped.
        assert "event" not in tokens
        assert "meeting" not in tokens
        assert "folder" not in tokens

    def test_empty_input(self):
        assert ls._tokenize_for_related("") == set()
        assert ls._tokenize_for_related(None) == set()


class TestScoreEventPair:
    def test_cjk_topic_match_within_same_subfolder(self):
        a = _make_peer("2023-02-28", "2023-02-28 施工图", "CD")
        b = _make_peer("2023-02-27", "2023-02-27 施工图", "CD")
        # 3 shared CJK tokens (capped at 3) + same subfolder + near date.
        score = ls.score_event_pair(a, b)
        assert score >= ls._MIN_RELATED_SCORE
        # Reality check: three tokens capped * 5 = 15, + 3 subfolder, + date ≈ 21–22
        assert 18 <= score <= 25

    def test_self_pair_scores_zero(self):
        a = _make_peer("2023-02-28", "2023-02-28 施工图", "CD")
        assert ls.score_event_pair(a, a) == 0.0

    def test_different_subfolder_still_scores_if_topic_matches(self):
        a = _make_peer("2023-02-28", "2023-02-28 施工图", "CD")
        b = _make_peer("2023-02-27", "2023-02-27 施工图", "DD")
        score = ls.score_event_pair(a, b)
        # 3 tokens + date proximity, no subfolder bonus
        assert score > 0
        assert score < 25  # lower than the same-subfolder case

    def test_party_overlap_adds_score(self):
        a = _make_peer("2024-08-15", "2024-08-15 review", "SD", parties=["ClientCo"])
        b = _make_peer("2024-09-30", "2024-09-30 followup", "DD", parties=["ClientCo", "ArchFirm"])
        # Different subfolder, different tokens, different dates beyond window.
        # Only the party overlap contributes.
        score = ls.score_event_pair(a, b)
        assert score == pytest.approx(ls._RELATED_WEIGHT_PARTY_OVERLAP * 1)

    def test_pairs_with_no_signal_score_zero(self):
        a = _make_peer("2024-08-15", "2024-08-15 kickoff", "SD", parties=["A"])
        b = _make_peer("2025-12-31", "2025-12-31 unrelated", "CA", parties=["Z"])
        assert ls.score_event_pair(a, b) == 0.0


class TestFindRelatedEvents:
    def test_returns_top_k_sorted_by_score(self):
        current = _make_peer("2023-02-28", "2023-02-28 施工图", "CD")
        peers = [
            _make_peer("2023-02-27", "2023-02-27 施工图", "CD"),
            _make_peer("2023-03-17", "2023-03-17 施工图", "CD"),
            _make_peer("2023-03-21", "2023-03-21 施工图", "CD"),
            _make_peer("2023-01-15", "2023-01-15 kickoff", "SD"),
        ]
        related = ls.find_related_events(current, peers, k=2)
        assert len(related) == 2
        # Closest date wins.
        assert related[0][0]["note_filename"] == "2023-02-27 施工图"

    def test_filters_below_min_score(self):
        current = _make_peer("2024-08-15", "2024-08-15 kickoff", "SD")
        peers = [
            _make_peer("2025-12-31", "2025-12-31 unrelated", "CA"),
        ]
        assert ls.find_related_events(current, peers) == []

    def test_excludes_self(self):
        current = _make_peer("2023-02-28", "2023-02-28 施工图", "CD")
        related = ls.find_related_events(current, [current])
        assert related == []


class TestBuildRelatedSection:
    def test_empty_list_returns_empty_string(self):
        assert ls.build_event_related_section([]) == ""

    def test_renders_wikilinks_in_score_order(self):
        peers = [
            (_make_peer("2023-02-27", "2023-02-27 施工图", "CD"), 22.0),
            (_make_peer("2023-03-17", "2023-03-17 施工图", "CD"), 18.0),
        ]
        out = ls.build_event_related_section(peers)
        assert "## Related" in out
        assert out.index("[[2023-02-27 施工图]]") < out.index("[[2023-03-17 施工图]]")


class TestPrevNextInSubfolder:
    def test_returns_chronological_neighbours_in_same_subfolder(self):
        current = _make_peer("2023-02-28", "2023-02-28 施工图", "CD")
        peers = [
            _make_peer("2023-02-27", "2023-02-27 施工图", "CD"),
            _make_peer("2023-03-17", "2023-03-17 施工图", "CD"),
            _make_peer("2023-03-21", "2023-03-21 施工图", "CD"),
            _make_peer("2023-01-15", "2023-01-15 kickoff", "SD"),   # different subfolder
        ]
        prev, nxt = ls.find_prev_next_in_subfolder(current, peers)
        assert prev["note_filename"] == "2023-02-27 施工图"
        assert nxt["note_filename"] == "2023-03-17 施工图"

    def test_head_of_sequence_has_no_prev(self):
        current = _make_peer("2023-02-27", "2023-02-27 施工图", "CD")
        peers = [
            _make_peer("2023-02-28", "2023-02-28 施工图", "CD"),
            _make_peer("2023-03-17", "2023-03-17 施工图", "CD"),
        ]
        prev, nxt = ls.find_prev_next_in_subfolder(current, peers)
        assert prev is None
        assert nxt["note_filename"] == "2023-02-28 施工图"

    def test_tail_of_sequence_has_no_next(self):
        current = _make_peer("2023-03-21", "2023-03-21 施工图", "CD")
        peers = [
            _make_peer("2023-02-27", "2023-02-27 施工图", "CD"),
            _make_peer("2023-03-17", "2023-03-17 施工图", "CD"),
        ]
        prev, nxt = ls.find_prev_next_in_subfolder(current, peers)
        assert prev["note_filename"] == "2023-03-17 施工图"
        assert nxt is None

    def test_singleton_returns_none_none(self):
        current = _make_peer("2023-02-28", "2023-02-28 施工图", "CD")
        assert ls.find_prev_next_in_subfolder(current, []) == (None, None)


class TestBuildPrevNextSection:
    def test_empty_when_both_none(self):
        assert ls.build_prev_next_section(None, None, "CD") == ""

    def test_only_prev_renders_arrow_left(self):
        prev = _make_peer("2023-02-27", "2023-02-27 施工图", "CD")
        out = ls.build_prev_next_section(prev, None, "CD")
        assert "←" in out
        assert "[[2023-02-27 施工图]]" in out
        assert "→" not in out

    def test_both_render_both_arrows(self):
        prev = _make_peer("2023-02-27", "2023-02-27 施工图", "CD")
        nxt = _make_peer("2023-03-17", "2023-03-17 施工图", "CD")
        out = ls.build_prev_next_section(prev, nxt, "CD")
        assert "←" in out
        assert "→" in out
        assert out.index("Previous") < out.index("Next")
