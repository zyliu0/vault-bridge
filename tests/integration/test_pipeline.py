"""End-to-end integration test for the vault-bridge Python pipeline.

This test exercises every component in Phase A + B working together against
a hand-crafted fixture project:

1. Parse the user's CLAUDE.md config (parse_config.py)
2. Build a fixture project on the filesystem
3. For each fixture event:
   - Compute event_date (extract_event_date.py)
   - Compute fingerprint (fingerprint.py)
   - Route via config patterns
   - Compress any images (compress_images.py)
   - Build frontmatter in canonical order
   - Write the note to a temp vault
   - Validate the note (validate_frontmatter.py)
   - Append to the scan index (vault_scan.py)
4. Simulate a second scan — every event should lookup as "skip"
5. Simulate a folder rename — the renamed event should lookup as "rename"

The LLM layer (writing the diary body) is NOT tested in CI — that's the
responsibility of the live Claude session. This test covers everything
that doesn't need Claude.
"""
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import compress_images  # noqa: E402
import extract_event_date  # noqa: E402
import fingerprint  # noqa: E402
import parse_config  # noqa: E402
import vault_scan  # noqa: E402
from schema import FIELD_ORDER, LITERAL_VALUES  # noqa: E402


FIXTURE_DIR = Path(__file__).parent / "fixtures" / "sample-project"


# ---------------------------------------------------------------------------
# Fixture builder — creates a small real project on disk
# ---------------------------------------------------------------------------

def _backdate(path: Path, iso_date: str) -> None:
    """Set a file or directory's mtime/atime to match an ISO date.

    Fixture files are created "now" so their mtime always diverges from any
    embedded filename prefix date. The extract_event_date conflict rule
    (>7 days → use mtime) would then fire on every fixture event, making
    it impossible to test the filename-prefix priority. Backdating sidesteps
    this so tests exercise the rule they mean to.
    """
    ts = datetime.fromisoformat(iso_date).timestamp()
    os.utime(path, (ts, ts))


@pytest.fixture
def fixture_source(tmp_path):
    """Build a realistic source archive under tmp_path.

    Layout mimics a small architecture project:
      sample-project/
      ├── 240909 memo.pdf              (standalone date-stamped PDF → SD)
      ├── 2024-10-15 CD drawings/      (date folder, CD phase → CD)
      │   ├── plan-01.pdf
      │   └── elevation-02.pdf
      ├── site-photos/                 (>10 images → image-folder event)
      │   ├── photo-001.jpg
      │   ├── photo-002.jpg
      │   └── ... (12 total)
      ├── 251001 meeting memo.pdf      (→ Meetings via content override)
      └── model.dwg                    (unreadable → metadata-only)

    Each file's mtime is backdated to match its filename prefix so the
    extract_event_date conflict rule does not fire on the fixture.
    """
    source = tmp_path / "sample-project"
    source.mkdir()

    # Standalone PDF with date prefix (backdated to 2024-09-09)
    memo = source / "240909 memo.pdf"
    memo.write_bytes(b"%PDF-1.4\n%fake pdf content\n")
    _backdate(memo, "2024-09-09")

    # Date-stamped folder with two PDFs inside (backdated to 2024-10-15)
    cd_folder = source / "2024-10-15 CD drawings"
    cd_folder.mkdir()
    (cd_folder / "plan-01.pdf").write_bytes(b"%PDF-1.4\nplan\n")
    (cd_folder / "elevation-02.pdf").write_bytes(b"%PDF-1.4\nelevation\n")
    _backdate(cd_folder / "plan-01.pdf", "2024-10-15")
    _backdate(cd_folder / "elevation-02.pdf", "2024-10-15")
    _backdate(cd_folder, "2024-10-15")

    # Image folder with 12 images (triggers >10 sampling rule)
    photos = source / "site-photos"
    photos.mkdir()
    for i in range(12):
        img = Image.new("RGB", (600, 400), (i * 20, 100, 200))
        img.save(photos / f"photo-{i:03d}.jpg", "JPEG")

    # PDF that should route to Meetings via content override (backdated)
    meeting = source / "251001 meeting memo.pdf"
    meeting.write_bytes(b"%PDF-1.4\nmeeting\n")
    _backdate(meeting, "2025-10-01")

    # DWG file (metadata-only event)
    (source / "model.dwg").write_bytes(b"\x00\x01fake dwg\x00\x00")

    return source


@pytest.fixture
def fixture_vault(tmp_path):
    """An empty vault destination."""
    vault = tmp_path / "vault" / "sample-project"
    vault.mkdir(parents=True)
    return vault


@pytest.fixture
def fixture_state_dir(tmp_path, monkeypatch):
    state = tmp_path / "vault-bridge-state"
    state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(state))
    return state


@pytest.fixture
def fixture_claude_md(tmp_path, fixture_source):
    """A CLAUDE.md with a vault-bridge config block pointing at fixture_source."""
    claude_md = tmp_path / "CLAUDE.md"
    # Point root_path at the real fixture folder (not /tmp hardcoded)
    claude_md.write_text(f"""## vault-bridge: configuration

```yaml
version: 1

file_system:
  type: local-path
  root_path: {fixture_source}
  access_pattern: "Use Read and Glob tools for all file reads."

routing:
  patterns:
    - match: "CD"
      subfolder: CD
    - match: "SD"
      subfolder: SD
  fallback: Admin

skip_patterns:
  - ".DS_Store"
  - "Thumbs.db"
```
""")
    return claude_md


# ---------------------------------------------------------------------------
# Step 1: config parsing
# ---------------------------------------------------------------------------

def test_pipeline_1_config_parses(fixture_claude_md):
    """The fixture CLAUDE.md config block must parse successfully."""
    config = parse_config.parse_config(str(fixture_claude_md))
    assert config["version"] == 1
    assert config["file_system"]["type"] == "local-path"
    assert len(config["routing"]["patterns"]) == 2
    assert config["routing"]["fallback"] == "Admin"


# ---------------------------------------------------------------------------
# Step 2: event detection + event_date extraction
# ---------------------------------------------------------------------------

def test_pipeline_2_standalone_pdf_event_date(fixture_source):
    """240909 memo.pdf should extract event_date from filename prefix."""
    pdf = fixture_source / "240909 memo.pdf"
    event_date, source = extract_event_date.extract_event_date(
        filename=pdf.name,
        parent_folder_name=fixture_source.name,
        mtime_unix=pdf.stat().st_mtime,
    )
    assert event_date == "2024-09-09"
    assert source == "filename-prefix"


def test_pipeline_2_date_folder_event_date(fixture_source):
    """2024-10-15 CD drawings/ should extract from folder name prefix."""
    folder = fixture_source / "2024-10-15 CD drawings"
    event_date, source = extract_event_date.extract_event_date(
        filename=folder.name,
        parent_folder_name=fixture_source.name,
        mtime_unix=folder.stat().st_mtime,
    )
    assert event_date == "2024-10-15"
    assert source == "filename-prefix"


# ---------------------------------------------------------------------------
# Step 3: fingerprinting
# ---------------------------------------------------------------------------

def test_pipeline_3_folder_fingerprint_is_stable(fixture_source):
    """Same folder contents → same fingerprint on repeat calls."""
    folder = fixture_source / "2024-10-15 CD drawings"
    fp1 = fingerprint.fingerprint_folder(folder)
    fp2 = fingerprint.fingerprint_folder(folder)
    assert fp1 == fp2
    assert len(fp1) == 16


def test_pipeline_3_file_fingerprint(fixture_source):
    """Standalone PDF gets a file fingerprint."""
    pdf = fixture_source / "240909 memo.pdf"
    fp = fingerprint.fingerprint_file(pdf)
    assert len(fp) == 16


def test_pipeline_3_rename_detected_by_fingerprint(fixture_source):
    """The rename detection scenario: copy a folder to a new name,
    fingerprint should be identical, lookup should return 'rename'."""
    folder_old = fixture_source / "2024-10-15 CD drawings"
    fp_old = fingerprint.fingerprint_folder(folder_old)

    folder_new = fixture_source / "2024-10-15 CD drawings v2"
    folder_new.mkdir()
    for child in folder_old.iterdir():
        (folder_new / child.name).write_bytes(child.read_bytes())

    fp_new = fingerprint.fingerprint_folder(folder_new)
    assert fp_new == fp_old


# ---------------------------------------------------------------------------
# Step 4: image compression
# ---------------------------------------------------------------------------

def test_pipeline_4_compress_and_dedup(fixture_source, fixture_vault):
    """Compress one image, confirm the naming, confirm de-dup on re-run."""
    photo = fixture_source / "site-photos" / "photo-000.jpg"
    attachments = fixture_vault / "_Attachments"

    result1 = compress_images.compress_image(
        src_path=photo,
        out_dir=attachments,
        event_date="2024-09-09",
    )
    assert result1.exists()
    assert result1.suffix == ".jpg"
    # Filename format: YYYY-MM-DD--{stem}--{hash8}.jpg
    parts = result1.stem.split("--")
    assert parts[0] == "2024-09-09"
    assert parts[1] == "photo-000"
    assert len(parts[2]) == 8

    # Second call should de-dup (same bytes → same filename → no-op)
    result2 = compress_images.compress_image(
        src_path=photo,
        out_dir=attachments,
        event_date="2024-09-09",
    )
    assert result2 == result1


# ---------------------------------------------------------------------------
# Step 5: end-to-end note write + validation
# ---------------------------------------------------------------------------

def test_pipeline_5_note_write_and_validate(fixture_source, fixture_vault, fixture_state_dir):
    """Build a Template A note for the standalone PDF, write it, validate it."""
    pdf = fixture_source / "240909 memo.pdf"

    # Compute the frontmatter fields
    event_date, event_date_source = extract_event_date.extract_event_date(
        filename=pdf.name,
        parent_folder_name=fixture_source.name,
        mtime_unix=pdf.stat().st_mtime,
    )
    fp = fingerprint.fingerprint_file(pdf)

    # Build the note in canonical order
    frontmatter_lines = [
        "schema_version: 2",
        "plugin: vault-bridge",
        "domain: test-domain",
        'project: "sample-project"',
        f'source_path: "{pdf}"',
        "file_type: pdf",
        f"captured_date: {datetime.now().date().isoformat()}",
        f"event_date: {event_date}",
        f"event_date_source: {event_date_source}",
        "scan_type: retro",
        "sources_read:",
        f'  - "{pdf}"',
        f"read_bytes: {pdf.stat().st_size}",
        "content_confidence: high",
        "cssclasses: []",
    ]

    body = (
        "Read the 240909 memo PDF. This is a fixture test body paragraph — "
        "in a real scan it would contain grounded content from the extracted "
        "text, not invented specifics.\n"
        f"\nNAS: `{pdf}`\n"
    )

    note_path = fixture_vault / "Admin" / "2024-09-09 memo.md"
    note_path.parent.mkdir(parents=True, exist_ok=True)
    note_path.write_text(
        "---\n" + "\n".join(frontmatter_lines) + "\n---\n\n" + body
    )

    # Validate the note — must exit 0
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "validate_frontmatter.py"), str(note_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Validator rejected a valid fixture note:\n{result.stderr}"
    )


# ---------------------------------------------------------------------------
# Step 6: idempotency via the scan index
# ---------------------------------------------------------------------------

def test_pipeline_6_idempotency_skip_on_second_scan(fixture_source, fixture_state_dir):
    """First scan records the event; second scan sees it as 'skip'."""
    pdf = fixture_source / "240909 memo.pdf"
    fp = fingerprint.fingerprint_file(pdf)

    # First scan: append
    vault_scan.append_index(str(pdf), fp, "vault/Admin/2024-09-09 memo.md")

    # Second scan: load, lookup
    by_path, by_fp = vault_scan.load_index()
    decision = vault_scan.lookup_event(
        source_path=str(pdf),
        fingerprint=fp,
        index_by_path=by_path,
        index_by_fp=by_fp,
    )
    assert decision.action == "skip"
    assert decision.existing_note_path == "vault/Admin/2024-09-09 memo.md"


def test_pipeline_6_rename_detected_by_index(fixture_source, fixture_state_dir):
    """First scan recorded the old path; second scan with renamed path + same
    fingerprint returns 'rename'."""
    folder_old = fixture_source / "2024-10-15 CD drawings"
    fp = fingerprint.fingerprint_folder(folder_old)

    # Record under the OLD path
    vault_scan.append_index(
        str(folder_old), fp, "vault/CD/2024-10-15 CD drawings.md"
    )

    # Now simulate that the folder was renamed on disk
    by_path, by_fp = vault_scan.load_index()
    decision = vault_scan.lookup_event(
        source_path=str(fixture_source / "2024-10-15 CD drawings v2"),
        fingerprint=fp,  # SAME fingerprint
        index_by_path=by_path,
        index_by_fp=by_fp,
    )
    assert decision.action == "rename"
    assert decision.existing_note_path == "vault/CD/2024-10-15 CD drawings.md"
    assert decision.old_source_path == str(folder_old)


# ---------------------------------------------------------------------------
# Step 7: lockfile acquire/release roundtrip
# ---------------------------------------------------------------------------

def test_pipeline_7_lockfile_acquire_release(fixture_state_dir):
    """A scan can acquire, then release, then acquire again — no stale lock."""
    lock1 = vault_scan.acquire_lock()
    assert lock1.exists()
    vault_scan.release_lock()
    assert not lock1.exists()
    # Can re-acquire after release
    lock2 = vault_scan.acquire_lock()
    assert lock2.exists()
    vault_scan.release_lock()
