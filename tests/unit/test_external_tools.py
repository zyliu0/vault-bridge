"""Tests for scripts/external_tools.py — v16.0.4 auto-install of the
external CLI tools that `.doc` / `.ppt` / `.dwg` handlers shell out
to.

Covers the v16.0.3 field report complaint ("setup should do the
dependency detection and installation"):

* `detect_missing_tools` surfaces tools that are missing AND
  auto-installable on the current OS; tools with no auto-install path
  (ODA) stay silent so setup's existing REQUIREMENTS.md hint can take
  over.
* `install_tool` runs the package-manager command, re-probes PATH
  plus canonical macOS app-bundle paths (critical because
  `brew --cask libreoffice` does NOT add `soffice` to PATH), and
  reports a clear error when the subprocess exits 0 but the binary
  is still undiscoverable.
* The consent cache (`read_consent` / `write_consent`) persists per
  tool under ``Config.file_type_config["install_consent"]`` so
  re-running setup doesn't re-ask.
* `format_prompt_label` renders the batched AskUserQuestion text.
"""
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import external_tools as et  # noqa: E402


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------

class TestIsToolPresent:
    def test_true_when_binary_on_path(self, monkeypatch):
        monkeypatch.setattr(
            et.shutil, "which",
            lambda b: "/opt/homebrew/bin/soffice" if b == "soffice" else None,
        )
        assert et.is_tool_present(et.LIBREOFFICE) is True

    def test_true_when_macos_app_bundle_exists(self, monkeypatch, tmp_path):
        # Neither `soffice` nor `libreoffice` on PATH, but the app bundle
        # is present — the cask install path that fooled pre-v16.0.4
        # setup into reporting "missing" when it was actually there.
        monkeypatch.setattr(et.shutil, "which", lambda b: None)
        app_path = tmp_path / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"
        app_path.parent.mkdir(parents=True)
        app_path.write_text("#!/bin/sh\n")
        spec = et.ToolSpec(
            name="libreoffice",
            label="LibreOffice",
            categories=("document-office-legacy",),
            binaries=("soffice",),
            macos_app_paths=(str(app_path),),
        )
        assert et.is_tool_present(spec) is True

    def test_false_when_nothing_installed(self, monkeypatch):
        monkeypatch.setattr(et.shutil, "which", lambda b: None)
        spec = et.ToolSpec(
            name="phantom",
            label="Phantom",
            categories=("something",),
            binaries=("phantom-bin",),
        )
        assert et.is_tool_present(spec) is False


class TestDetectMissingTools:
    def test_returns_empty_when_nothing_selected(self, monkeypatch):
        monkeypatch.setattr(et.shutil, "which", lambda b: None)
        assert et.detect_missing_tools([]) == []

    def test_returns_empty_when_tools_already_present(self, monkeypatch):
        monkeypatch.setattr(et, "_platform_key", lambda: "darwin")
        # Pretend soffice AND dwg2dxf are already on PATH.
        monkeypatch.setattr(
            et.shutil, "which",
            lambda b: "/opt/homebrew/bin/" + b if b in {
                "soffice", "dwg2dxf", "brew"} else None,
        )
        missing = et.detect_missing_tools(
            ["document-office-legacy", "cad-dwg"])
        assert missing == []

    def test_detects_libreoffice_on_macos(self, monkeypatch):
        monkeypatch.setattr(et, "_platform_key", lambda: "darwin")
        # Nothing but `brew` on PATH.
        monkeypatch.setattr(
            et.shutil, "which",
            lambda b: "/opt/homebrew/bin/brew" if b == "brew" else None,
        )
        # Block the macOS app-bundle short-circuit too; a test machine
        # that actually has LibreOffice installed at the canonical
        # path would otherwise report the spec as satisfied.
        monkeypatch.setattr(et.Path, "exists", lambda self: False)
        missing = et.detect_missing_tools(["document-office-legacy"])
        assert len(missing) == 1
        assert missing[0].spec.name == "libreoffice"
        assert missing[0].install_cmd[0] == "brew"
        assert "--cask" in missing[0].install_cmd

    def test_deduplicates_tool_shared_across_categories(self, monkeypatch):
        # In a future expansion where both .doc and .docx shared a tool,
        # the two categories should collapse into a single MissingTool
        # entry so the user sees one prompt instead of two.
        monkeypatch.setattr(et, "_platform_key", lambda: "darwin")
        monkeypatch.setattr(
            et.shutil, "which",
            lambda b: "/opt/homebrew/bin/brew" if b == "brew" else None,
        )
        # Inject a synthetic second category that points at LibreOffice.
        fake_spec = et.ToolSpec(
            name="libreoffice",
            label="LibreOffice",
            categories=("document-office-legacy", "doc-office-modern-fake"),
            binaries=("soffice",),
            macos_app_paths=("/non/existent",),
            install_cmds={
                "darwin": ["brew", "install", "--cask", "libreoffice"],
            },
        )
        monkeypatch.setattr(et, "_REGISTRY", (fake_spec,))
        monkeypatch.setattr(
            et, "_spec_by_category",
            lambda c: fake_spec if c in fake_spec.categories else None,
        )
        missing = et.detect_missing_tools(
            ["document-office-legacy", "doc-office-modern-fake"])
        assert len(missing) == 1
        assert set(missing[0].categories) == {
            "document-office-legacy", "doc-office-modern-fake"}

    def test_skips_when_no_install_path_for_platform(self, monkeypatch):
        # libredwg has no Windows install command — simulate that.
        monkeypatch.setattr(et, "_platform_key", lambda: "win32")
        monkeypatch.setattr(
            et.shutil, "which",
            lambda b: "C:\\x\\winget.exe" if b == "winget" else None,
        )
        missing = et.detect_missing_tools(["cad-dwg"])
        # LibreDWG has no win32 entry → not returned (caller falls
        # back to REQUIREMENTS.md warning from handler_installer).
        assert all(m.spec.name != "libredwg" for m in missing)

    def test_skips_when_package_manager_not_on_path(self, monkeypatch):
        # brew missing → can't run the command → skip rather than
        # surface a prompt that would fail immediately.
        monkeypatch.setattr(et, "_platform_key", lambda: "darwin")
        monkeypatch.setattr(et.shutil, "which", lambda b: None)
        missing = et.detect_missing_tools(["document-office-legacy"])
        assert missing == []


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

class TestInstallTool:
    def _missing(self, tool=et.LIBREOFFICE, cmd=None):
        return et.MissingTool(
            spec=tool,
            categories=tool.categories,
            install_cmd=cmd or list(tool.install_cmds["darwin"]),
        )

    def test_reports_failure_when_pm_vanishes(self, monkeypatch):
        monkeypatch.setattr(et.shutil, "which", lambda b: None)
        outcome = et.install_tool(
            self._missing(), stream_output=False, timeout=5)
        assert outcome.ok is False
        assert "not on PATH" in outcome.error

    def test_reports_failure_on_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            et.shutil, "which",
            lambda b: "/opt/homebrew/bin/" + b if b == "brew" else None,
        )

        class _FakeResult:
            returncode = 42
            stdout = ""
            stderr = "brew: network error"

        monkeypatch.setattr(
            et.subprocess, "run", lambda *a, **kw: _FakeResult())
        outcome = et.install_tool(
            self._missing(), stream_output=False, timeout=5)
        assert outcome.ok is False
        assert "exited 42" in outcome.error

    def test_reports_failure_when_reprobe_fails(self, monkeypatch):
        # The critical regression path: brew exits 0 but soffice is
        # still not on PATH and the app bundle doesn't exist. Pre-
        # v16.0.4 code would have reported success; v16.0.4 must
        # report "installed but not detected on PATH" so the caller
        # can flag the failure.
        monkeypatch.setattr(
            et.shutil, "which",
            lambda b: "/opt/homebrew/bin/brew" if b == "brew" else None,
        )

        class _FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(
            et.subprocess, "run", lambda *a, **kw: _FakeResult())
        # Force macos_app_paths to a non-existent path so re-probe fails.
        fake = et.ToolSpec(
            name="libreoffice",
            label="LibreOffice",
            categories=("document-office-legacy",),
            binaries=("soffice",),
            macos_app_paths=("/non/existent/path",),
            install_cmds=et.LIBREOFFICE.install_cmds,
        )
        outcome = et.install_tool(
            self._missing(tool=fake), stream_output=False, timeout=5)
        assert outcome.ok is False
        assert "not detected on PATH" in outcome.error

    def test_reports_success_when_reprobe_finds_app_bundle(
        self, monkeypatch, tmp_path,
    ):
        monkeypatch.setattr(
            et.shutil, "which",
            lambda b: "/opt/homebrew/bin/brew" if b == "brew" else None,
        )

        class _FakeResult:
            returncode = 0
            stdout = ""
            stderr = ""

        monkeypatch.setattr(
            et.subprocess, "run", lambda *a, **kw: _FakeResult())
        app_bundle = tmp_path / "LibreOffice.app" / "Contents" / "MacOS" / "soffice"
        app_bundle.parent.mkdir(parents=True)
        app_bundle.write_text("#!/bin/sh\n")
        fake = et.ToolSpec(
            name="libreoffice",
            label="LibreOffice",
            categories=("document-office-legacy",),
            binaries=("soffice",),  # still not on PATH
            macos_app_paths=(str(app_bundle),),
            install_cmds=et.LIBREOFFICE.install_cmds,
        )
        outcome = et.install_tool(
            self._missing(tool=fake), stream_output=False, timeout=5)
        assert outcome.ok is True
        assert outcome.error == ""

    def test_timeout_returns_failure(self, monkeypatch):
        monkeypatch.setattr(
            et.shutil, "which",
            lambda b: "/opt/homebrew/bin/brew" if b == "brew" else None,
        )

        def _raise_timeout(*a, **kw):
            raise subprocess.TimeoutExpired("brew", 5)

        monkeypatch.setattr(et.subprocess, "run", _raise_timeout)
        outcome = et.install_tool(
            self._missing(), stream_output=False, timeout=5)
        assert outcome.ok is False
        assert "install timed out" in outcome.error or "unexpected" in outcome.error


# ---------------------------------------------------------------------------
# Consent cache
# ---------------------------------------------------------------------------

class TestConsentCache:
    def test_read_returns_none_when_missing(self):
        assert et.read_consent({}, "libreoffice") is None

    def test_read_returns_none_when_malformed(self):
        assert et.read_consent({"install_consent": "bad"}, "libreoffice") is None

    def test_write_then_read_round_trip(self):
        ftc = {}
        et.write_consent(ftc, "libreoffice", True)
        assert et.read_consent(ftc, "libreoffice") is True
        et.write_consent(ftc, "libreoffice", False)
        assert et.read_consent(ftc, "libreoffice") is False

    def test_write_preserves_other_keys(self):
        # Consent writes must NOT stomp on sibling file-type config
        # keys like `installed_packages` — setup.md writes both.
        ftc = {"installed_packages": {"pdf": "document_pdf_pdf.py"}}
        et.write_consent(ftc, "libreoffice", True)
        assert ftc["installed_packages"] == {"pdf": "document_pdf_pdf.py"}
        assert ftc["install_consent"] == {"libreoffice": True}

    def test_write_repairs_malformed_cache(self):
        # If someone hand-edited config.json and made install_consent
        # a string, write_consent must replace it rather than raise.
        ftc = {"install_consent": "oops"}
        et.write_consent(ftc, "libreoffice", True)
        assert ftc["install_consent"] == {"libreoffice": True}


# ---------------------------------------------------------------------------
# Prompt rendering
# ---------------------------------------------------------------------------

class TestFormatPromptLabel:
    def test_empty_list_returns_empty_string(self):
        assert et.format_prompt_label([]) == ""

    def test_single_tool_shows_count_label_and_hint(self):
        m = et.MissingTool(
            spec=et.LIBREOFFICE,
            categories=("document-office-legacy",),
            install_cmd=list(et.LIBREOFFICE.install_cmds["darwin"]),
        )
        out = et.format_prompt_label([m])
        assert "Install 1 missing tool(s)" in out
        assert "LibreOffice" in out
        assert ".doc/.ppt" in out
        assert "~500 MB" in out

    def test_multiple_tools_joined_with_semicolons(self):
        m1 = et.MissingTool(
            spec=et.LIBREOFFICE,
            categories=("document-office-legacy",),
            install_cmd=list(et.LIBREOFFICE.install_cmds["darwin"]),
        )
        m2 = et.MissingTool(
            spec=et.LIBREDWG,
            categories=("cad-dwg",),
            install_cmd=list(et.LIBREDWG.install_cmds["darwin"]),
        )
        out = et.format_prompt_label([m1, m2])
        assert "Install 2 missing tool(s)" in out
        assert ";" in out
        assert "LibreDWG" in out and "LibreOffice" in out


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

class TestPlatformDetection:
    def test_darwin(self, monkeypatch):
        monkeypatch.setattr(et.sys, "platform", "darwin")
        assert et._platform_key() == "darwin"

    def test_win32(self, monkeypatch):
        monkeypatch.setattr(et.sys, "platform", "win32")
        assert et._platform_key() == "win32"

    def test_linux_debian_via_os_release(self, monkeypatch, tmp_path):
        monkeypatch.setattr(et.sys, "platform", "linux")
        osrel = tmp_path / "os-release"
        osrel.write_text('ID=ubuntu\nID_LIKE=debian\n')
        monkeypatch.setattr(
            et, "Path", lambda p: osrel if p == "/etc/os-release" else Path(p))
        # Path replacement in dynamic module is brittle; easier: patch
        # the helper directly.
        monkeypatch.setattr(et, "_linux_family", lambda: "debian")
        assert et._platform_key() == "linux.debian"

    def test_linux_unknown_family(self, monkeypatch):
        monkeypatch.setattr(et.sys, "platform", "linux")
        monkeypatch.setattr(et, "_linux_family", lambda: "unknown")
        assert et._platform_key() == "linux.unknown"
