"""Tests for the F2 pipeline integration of attachment_index + size gate.

Exercises `scan_pipeline._process_images` directly with a shared
AttachmentIndex across successive process_file calls. These tests
explicitly override the size-gate to a deliberate value (default
IMAGE_MIN_BYTES is monkey-patched to 0 globally by `tests/conftest.py`
so pipeline tests can use tiny fixtures — here we set it back to
a real value to exercise the gate).
"""
import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import attachment_index  # noqa: E402
import scan_pipeline  # noqa: E402


def _write_jpeg_of_size(path: Path, payload: bytes) -> None:
    """Write a dummy JPEG-named file with the given byte payload."""
    path.parent.mkdir(parents=True, exist_ok=True)
    # Add a short JPEG header so content sniffers relax; size is what matters.
    path.write_bytes(b"\xff\xd8\xff\xe0" + payload + b"\xff\xd9")


@pytest.fixture
def workdir(tmp_path):
    return tmp_path


# ---------------------------------------------------------------------------
# Size gate
# ---------------------------------------------------------------------------

def test_size_gate_drops_tiny_images(monkeypatch, workdir):
    """Images under IMAGE_MIN_BYTES bytes are warned-and-dropped."""
    # Re-enable the gate for this test (conftest turns it off).
    monkeypatch.setattr(scan_pipeline, "IMAGE_MIN_BYTES", 10_000, raising=True)

    src = workdir / "deck.pdf"
    src.write_bytes(b"%PDF-1.4")
    tiny = workdir / "tiny.jpg"
    _write_jpeg_of_size(tiny, b"x" * 500)  # ~500 bytes total

    with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[tiny]):
        with mock.patch("scan_pipeline.compress_images.compress_image", return_value=tiny):
            result = scan_pipeline.process_file(
                source_path=str(src),
                workdir=str(workdir),
                vault_project_path="arch-projects/2408 Sample/SD",
                event_date="2024-08-01",
                dry_run=True,
            )
    assert result.images_embedded == 0
    assert any("size gate" in w for w in result.warnings)


def test_size_gate_keeps_images_above_threshold(monkeypatch, workdir):
    """Images >= IMAGE_MIN_BYTES bytes pass the gate."""
    monkeypatch.setattr(scan_pipeline, "IMAGE_MIN_BYTES", 1_000, raising=True)

    src = workdir / "deck.pdf"
    src.write_bytes(b"%PDF-1.4")
    big = workdir / "big.jpg"
    _write_jpeg_of_size(big, b"x" * 2_000)  # well above 1 KB

    with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[big]):
        with mock.patch("scan_pipeline.compress_images.compress_image", return_value=big):
            result = scan_pipeline.process_file(
                source_path=str(src),
                workdir=str(workdir),
                vault_project_path="arch-projects/2408 Sample/SD",
                event_date="2024-08-01",
                dry_run=True,
            )
    assert result.images_embedded == 1


# ---------------------------------------------------------------------------
# Content-hash dedup within a single event
# ---------------------------------------------------------------------------

def test_dedup_within_event_collapses_identical_bytes(workdir):
    """Five byte-identical compressed images → 1 embed, not 5."""
    src = workdir / "deck.pdf"
    src.write_bytes(b"%PDF-1.4")
    logo = workdir / "2024-08-01--deck--abcd1234.jpg"
    _write_jpeg_of_size(logo, b"logo-bytes" * 200)  # ~2 KB

    with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[logo] * 5):
        with mock.patch("scan_pipeline.compress_images.compress_image", return_value=logo):
            result = scan_pipeline.process_file(
                source_path=str(src),
                workdir=str(workdir),
                vault_project_path="arch-projects/2408 Sample/SD",
                event_date="2024-08-01",
                dry_run=True,
            )
    assert result.images_embedded == 1
    assert result.attachments == ["![[2024-08-01--deck--abcd1234.jpg]]"]


# ---------------------------------------------------------------------------
# Cross-event dedup via shared AttachmentIndex
# ---------------------------------------------------------------------------

def test_dedup_across_events_reuses_canonical_name(workdir):
    """Second event sees the first event's attachment; its grid embeds the
    canonical filename instead of writing a new vault file.
    """
    src = workdir / "deck.pdf"
    src.write_bytes(b"%PDF-1.4")
    # Two events produce byte-identical images but different event_dates →
    # different compressed filenames. Without F2 dedup we'd see both in the
    # vault. With dedup, event 2 embeds event 1's filename.
    logo_a = workdir / "2024-08-01--deck--aaaaaaaa.jpg"
    _write_jpeg_of_size(logo_a, b"logo-bytes" * 200)
    logo_b = workdir / "2024-09-15--deck--aaaaaaaa.jpg"
    # Same bytes as logo_a → same sha256.
    logo_b.write_bytes(logo_a.read_bytes())

    shared_index = attachment_index.AttachmentIndex()

    # Event 1 — records the canonical name.
    with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[logo_a]):
        with mock.patch("scan_pipeline.compress_images.compress_image", return_value=logo_a):
            r1 = scan_pipeline.process_file(
                source_path=str(src),
                workdir=str(workdir),
                vault_project_path="arch-projects/2408 Sample/SD",
                event_date="2024-08-01",
                dry_run=True,
                att_index=shared_index,
            )
    assert r1.images_embedded == 1
    assert r1.attachments == ["![[2024-08-01--deck--aaaaaaaa.jpg]]"]

    # Event 2 — finds the canonical via hash lookup and reuses it.
    with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[logo_b]):
        with mock.patch("scan_pipeline.compress_images.compress_image", return_value=logo_b):
            r2 = scan_pipeline.process_file(
                source_path=str(src),
                workdir=str(workdir),
                vault_project_path="arch-projects/2408 Sample/SD",
                event_date="2024-09-15",
                dry_run=True,
                att_index=shared_index,
            )
    assert r2.images_embedded == 1
    # Event 2 embeds the event-1 canonical filename, NOT its own event_date
    # prefix. This is the cross-event dedup F2 was added to fix.
    assert r2.attachments == ["![[2024-08-01--deck--aaaaaaaa.jpg]]"]
    assert shared_index.hits == 1


# ---------------------------------------------------------------------------
# process_batch persists the index across runs
# ---------------------------------------------------------------------------

def test_process_batch_persists_attachment_index(workdir):
    """process_batch saves the attachment index under .vault-bridge/."""
    src = workdir / "deck.pdf"
    src.write_bytes(b"%PDF-1.4")
    img = workdir / "2024-08-01--deck--aaaaaaaa.jpg"
    _write_jpeg_of_size(img, b"content" * 200)

    with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
        with mock.patch("scan_pipeline.compress_images.compress_image", return_value=img):
            with mock.patch("scan_pipeline.vault_binary.write_binary", return_value={"ok": True}):
                scan_pipeline.process_batch(
                    source_paths=[str(src)],
                    workdir=str(workdir),
                    vault_project_path="arch-projects/2408 Sample/SD",
                    event_date="2024-08-01",
                    vault_name="V",
                )

    index_path = workdir / ".vault-bridge" / "attachment_hashes.tsv"
    assert index_path.exists()
    # Reload and confirm the canonical was recorded.
    reloaded = attachment_index.load(str(workdir))
    assert len(reloaded.mapping) == 1
    assert list(reloaded.mapping.values())[0] == "2024-08-01--deck--aaaaaaaa.jpg"
