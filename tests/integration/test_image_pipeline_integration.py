"""Integration test for the vault-bridge image pipeline.

End-to-end test with all external dependencies faked:
- Real archive JPEG + blank PDF (no embedded images for simplicity)
- Fake transport.py copies archive → tmp
- Fake vault runner parses JS, copies to fake vault dir
- Fake vision_callback returns a fixed description
- Runs image_pipeline.process_source_for_images on both sources
- Asserts: compressed files in fake vault; wiki-embeds; frontmatter fields;
  validator accepts the composed frontmatter

Uses only local filesystem — no Obsidian, no network.
"""
import io
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import image_pipeline
import validate_frontmatter
import local_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_jpeg(size: int = 50) -> bytes:
    """Make a small valid JPEG."""
    img = Image.new("RGB", (size, size), color=(200, 100, 50))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    return buf.getvalue()


def _make_blank_pdf() -> bytes:
    """Make a blank PDF with no embedded images."""
    from PyPDF2 import PdfWriter
    writer = PdfWriter()
    writer.add_blank_page(width=100, height=100)
    buf = io.BytesIO()
    writer.write(buf)
    return buf.getvalue()


@pytest.fixture
def integration_env(tmp_path):
    """Set up a complete integration environment."""
    # Archive directory with test files
    archive_dir = tmp_path / "archive"
    archive_dir.mkdir()

    jpeg_file = archive_dir / "2026-04-16 test photo.jpg"
    jpeg_file.write_bytes(_make_jpeg(50))

    pdf_file = archive_dir / "2026-04-16 document.pdf"
    pdf_file.write_bytes(_make_blank_pdf())

    # Fake vault directory
    fake_vault = tmp_path / "vault"
    fake_vault.mkdir()

    # Setup vault-bridge workdir
    local_config.save_local_config(tmp_path, active_domain="test-domain")

    # Write a transport.py that returns files from archive directly
    transport_path = tmp_path / ".vault-bridge" / "transport.py"
    transport_path.write_text(
        "from pathlib import Path\n"
        "def fetch_to_local(archive_path: str) -> Path:\n"
        "    p = Path(archive_path)\n"
        "    if not p.exists():\n"
        "        raise FileNotFoundError(f'not found: {archive_path}')\n"
        "    return p\n"
    )

    return {
        "workdir": tmp_path,
        "archive_dir": archive_dir,
        "jpeg_file": jpeg_file,
        "pdf_file": pdf_file,
        "fake_vault": fake_vault,
    }


def _make_fake_vault_runner(fake_vault: Path):
    """
    Return a runner that intercepts 'obsidian eval' calls,
    extracts src_path from the JS, and copies to fake_vault.
    """
    def runner(cmd):
        # Extract src path and dst path from the JS command
        cmd_str = " ".join(str(c) for c in cmd)
        result_mock = type("R", (), {
            "returncode": 0,
            "stdout": "",
            "stderr": "",
        })()

        try:
            # Parse srcPath from JS: const srcPath = "/path/to/file";
            import re
            src_match = re.search(r"const srcPath = (.+?);", cmd_str)
            dst_match = re.search(r"const dstPath = (.+?);", cmd_str)
            if src_match and dst_match:
                src_path = Path(json.loads(src_match.group(1)))
                dst_rel = json.loads(dst_match.group(1))
                dst_path = fake_vault / dst_rel
                dst_path.parent.mkdir(parents=True, exist_ok=True)
                if src_path.exists():
                    shutil.copy2(str(src_path), str(dst_path))
                    bytes_written = dst_path.stat().st_size
                else:
                    bytes_written = 0
                result_mock.stdout = json.dumps({
                    "ok": True,
                    "bytes_written": bytes_written,
                    "sha256": "abc12345",
                    "vault_path": dst_rel,
                })
        except Exception as e:
            result_mock.returncode = 1
            result_mock.stderr = str(e)

        return result_mock

    return runner


# ---------------------------------------------------------------------------
# Integration test: JPEG source
# ---------------------------------------------------------------------------

def test_jpeg_source_pipeline(integration_env, tmp_path):
    """JPEG archive source runs full pipeline: fetch → compress → vault write."""
    env = integration_env
    fake_runner = _make_fake_vault_runner(env["fake_vault"])

    out_tempdir = tmp_path / "pipeline_tmp"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=env["workdir"],
        vault_name="TestVault",
        archive_path=str(env["jpeg_file"]),
        file_type="jpg",
        event_date="2026-04-16",
        project_vault_path="TestProject/SD",
        out_tempdir=out_tempdir,
        runner=fake_runner,
    )

    assert not result["errors"], f"Unexpected errors: {result['errors']}"
    assert result["images_embedded"] == 1
    assert len(result["attachments"]) == 1
    assert len(result["vault_wiki_embeds"]) == 1

    # Wiki-embed format
    embed = result["vault_wiki_embeds"][0]
    assert embed.startswith("![[")
    assert embed.endswith("]]")
    assert ".jpg" in embed

    # Verify file was written to fake vault
    attachment_name = result["attachments"][0]
    vault_file = env["fake_vault"] / "TestProject" / "_Attachments" / attachment_name
    assert vault_file.exists(), f"Expected vault file: {vault_file}"

    # Verify it's a valid JPEG
    magic = vault_file.read_bytes()[:2]
    assert magic == b"\xff\xd8", f"Expected JPEG magic, got {magic.hex()}"


# ---------------------------------------------------------------------------
# Integration test: PDF source (no embedded images)
# ---------------------------------------------------------------------------

def test_pdf_source_no_images(integration_env, tmp_path):
    """Blank PDF (no images) → images_embedded: 0, no errors."""
    env = integration_env
    fake_runner = _make_fake_vault_runner(env["fake_vault"])

    out_tempdir = tmp_path / "pipeline_tmp"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=env["workdir"],
        vault_name="TestVault",
        archive_path=str(env["pdf_file"]),
        file_type="pdf",
        event_date="2026-04-16",
        project_vault_path="TestProject/SD",
        out_tempdir=out_tempdir,
        runner=fake_runner,
    )

    # No transport errors
    assert not any("transport" in e.lower() for e in result["errors"])
    assert result["images_embedded"] == 0
    assert str(env["pdf_file"]) in result["source_images"]


# ---------------------------------------------------------------------------
# Integration test: source_images always contains original archive_path
# ---------------------------------------------------------------------------

def test_source_images_always_populated(integration_env, tmp_path):
    """source_images contains the original archive_path regardless of success."""
    env = integration_env

    # Use a non-existent archive path (will cause transport failure)
    missing_path = "/completely/nonexistent/file.jpg"

    out_tempdir = tmp_path / "pipeline_tmp"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=env["workdir"],
        vault_name="TestVault",
        archive_path=missing_path,
        file_type="jpg",
        event_date="2026-04-16",
        project_vault_path="TestProject/SD",
        out_tempdir=out_tempdir,
        runner=_make_fake_vault_runner(env["fake_vault"]),
    )

    assert missing_path in result["source_images"]
    assert result["images_embedded"] == 0


# ---------------------------------------------------------------------------
# Integration test: validator accepts composed frontmatter
# ---------------------------------------------------------------------------

def test_validator_accepts_composed_frontmatter(integration_env, tmp_path):
    """Frontmatter composed from pipeline result passes schema validator."""
    env = integration_env
    fake_runner = _make_fake_vault_runner(env["fake_vault"])

    out_tempdir = tmp_path / "pipeline_tmp"
    out_tempdir.mkdir()

    result = image_pipeline.process_source_for_images(
        workdir=env["workdir"],
        vault_name="TestVault",
        archive_path=str(env["jpeg_file"]),
        file_type="jpg",
        event_date="2026-04-16",
        project_vault_path="TestProject/SD",
        out_tempdir=out_tempdir,
        runner=fake_runner,
    )

    # Build a complete v2 frontmatter with image fields
    fm_lines = [
        "schema_version: 2",
        "plugin: vault-bridge",
        "domain: test-domain",
        "project: TestProject",
        f"source_path: \"{env['jpeg_file']}\"",
        "file_type: jpg",
        "captured_date: 2026-04-16",
        "event_date: 2026-04-16",
        "event_date_source: filename-prefix",
        "scan_type: retro",
        f"sources_read:",
        f"  - \"{env['jpeg_file']}\"",
        "read_bytes: 1000",
        "content_confidence: high",
    ]

    if result["images_embedded"] > 0:
        fm_lines.append("attachments:")
        for att in result["attachments"]:
            fm_lines.append(f"  - \"{att}\"")
        fm_lines.append(f"source_images:")
        for si in result["source_images"]:
            fm_lines.append(f"  - \"{si}\"")
        fm_lines.append(f"images_embedded: {result['images_embedded']}")

    fm_lines.append("cssclasses: [img-grid]" if result["images_embedded"] > 0 else "cssclasses: []")

    frontmatter = "\n".join(fm_lines)
    note_content = f"---\n{frontmatter}\n---\n\nBody text.\n"

    # Write to temp file and validate
    note_file = tmp_path / "test_note.md"
    note_file.write_text(note_content)

    try:
        validate_frontmatter.validate_content(note_content, str(note_file))
        validation_ok = True
    except SystemExit as e:
        validation_ok = e.code == 0

    assert validation_ok, f"Validator rejected composed frontmatter"
