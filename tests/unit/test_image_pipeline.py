"""Tests for scripts/image_pipeline.py — end-to-end image processing pipeline.

TDD: tests written BEFORE the implementation.

Cases:
1. Image source: transport.fetch → compress → vault_binary.write sequence
2. Container source (PDF): transport.fetch → extract → compress each → write each
3. Transport raises FileNotFoundError → errors populated, images_embedded: 0
4. vault_binary fails on 1 of 3 → 2 succeed, 1 error; images_embedded: 2
5. Wiki-embed format: ![[YYYY-MM-DD--source-stem--abc12345.jpg]]
6. source_images always contains the original archive_path (even on failure)
"""
import io
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import image_pipeline  # noqa: E402
import transport_loader  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal fixtures
# ---------------------------------------------------------------------------

def _make_jpeg_bytes() -> bytes:
    from PIL import Image
    img = Image.new("RGB", (100, 100), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _write_valid_transport(workdir: Path, archive_file: Path) -> None:
    """Write a transport.py that returns the given archive_file."""
    t = workdir / ".vault-bridge" / "transport.py"
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(
        "from pathlib import Path\n"
        f"def fetch_to_local(archive_path: str) -> Path:\n"
        f"    return Path('{archive_file}')\n"
    )


def _write_failing_transport(workdir: Path) -> None:
    """Write a transport.py that raises FileNotFoundError."""
    t = workdir / ".vault-bridge" / "transport.py"
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(
        "from pathlib import Path\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    raise FileNotFoundError(f'not found: {archive_path}')\n"
    )


def _success_vault_runner(cmd):
    """Fake runner that always succeeds for vault_binary writes."""
    import json
    result = mock.MagicMock()
    result.returncode = 0
    result.stdout = json.dumps({
        "ok": True,
        "bytes_written": 1000,
        "sha256": "abc12345",
        "vault_path": "test",
    })
    result.stderr = ""
    return result


def _failing_vault_runner(cmd):
    """Fake runner that always fails for vault_binary writes."""
    result = mock.MagicMock()
    result.returncode = 1
    result.stdout = ""
    result.stderr = "write failed"
    return result


# ---------------------------------------------------------------------------
# Test 1: Image source flows through transport → compress → vault_binary
# ---------------------------------------------------------------------------

def test_image_source_runs_full_pipeline(tmp_path):
    """JPEG archive source runs transport.fetch → compress → vault_binary.write."""
    # Setup archive file
    archive_file = tmp_path / "archive" / "2026-01-15 photo.jpg"
    archive_file.parent.mkdir(parents=True)
    archive_file.write_bytes(_make_jpeg_bytes())

    _write_valid_transport(tmp_path, archive_file)

    out_tempdir = tmp_path / "tmp_out"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=tmp_path,
        vault_name="MyVault",
        archive_path=str(archive_file),
        file_type="jpg",
        event_date="2026-01-15",
        project_vault_path="TestProject/SD",
        out_tempdir=out_tempdir,
        runner=_success_vault_runner,
    )

    assert result["images_embedded"] >= 1
    assert len(result["vault_wiki_embeds"]) >= 1
    assert len(result["attachments"]) >= 1
    assert not result["errors"]


def test_image_source_wiki_embed_format(tmp_path):
    """Wiki-embed entries use ![[filename.jpg]] format."""
    archive_file = tmp_path / "archive" / "2026-01-15 photo.jpg"
    archive_file.parent.mkdir(parents=True)
    archive_file.write_bytes(_make_jpeg_bytes())
    _write_valid_transport(tmp_path, archive_file)

    out_tempdir = tmp_path / "tmp_out"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=tmp_path,
        vault_name="MyVault",
        archive_path=str(archive_file),
        file_type="jpg",
        event_date="2026-01-15",
        project_vault_path="TestProject/SD",
        out_tempdir=out_tempdir,
        runner=_success_vault_runner,
    )

    for embed in result["vault_wiki_embeds"]:
        assert embed.startswith("![["), f"Expected wiki-embed format, got: {embed!r}"
        assert embed.endswith("]]"), f"Expected wiki-embed format, got: {embed!r}"
        assert ".jpg" in embed, f"Expected .jpg in wiki-embed, got: {embed!r}"


def test_source_images_contains_archive_path(tmp_path):
    """source_images always contains the original archive_path."""
    archive_path = "/fake/archive/photo.jpg"
    _write_failing_transport(tmp_path)

    out_tempdir = tmp_path / "tmp_out"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=tmp_path,
        vault_name="MyVault",
        archive_path=archive_path,
        file_type="jpg",
        event_date="2026-01-15",
        project_vault_path="TestProject/SD",
        out_tempdir=out_tempdir,
        runner=_success_vault_runner,
    )

    assert archive_path in result["source_images"]


# ---------------------------------------------------------------------------
# Test 3: Transport failure
# ---------------------------------------------------------------------------

def test_transport_failure_populates_errors(tmp_path):
    """Transport raises FileNotFoundError → errors populated, images_embedded: 0."""
    _write_failing_transport(tmp_path)

    out_tempdir = tmp_path / "tmp_out"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=tmp_path,
        vault_name="MyVault",
        archive_path="/nonexistent/file.jpg",
        file_type="jpg",
        event_date="2026-01-15",
        project_vault_path="TestProject/SD",
        out_tempdir=out_tempdir,
        runner=_success_vault_runner,
    )

    assert result["images_embedded"] == 0
    assert len(result["errors"]) >= 1
    assert result["source_images"] == ["/nonexistent/file.jpg"]


# ---------------------------------------------------------------------------
# Test 4: Vault binary partial failure
# ---------------------------------------------------------------------------

def test_vault_binary_partial_failure(tmp_path):
    """vault_binary fails on 1 of 2 → 1 succeed, 1 error; images_embedded: 1."""
    # Create 2 distinct archive images
    archive_file1 = tmp_path / "archive" / "photo1.jpg"
    archive_file2 = tmp_path / "archive" / "photo2.jpg"
    archive_file1.parent.mkdir(parents=True)
    archive_file1.write_bytes(_make_jpeg_bytes())

    from PIL import Image
    img2 = Image.new("RGB", (80, 80), color=(0, 200, 100))
    buf2 = io.BytesIO()
    img2.save(buf2, "JPEG")
    archive_file2.write_bytes(buf2.getvalue())

    # Transport returns first file
    t = tmp_path / ".vault-bridge" / "transport.py"
    t.parent.mkdir(parents=True, exist_ok=True)
    t.write_text(
        "from pathlib import Path\n"
        f"_FILES = ['{archive_file1}', '{archive_file2}']\n"
        "_IDX = [0]\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    return Path(archive_path)\n"
    )

    call_count = [0]

    def partial_runner(cmd):
        import json
        call_count[0] += 1
        result = mock.MagicMock()
        if call_count[0] == 1:
            # First write succeeds
            result.returncode = 0
            result.stdout = json.dumps({
                "ok": True, "bytes_written": 1000, "sha256": "abc123", "vault_path": "p1",
            })
        else:
            # Second write fails
            result.returncode = 1
            result.stdout = ""
            result.stderr = "write failed"
        result.stderr = result.stderr if result.returncode != 0 else ""
        return result

    out_tempdir = tmp_path / "tmp_out"
    out_tempdir.mkdir()

    # Process two files by calling pipeline twice (one per archive path)
    result1 = image_pipeline.process_source_for_images(
        workdir=tmp_path,
        vault_name="MyVault",
        archive_path=str(archive_file1),
        file_type="jpg",
        event_date="2026-01-15",
        project_vault_path="TestProject",
        out_tempdir=out_tempdir,
        runner=partial_runner,
    )
    result2 = image_pipeline.process_source_for_images(
        workdir=tmp_path,
        vault_name="MyVault",
        archive_path=str(archive_file2),
        file_type="jpg",
        event_date="2026-01-15",
        project_vault_path="TestProject",
        out_tempdir=out_tempdir,
        runner=partial_runner,
    )

    # First should succeed, second should fail
    assert result1["images_embedded"] == 1
    assert result2["images_embedded"] == 0
    assert len(result2["errors"]) >= 1


# ---------------------------------------------------------------------------
# Test: container source goes through extract
# ---------------------------------------------------------------------------

def test_container_source_pdf_extracts_and_writes(tmp_path):
    """PDF source: transport.fetch → extract (returns []) → no images written."""
    # Use a blank PDF (no images) to test the flow without complex fixture
    from PyPDF2 import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)

    archive_file = tmp_path / "archive" / "document.pdf"
    archive_file.parent.mkdir(parents=True)
    archive_file.write_bytes(buf.getvalue())

    _write_valid_transport(tmp_path, archive_file)

    out_tempdir = tmp_path / "tmp_out"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=tmp_path,
        vault_name="MyVault",
        archive_path=str(archive_file),
        file_type="pdf",
        event_date="2026-01-15",
        project_vault_path="TestProject/SD",
        out_tempdir=out_tempdir,
        runner=_success_vault_runner,
    )

    # Blank PDF has no images → images_embedded: 0, no errors from transport
    assert result["images_embedded"] == 0
    assert str(archive_file) in result["source_images"]
    # No transport errors
    assert not any("transport" in e.lower() for e in result["errors"])


# ---------------------------------------------------------------------------
# Test: return dict structure
# ---------------------------------------------------------------------------

def test_return_dict_has_all_required_keys(tmp_path):
    """Return value has all required keys."""
    _write_failing_transport(tmp_path)
    out_tempdir = tmp_path / "tmp_out"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=tmp_path,
        vault_name="MyVault",
        archive_path="/fake/file.jpg",
        file_type="jpg",
        event_date="2026-01-15",
        project_vault_path="TestProject",
        out_tempdir=out_tempdir,
        runner=_success_vault_runner,
    )

    required_keys = {
        "source_images", "compressed_paths", "vault_wiki_embeds",
        "attachments", "images_embedded", "warnings", "errors",
    }
    missing = required_keys - set(result.keys())
    assert not missing, f"Missing keys in result: {missing}"
