"""CI lock on schema drift between scripts/schema.py and the rest of the repo.

scripts/schema.py is the single source of truth for the frontmatter contract.
The command markdown files reference the same field names in their inlined
instructions. This test ensures they stay in sync: if someone adds a field to
schema.py but forgets to update retro-scan.md, CI fails.

It also locks the config-schema terms from scripts/parse_config.py: the
validate-config command must mention the heading it looks for, and the
README must document the preset profiles.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = REPO_ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

import schema  # noqa: E402
import parse_config  # noqa: E402


def _command_file(name: str) -> str:
    path = REPO_ROOT / "commands" / name
    return path.read_text() if path.exists() else ""


def _readme() -> str:
    path = REPO_ROOT / "README.md"
    return path.read_text() if path.exists() else ""


def _plugin_claude_md() -> str:
    path = REPO_ROOT / "CLAUDE.md"
    return path.read_text() if path.exists() else ""


# ---------------------------------------------------------------------------
# Retro-scan command must mention every required frontmatter field
# ---------------------------------------------------------------------------

def test_retro_scan_command_mentions_all_required_frontmatter_fields():
    """If schema.py adds a field, retro-scan.md must reference it so Claude
    knows to include it in notes it writes.
    """
    content = _command_file("retro-scan.md")
    if not content:
        # Command file not written yet — skip (the test becomes meaningful
        # once Phase C.1 ships the file)
        import pytest
        pytest.skip("commands/retro-scan.md not yet created")

    for field in schema.REQUIRED_FIELDS:
        assert field in content, (
            f"commands/retro-scan.md does not mention required frontmatter "
            f"field '{field}'. schema.py and retro-scan.md are out of sync. "
            f"Update the command prompt to include the new field."
        )


def test_retro_scan_mentions_every_file_type_enum():
    content = _command_file("retro-scan.md")
    if not content:
        import pytest
        pytest.skip("commands/retro-scan.md not yet created")
    for ft in schema.ENUMS["file_type"]:
        assert ft in content, (
            f"commands/retro-scan.md does not mention file_type '{ft}'"
        )


def test_retro_scan_mentions_validator_call():
    """retro-scan must invoke validate_frontmatter.py after every Write."""
    content = _command_file("retro-scan.md")
    if not content:
        import pytest
        pytest.skip("commands/retro-scan.md not yet created")
    assert "validate_frontmatter.py" in content or "validate-frontmatter.py" in content, (
        "retro-scan.md does not call validate_frontmatter.py — "
        "the write-time schema check will not run"
    )


def test_retro_scan_mentions_parse_config_call():
    content = _command_file("retro-scan.md")
    if not content:
        import pytest
        pytest.skip("commands/retro-scan.md not yet created")
    assert "parse_config.py" in content or "parse-config.py" in content, (
        "retro-scan.md does not call parse_config.py — "
        "the config will not be parsed and the routing table will be missing"
    )


def test_retro_scan_mentions_stop_word_list():
    """The fabrication firewall stop-word list must be inlined."""
    content = _command_file("retro-scan.md")
    if not content:
        import pytest
        pytest.skip("commands/retro-scan.md not yet created")
    stop_words = ["pulled the back wall in", "Wu said", "review came back"]
    for sw in stop_words:
        assert sw in content, (
            f"retro-scan.md does not include stop-word '{sw}' — "
            f"the fabrication firewall from Composition Test 2 is weaker"
        )


# ---------------------------------------------------------------------------
# Validate-config command
# ---------------------------------------------------------------------------

def test_validate_config_command_exists_and_calls_parser():
    content = _command_file("validate-config.md")
    if not content:
        import pytest
        pytest.skip("commands/validate-config.md not yet created")
    assert "parse_config.py" in content


def test_setup_command_exists_and_calls_save():
    content = _command_file("setup.md")
    if not content:
        import pytest
        pytest.skip("commands/setup.md not yet created")
    assert "setup_config" in content or "save_config" in content
    assert "archive" in content.lower()
    assert "preset" in content.lower()


# ---------------------------------------------------------------------------
# Heartbeat-scan command
# ---------------------------------------------------------------------------

def test_heartbeat_scan_mentions_required_frontmatter_fields():
    content = _command_file("heartbeat-scan.md")
    if not content:
        import pytest
        pytest.skip("commands/heartbeat-scan.md not yet created")
    for field in schema.REQUIRED_FIELDS:
        assert field in content, (
            f"heartbeat-scan.md does not mention required field '{field}'"
        )


def test_heartbeat_scan_mentions_scan_type_heartbeat():
    content = _command_file("heartbeat-scan.md")
    if not content:
        import pytest
        pytest.skip("commands/heartbeat-scan.md not yet created")
    # scan_type must be set to "heartbeat" not "retro" for this command
    assert "heartbeat" in content


# ---------------------------------------------------------------------------
# Vault-health command
# ---------------------------------------------------------------------------

def test_vault_health_mentions_orphan_check():
    content = _command_file("vault-health.md")
    if not content:
        import pytest
        pytest.skip("commands/vault-health.md not yet created")
    assert "orphan" in content.lower()


def test_vault_health_mentions_fingerprint_duplicate_check():
    content = _command_file("vault-health.md")
    if not content:
        import pytest
        pytest.skip("commands/vault-health.md not yet created")
    assert "fingerprint" in content.lower() or "duplicate" in content.lower()


# ---------------------------------------------------------------------------
# README must document the preset profiles and the setup steps
# ---------------------------------------------------------------------------

def test_readme_mentions_config_heading():
    """The README must tell users what heading to add to their CLAUDE.md."""
    content = _readme()
    if not content:
        import pytest
        pytest.skip("README.md not yet created")
    assert "vault-bridge: configuration" in content


def test_readme_documents_requirements_txt():
    content = _readme()
    if not content:
        import pytest
        pytest.skip("README.md not yet created")
    assert "requirements.txt" in content or "pip install" in content


def test_readme_mentions_all_three_commands():
    content = _readme()
    if not content:
        import pytest
        pytest.skip("README.md not yet created")
    for cmd in ("retro-scan", "heartbeat-scan", "vault-health"):
        assert cmd in content, f"README.md does not mention /{cmd}"


# ---------------------------------------------------------------------------
# Plugin CLAUDE.md ships preset profiles
# ---------------------------------------------------------------------------

def test_plugin_claude_md_has_all_three_presets():
    content = _plugin_claude_md()
    if not content:
        import pytest
        pytest.skip("CLAUDE.md not yet created")
    # The 3 preset profile names from the design doc
    presets = ["architecture", "photographer", "writer"]
    for p in presets:
        assert p.lower() in content.lower(), (
            f"plugin CLAUDE.md does not include the '{p}' preset profile"
        )


# ---------------------------------------------------------------------------
# Plugin manifest
# ---------------------------------------------------------------------------

def test_plugin_json_exists_and_has_name():
    import json
    manifest_path = REPO_ROOT / ".claude-plugin" / "plugin.json"
    if not manifest_path.exists():
        import pytest
        pytest.skip(".claude-plugin/plugin.json not yet created")
    manifest = json.loads(manifest_path.read_text())
    assert manifest.get("name") == "vault-bridge"


def test_every_command_frontmatter_parses_as_yaml():
    """Regression test for the unquoted argument-hint bug.

    `claude plugin validate` caught this initially: argument-hint fields that
    contain unquoted square brackets get YAML-parsed as flow sequences, which
    silently drops the entire frontmatter block at plugin load time. The fix
    is to quote the argument-hint value. This test prevents regression.
    """
    import re
    import yaml
    commands_dir = REPO_ROOT / "commands"
    if not commands_dir.exists():
        import pytest
        pytest.skip("commands/ dir not yet created")

    for cmd_path in commands_dir.glob("*.md"):
        content = cmd_path.read_text()
        m = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
        assert m, f"{cmd_path.name} has no frontmatter block"
        try:
            fm = yaml.safe_load(m.group(1))
        except yaml.YAMLError as e:
            raise AssertionError(
                f"{cmd_path.name} frontmatter YAML fails to parse: {e}. "
                f"Quote any field value that contains square brackets."
            )
        assert isinstance(fm, dict), (
            f"{cmd_path.name} frontmatter parsed as {type(fm).__name__}, "
            f"not a dict. Likely an argument-hint with unquoted brackets."
        )
        # Every command must have a description
        assert "description" in fm, (
            f"{cmd_path.name} frontmatter missing 'description' field"
        )
        # argument-hint, if present, must be a string (not a list — that's
        # the canary for the unquoted-brackets bug)
        if "argument-hint" in fm:
            assert isinstance(fm["argument-hint"], str), (
                f"{cmd_path.name} argument-hint is {type(fm['argument-hint']).__name__}, "
                f"not str. Quote it with double quotes."
            )
