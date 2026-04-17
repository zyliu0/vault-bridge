"""Tests for scripts/memory_report.py — per-scan memory reports."""
import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import local_config as lc  # noqa: E402
import memory_report as mr  # noqa: E402


@pytest.fixture
def workdir(tmp_path):
    lc.save_local_config(tmp_path, active_domain="arch-projects")
    return tmp_path


def test_write_report_creates_file_in_reports_dir(workdir):
    stats = {"counts": {"events": 3, "written": 3}}
    path = mr.write_report(workdir, "retro", stats)
    assert path.exists()
    assert path.parent == workdir / ".vault-bridge" / "reports"
    assert path.name.endswith("_retro.md")


def test_write_report_includes_scan_type_and_counts(workdir):
    stats = {"counts": {"events": 5, "written": 4, "skipped": 1}}
    path = mr.write_report(workdir, "heartbeat", stats)
    content = path.read_text()
    assert "# vault-bridge heartbeat-scan report" in content
    assert "events" in content and "5" in content
    assert "written" in content and "4" in content


def test_write_report_handles_warnings_and_errors(workdir):
    stats = {
        "warnings": ["ambiguous domain: /nas/misc"],
        "errors": ["validator failed on note X"],
    }
    path = mr.write_report(workdir, "retro", stats)
    content = path.read_text()
    assert "## Warnings" in content
    assert "ambiguous domain" in content
    assert "## Errors" in content
    assert "validator failed" in content


def test_write_report_truncates_long_notes_list(workdir):
    notes = [f"path/note-{i}.md" for i in range(75)]
    stats = {"notes_written": notes}
    path = mr.write_report(workdir, "retro", stats)
    content = path.read_text()
    assert "path/note-0.md" in content
    assert "path/note-49.md" in content
    # Over the truncation line of 50
    assert "and 25 more" in content


def test_write_report_rejects_invalid_scan_type(workdir):
    with pytest.raises(ValueError):
        mr.write_report(workdir, "not-a-scan", {})


def test_cli_requires_setup(tmp_path):
    """Running the CLI in an unconfigured workdir should exit non-zero."""
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_report.py"),
            "retro",
            "--workdir",
            str(tmp_path),
            "--stats-json",
            "{}",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "setup" in result.stderr.lower()


def test_cli_writes_report_with_stats_json(workdir):
    stats = {"counts": {"events": 2}, "notes": "dry run preview"}
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "memory_report.py"),
            "retro",
            "--workdir",
            str(workdir),
            "--stats-json",
            json.dumps(stats),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    out_path = Path(result.stdout.strip())
    assert out_path.exists()
    assert "dry run preview" in out_path.read_text()


# ---------------------------------------------------------------------------
# visualization scan type — new tests (Phase 1d)
# ---------------------------------------------------------------------------

def test_visualization_in_valid_scan_types():
    """'visualization' must be a member of VALID_SCAN_TYPES."""
    assert "visualization" in mr.VALID_SCAN_TYPES


def test_write_report_visualization_filename_pattern(workdir):
    """write_report with scan_type='visualization' produces a *_visualization.md filename."""
    stats = {"counts": {"files_written": 1}}
    path = mr.write_report(workdir, "visualization", stats)
    assert path.exists()
    assert path.name.endswith("_visualization.md")
    assert path.parent == workdir / ".vault-bridge" / "reports"


def test_write_report_visualization_renders_stats(workdir):
    """visualization-specific stats keys appear in the rendered body."""
    stats = {
        "visualization_type": "canvas",
        "source_description": "Kickoff",
        "vault_path": "2408 Sample/",
    }
    path = mr.write_report(workdir, "visualization", stats)
    content = path.read_text()
    assert "canvas" in content
    assert "Kickoff" in content
    assert "2408 Sample/" in content


# ---------------------------------------------------------------------------
# research scan type — new tests (Phase 1h)
# ---------------------------------------------------------------------------

def test_research_in_valid_scan_types():
    """'research' must be a member of VALID_SCAN_TYPES."""
    assert "research" in mr.VALID_SCAN_TYPES


def test_write_report_research_filename_pattern(workdir):
    """write_report with scan_type='research' produces a *_research.md filename."""
    stats = {"topic": "OpenAI", "goal": "Understand mission"}
    path = mr.write_report(workdir, "research", stats)
    assert path.exists()
    assert path.name.endswith("_research.md")
    assert path.parent == workdir / ".vault-bridge" / "reports"


def test_write_report_research_renders_topic_goal_chinese_mode(workdir):
    """research-specific stats keys topic, goal, chinese_mode render in body."""
    stats = {
        "topic": "ByteDance",
        "goal": "Competitive analysis",
        "chinese_mode": True,
    }
    path = mr.write_report(workdir, "research", stats)
    content = path.read_text()
    assert "ByteDance" in content
    assert "Competitive analysis" in content
    assert "True" in content or "true" in content.lower()


# ---------------------------------------------------------------------------
# probe scan type — new tests (Phase 4b)
# ---------------------------------------------------------------------------

def test_probe_in_valid_scan_types():
    """'probe' must be a member of VALID_SCAN_TYPES."""
    assert "probe" in mr.VALID_SCAN_TYPES


def test_write_report_probe_filename_pattern(workdir):
    """write_report with scan_type='probe' produces a *_probe.md filename."""
    stats = {}
    path = mr.write_report(workdir, "probe", stats)
    assert path.exists()
    assert path.name.endswith("_probe.md")


def test_write_report_probe_renders_check_list(workdir):
    """probe_results list is rendered with check names and PASS/FAIL status."""
    stats = {
        "probe_results": [
            {"name": "check_transport_fetch", "ok": True, "detail": "fetched OK"},
            {"name": "check_compress", "ok": False, "detail": "compress failed"},
        ]
    }
    path = mr.write_report(workdir, "probe", stats)
    content = path.read_text()
    assert "check_transport_fetch" in content
    assert "PASS" in content
    assert "check_compress" in content
    assert "FAIL" in content
