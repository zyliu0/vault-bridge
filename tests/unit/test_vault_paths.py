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
