"""Tests for scripts/setup_probe.py — 6-step capability probe for /vault-bridge:setup.

TDD: tests written BEFORE the implementation.

Cases:
1. All 6 checks pass → ok: True
2. Check 2 (transport fetch) fails → ok: False, subsequent checks not run
3. Check 4 (extract) skipped when no container sample → ok: True, detail: "skipped"
4. Vision callback returns empty string → check 5 fails
5. Memory report written with 'probe' scan_type
6. All check details surface in report
"""
import io
import json
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import local_config  # noqa: E402
import setup_probe  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_jpeg_bytes() -> bytes:
    from PIL import Image
    img = Image.new("RGB", (50, 50), color=(100, 150, 200))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _make_workdir(tmp_path: Path) -> Path:
    """Create a properly initialized workdir."""
    local_config.save_local_config(tmp_path, active_domain="test-domain")
    return tmp_path


def _make_archive_file(tmp_path: Path) -> Path:
    """Create a real JPEG on disk as a fake archive file."""
    archive = tmp_path / "archive" / "test_photo.jpg"
    archive.parent.mkdir(parents=True)
    archive.write_bytes(_make_jpeg_bytes())
    return archive


def _write_valid_transport(workdir: Path, archive_file: Path) -> None:
    t = workdir / ".vault-bridge" / "transport.py"
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(
        "from pathlib import Path\n"
        f"def fetch_to_local(archive_path: str) -> Path:\n"
        f"    return Path('{archive_file}')\n"
    )


def _success_vault_runner(cmd):
    """Fake vault runner that always succeeds."""
    result = mock.MagicMock()
    result.returncode = 0
    probe_bytes = setup_probe._PROBE_PNG_SIZE
    result.stdout = json.dumps({
        "ok": True,
        "bytes_written": probe_bytes,
        "sha256": "abc123",
        "vault_path": "_Attachments/_probe/test.png",
    })
    result.stderr = ""
    return result


def _failing_vault_runner(cmd):
    result = mock.MagicMock()
    result.returncode = 1
    result.stdout = ""
    result.stderr = "vault error"
    return result


def _good_vision(jpeg_path: Path) -> str:
    return "A photograph of a colorful test image with bright colors."


def _empty_vision(jpeg_path: Path) -> str:
    return ""


def _short_vision(jpeg_path: Path) -> str:
    return "Small."


# ---------------------------------------------------------------------------
# Test 1: All 6 checks pass
# ---------------------------------------------------------------------------

def test_all_checks_pass_returns_ok_true(tmp_path):
    """When all checks succeed, run_probe returns {ok: True}."""
    workdir = _make_workdir(tmp_path)
    archive_file = _make_archive_file(tmp_path)
    _write_valid_transport(workdir, archive_file)

    result = setup_probe.run_probe(
        workdir=workdir,
        vault_name="MyVault",
        sample_archive_paths=[str(archive_file)],
        sample_container_path=None,
        vision_callback=_good_vision,
        runner=_success_vault_runner,
    )
    assert result["ok"] is True


def test_all_checks_have_expected_names(tmp_path):
    """The 6 check names are present in result['checks']."""
    workdir = _make_workdir(tmp_path)
    archive_file = _make_archive_file(tmp_path)
    _write_valid_transport(workdir, archive_file)

    result = setup_probe.run_probe(
        workdir=workdir,
        vault_name="MyVault",
        sample_archive_paths=[str(archive_file)],
        sample_container_path=None,
        vision_callback=_good_vision,
        runner=_success_vault_runner,
    )
    check_names = [c["name"] for c in result["checks"]]
    for expected in [
        "check_obsidian_binary_write",
        "check_transport_fetch",
        "check_compress",
        "check_extract",
        "check_vision",
        "check_vault_write_full",
    ]:
        assert expected in check_names, f"Missing check: {expected}"


# ---------------------------------------------------------------------------
# Test 2: Transport fetch failure stops subsequent checks
# ---------------------------------------------------------------------------

def test_transport_fetch_failure_stops_subsequent_checks(tmp_path):
    """Check 2 fails → ok: False, subsequent checks should not run."""
    workdir = _make_workdir(tmp_path)
    # No transport.py — will cause TransportMissing in check_transport_fetch

    result = setup_probe.run_probe(
        workdir=workdir,
        vault_name="MyVault",
        sample_archive_paths=["/nonexistent/file.jpg"],
        sample_container_path=None,
        vision_callback=_good_vision,
        runner=_success_vault_runner,
    )
    assert result["ok"] is False
    # Check 2 should have ok=False
    checks_by_name = {c["name"]: c for c in result["checks"]}
    assert checks_by_name["check_transport_fetch"]["ok"] is False
    # Subsequent checks (compress, vision, vault_write_full) should not have ok=True
    # They may be absent or have ok=False
    for name in ["check_compress", "check_vision", "check_vault_write_full"]:
        if name in checks_by_name:
            assert checks_by_name[name]["ok"] is not True, (
                f"Check {name} should not pass when transport fails"
            )


# ---------------------------------------------------------------------------
# Test 3: Check 4 skipped when no container sample
# ---------------------------------------------------------------------------

def test_extract_check_skipped_when_no_container_sample(tmp_path):
    """sample_container_path=None → check_extract: ok=True, detail='skipped'."""
    workdir = _make_workdir(tmp_path)
    archive_file = _make_archive_file(tmp_path)
    _write_valid_transport(workdir, archive_file)

    result = setup_probe.run_probe(
        workdir=workdir,
        vault_name="MyVault",
        sample_archive_paths=[str(archive_file)],
        sample_container_path=None,
        vision_callback=_good_vision,
        runner=_success_vault_runner,
    )
    checks_by_name = {c["name"]: c for c in result["checks"]}
    extract_check = checks_by_name.get("check_extract")
    assert extract_check is not None
    assert extract_check["ok"] is True
    assert "skipped" in extract_check["detail"].lower()


# ---------------------------------------------------------------------------
# Test 4: Vision callback returns empty string → check 5 fails
# ---------------------------------------------------------------------------

def test_vision_empty_string_fails_check(tmp_path):
    """Vision callback returning empty string → check_vision fails → ok: False."""
    workdir = _make_workdir(tmp_path)
    archive_file = _make_archive_file(tmp_path)
    _write_valid_transport(workdir, archive_file)

    result = setup_probe.run_probe(
        workdir=workdir,
        vault_name="MyVault",
        sample_archive_paths=[str(archive_file)],
        sample_container_path=None,
        vision_callback=_empty_vision,
        runner=_success_vault_runner,
    )
    checks_by_name = {c["name"]: c for c in result["checks"]}
    vision_check = checks_by_name.get("check_vision")
    assert vision_check is not None
    assert vision_check["ok"] is False


def test_vision_too_short_fails_check(tmp_path):
    """Vision callback returning < 10 chars → check_vision fails."""
    workdir = _make_workdir(tmp_path)
    archive_file = _make_archive_file(tmp_path)
    _write_valid_transport(workdir, archive_file)

    result = setup_probe.run_probe(
        workdir=workdir,
        vault_name="MyVault",
        sample_archive_paths=[str(archive_file)],
        sample_container_path=None,
        vision_callback=_short_vision,
        runner=_success_vault_runner,
    )
    checks_by_name = {c["name"]: c for c in result["checks"]}
    # "Small." is 6 chars < 10 → fail
    vision_check = checks_by_name.get("check_vision")
    assert vision_check is not None
    assert vision_check["ok"] is False


# ---------------------------------------------------------------------------
# Test 5: Memory report written with 'probe' scan_type
# ---------------------------------------------------------------------------

def test_memory_report_written_with_probe_scan_type(tmp_path):
    """run_probe writes a memory report with scan_type 'probe'."""
    workdir = _make_workdir(tmp_path)
    archive_file = _make_archive_file(tmp_path)
    _write_valid_transport(workdir, archive_file)

    result = setup_probe.run_probe(
        workdir=workdir,
        vault_name="MyVault",
        sample_archive_paths=[str(archive_file)],
        sample_container_path=None,
        vision_callback=_good_vision,
        runner=_success_vault_runner,
    )
    # Report path is in result
    assert "report_path" in result
    report_path = Path(result["report_path"])
    assert report_path.exists()
    assert "_probe.md" in report_path.name


# ---------------------------------------------------------------------------
# Test 6: Check details surface in report
# ---------------------------------------------------------------------------

def test_check_details_appear_in_report(tmp_path):
    """Probe results (check names and details) appear in the memory report."""
    workdir = _make_workdir(tmp_path)
    archive_file = _make_archive_file(tmp_path)
    _write_valid_transport(workdir, archive_file)

    result = setup_probe.run_probe(
        workdir=workdir,
        vault_name="MyVault",
        sample_archive_paths=[str(archive_file)],
        sample_container_path=None,
        vision_callback=_good_vision,
        runner=_success_vault_runner,
    )
    report_path = Path(result["report_path"])
    content = report_path.read_text()
    # At least one check name should appear in the report
    assert "check_transport_fetch" in content or "check_compress" in content


# ---------------------------------------------------------------------------
# Result structure
# ---------------------------------------------------------------------------

def test_run_probe_result_has_required_keys(tmp_path):
    """run_probe result contains all required keys."""
    workdir = _make_workdir(tmp_path)
    archive_file = _make_archive_file(tmp_path)
    _write_valid_transport(workdir, archive_file)

    result = setup_probe.run_probe(
        workdir=workdir,
        vault_name="MyVault",
        sample_archive_paths=[str(archive_file)],
        sample_container_path=None,
        vision_callback=_good_vision,
        runner=_success_vault_runner,
    )
    for key in ["ok", "checks", "sample_used", "vision_description", "report_path"]:
        assert key in result, f"Missing key: {key}"
