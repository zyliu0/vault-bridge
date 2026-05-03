"""Tests for scripts/vault_paths.py — the single source of truth for vault path assembly.

Every vault write must use `{domain}/{project}/{subfolder}/{note}.md`.
The canonical bug being fixed: event notes were landing at vault root because
the domain prefix was omitted in retro-scan.md. These tests lock the contract.
"""
import pytest

from scripts import vault_paths


class TestEventNotePath:
    def test_full_path_with_subfolder(self):
        assert (
            vault_paths.event_note_path("arch-projects", "2408 Sample", "SD", "2024-08-01 kickoff.md")
            == "arch-projects/2408 Sample/SD/2024-08-01 kickoff.md"
        )

    def test_empty_subfolder_places_at_project_root(self):
        assert (
            vault_paths.event_note_path("photography", "2024-shoot", "", "2024-05-01 notes.md")
            == "photography/2024-shoot/2024-05-01 notes.md"
        )

    def test_none_subfolder_places_at_project_root(self):
        assert (
            vault_paths.event_note_path("writing", "book-draft", None, "2024-01-01 chapter.md")
            == "writing/book-draft/2024-01-01 chapter.md"
        )

    def test_empty_domain_rejected(self):
        with pytest.raises(ValueError, match="domain"):
            vault_paths.event_note_path("", "proj", "SD", "note.md")

    def test_empty_project_rejected(self):
        with pytest.raises(ValueError, match="project"):
            vault_paths.event_note_path("arch-projects", "", "SD", "note.md")

    def test_empty_note_name_rejected(self):
        with pytest.raises(ValueError, match="note"):
            vault_paths.event_note_path("arch-projects", "proj", "SD", "")

    def test_strips_leading_slashes(self):
        assert (
            vault_paths.event_note_path("/arch-projects", "/proj", "/SD", "/note.md")
            == "arch-projects/proj/SD/note.md"
        )

    def test_strips_trailing_slashes(self):
        assert (
            vault_paths.event_note_path("arch-projects/", "proj/", "SD/", "note.md")
            == "arch-projects/proj/SD/note.md"
        )


class TestProjectIndexPath:
    def test_index_path(self):
        assert (
            vault_paths.project_index_path("arch-projects", "2408 Sample")
            == "arch-projects/2408 Sample/2408 Sample.md"
        )

    def test_base_path(self):
        assert (
            vault_paths.project_base_path("arch-projects", "2408 Sample")
            == "arch-projects/2408 Sample/2408 Sample.base"
        )

    def test_empty_domain_rejected(self):
        with pytest.raises(ValueError, match="domain"):
            vault_paths.project_index_path("", "proj")


class TestAttachmentsRoot:
    def test_flat_attachments(self):
        assert (
            vault_paths.attachments_root("arch-projects", "2408 Sample")
            == "arch-projects/2408 Sample/_Attachments"
        )

    def test_with_batch_folder(self):
        assert (
            vault_paths.attachments_root("arch-projects", "2408 Sample", "2024-08-01--kickoff")
            == "arch-projects/2408 Sample/_Attachments/2024-08-01--kickoff"
        )

    def test_empty_batch_folder_same_as_flat(self):
        assert (
            vault_paths.attachments_root("arch-projects", "2408 Sample", "")
            == "arch-projects/2408 Sample/_Attachments"
        )


class TestEventFolder:
    def test_with_subfolder(self):
        assert vault_paths.event_folder("d", "p", "SD") == "d/p/SD"

    def test_without_subfolder(self):
        assert vault_paths.event_folder("d", "p", "") == "d/p"
        assert vault_paths.event_folder("d", "p", None) == "d/p"


class TestProjectFolder:
    """Convenience helper used by callers that want the project directory itself."""

    def test_project_folder(self):
        assert vault_paths.project_folder("arch-projects", "2408 Sample") == "arch-projects/2408 Sample"


# ---------------------------------------------------------------------------
# v16.3.0 SSS-F — lookup_existing: consult scan index for canonical path
# ---------------------------------------------------------------------------

class TestLookupExisting:
    def _setup_index(self, tmp_path, entries):
        """Write a minimal index.tsv at the workdir's `.vault-bridge/`
        location. Returns the workdir."""
        d = tmp_path / ".vault-bridge"
        d.mkdir(parents=True, exist_ok=True)
        index = d / "index.tsv"
        with index.open("w") as f:
            for source, fp, note in entries:
                f.write(f"{source}\t{fp}\t{note}\n")
        return tmp_path

    def test_returns_existing_note_path(self, tmp_path):
        wd = self._setup_index(tmp_path, [
            ("/archive/200609 清华同衡照明方案/", "abc123",
             "Lighting/2020-06-09 Tsinghua Tongheng plaza lighting scheme.md"),
        ])
        result = vault_paths.lookup_existing(
            wd, "/archive/200609 清华同衡照明方案/",
        )
        assert result == "Lighting/2020-06-09 Tsinghua Tongheng plaza lighting scheme.md"

    def test_returns_none_when_source_not_indexed(self, tmp_path):
        wd = self._setup_index(tmp_path, [])
        result = vault_paths.lookup_existing(wd, "/archive/never/indexed.pdf")
        assert result is None

    def test_returns_none_when_index_missing(self, tmp_path):
        # No index file at all.
        result = vault_paths.lookup_existing(tmp_path, "/some/source.pdf")
        assert result is None

    def test_returns_none_for_empty_source(self, tmp_path):
        wd = self._setup_index(tmp_path, [
            ("/archive/file.pdf", "fp", "Folder/note.md"),
        ])
        assert vault_paths.lookup_existing(wd, "") is None
        assert vault_paths.lookup_existing(wd, None) is None

    def test_preserves_curated_filename(self, tmp_path):
        """The whole point: rewrite uses the existing curated path,
        not a fresh slug derived from the source basename."""
        wd = self._setup_index(tmp_path, [
            ("/archive/221107_同济最新图纸/", "fp",
             "CD/2022-11-07 同济最新图纸 降高度变更送审.md"),
        ])
        out = vault_paths.lookup_existing(wd, "/archive/221107_同济最新图纸/")
        # The translated/expanded "降高度变更送审" suffix must round-trip.
        assert "降高度变更送审" in out
