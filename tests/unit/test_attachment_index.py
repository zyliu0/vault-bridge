"""Tests for scripts/attachment_index.py — cross-event sha256 dedup (F2)."""
import sys
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import attachment_index  # noqa: E402


def test_empty_index_misses_every_lookup():
    idx = attachment_index.AttachmentIndex()
    assert idx.lookup("abcdef") is None
    assert idx.hits == 0


def test_record_then_lookup_returns_canonical_name():
    idx = attachment_index.AttachmentIndex()
    idx.record("deadbeef", "2024-01-01--logo--deadbeef.jpg")
    assert idx.lookup("deadbeef") == "2024-01-01--logo--deadbeef.jpg"
    assert idx.hits == 1


def test_double_record_keeps_first():
    """The canonical filename is the first one seen."""
    idx = attachment_index.AttachmentIndex()
    idx.record("h1", "first.jpg")
    idx.record("h1", "second.jpg")
    assert idx.lookup("h1") == "first.jpg"


def test_load_returns_empty_when_no_file(tmp_path):
    idx = attachment_index.load(str(tmp_path))
    assert len(idx.mapping) == 0


def test_roundtrip_persist_then_load(tmp_path):
    idx = attachment_index.AttachmentIndex()
    idx.record("h1", "logo.jpg", today="2024-08-01")
    idx.record("h2", "diagram.jpg", today="2024-08-02")
    idx.persist(str(tmp_path))

    reloaded = attachment_index.load(str(tmp_path))
    assert reloaded.lookup("h1") == "logo.jpg"
    assert reloaded.lookup("h2") == "diagram.jpg"


def test_sha256_of_file(tmp_path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"hello world")
    digest = attachment_index.sha256_of_file(f)
    # Known sha256 of "hello world"
    assert digest == "b94d27b9934d3e08a52e52d7da7dabfac484efe37a5380ee9088f7ace2efcde9"


def test_persist_survives_missing_directory(tmp_path):
    """persist() creates .vault-bridge/ if it does not exist."""
    workdir = tmp_path / "fresh-workdir"
    workdir.mkdir()
    idx = attachment_index.AttachmentIndex()
    idx.record("h1", "logo.jpg")
    idx.persist(str(workdir))
    assert (workdir / ".vault-bridge" / "attachment_hashes.tsv").exists()


def test_load_skips_malformed_lines(tmp_path):
    """A malformed TSV row is ignored; good rows still load."""
    vb_dir = tmp_path / ".vault-bridge"
    vb_dir.mkdir()
    (vb_dir / "attachment_hashes.tsv").write_text(
        "# sha256\tfilename\tfirst_seen\n"
        "malformed-no-tabs\n"
        "h1\tlogo.jpg\t2024-01-01\n",
        encoding="utf-8",
    )
    idx = attachment_index.load(str(tmp_path))
    assert idx.lookup("h1") == "logo.jpg"
    assert len(idx.mapping) == 1
