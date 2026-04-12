"""Tests for scripts/parse_config.py — the plugin config parser.

Reads the user's vault CLAUDE.md, finds the `## vault-bridge: configuration`
heading, extracts the YAML codeblock under it, and validates the schema from
the design doc's Plugin Configuration Schema section.

Exit 0 with parsed JSON on stdout = valid.
Exit 2 with specific stderr message = invalid.

The 13 canonical test cases from the design doc, plus happy paths.
"""
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PARSER = REPO_ROOT / "scripts" / "parse_config.py"


def run_parser(claude_md_path: Path):
    result = subprocess.run(
        [sys.executable, str(PARSER), str(claude_md_path)],
        capture_output=True,
        text=True,
    )
    return result.returncode, result.stdout, result.stderr


def write_claude_md(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "CLAUDE.md"
    path.write_text(body)
    return path


VALID_CONFIG_BLOCK = """Some vault intro prose.

## vault-bridge: configuration

```yaml
version: 1

file_system:
  type: nas-mcp
  root_path: /archive/
  access_pattern: |
    Use mcp__nas__read_file(path) and mcp__nas__list_files(path).

routing:
  patterns:
    - match: "3_施工图 CD"
      subfolder: CD
    - match: "2_方案SD"
      subfolder: SD
  fallback: Admin

skip_patterns:
  - "#recycle"
  - "@eaDir"

style:
  note_filename_pattern: "YYYY-MM-DD topic.md"
  writing_voice: first-person-diary
  summary_word_count: [100, 200]
```

Some prose after.
"""


# ---------------------------------------------------------------------------
# Case 1: valid full config parses successfully
# ---------------------------------------------------------------------------

def test_case_1_valid_config_returns_parsed_json(tmp_path):
    claude_md = write_claude_md(tmp_path, VALID_CONFIG_BLOCK)
    code, stdout, stderr = run_parser(claude_md)
    assert code == 0, f"Valid config should pass, got stderr:\n{stderr}"
    config = json.loads(stdout)
    assert config["version"] == 1
    assert config["file_system"]["type"] == "nas-mcp"
    assert config["file_system"]["root_path"] == "/archive/"
    assert len(config["routing"]["patterns"]) == 2
    assert config["routing"]["fallback"] == "Admin"


def test_case_1_valid_config_with_only_required_fields(tmp_path):
    minimal = """## vault-bridge: configuration

```yaml
version: 1

file_system:
  type: local-path
  root_path: /home/user/projects
  access_pattern: "Use Read and Glob for all file reads."

routing:
  patterns: []
  fallback: Inbox
```
"""
    claude_md = write_claude_md(tmp_path, minimal)
    code, stdout, stderr = run_parser(claude_md)
    assert code == 0, f"Minimal valid config should pass, got stderr:\n{stderr}"
    config = json.loads(stdout)
    assert config["routing"]["fallback"] == "Inbox"


# ---------------------------------------------------------------------------
# Case 2: missing heading
# ---------------------------------------------------------------------------

def test_case_2_no_heading(tmp_path):
    body = "# Vault CLAUDE.md\n\nNo vault-bridge section at all.\n"
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "no config" in stderr.lower() or "not found" in stderr.lower()
    assert "vault-bridge: configuration" in stderr


# ---------------------------------------------------------------------------
# Case 3: heading exists but no YAML codeblock
# ---------------------------------------------------------------------------

def test_case_3_heading_without_yaml_codeblock(tmp_path):
    body = """## vault-bridge: configuration

This section exists but has no yaml codeblock underneath it.
Just prose and nothing else.
"""
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "yaml" in stderr.lower() or "codeblock" in stderr.lower() or "no config" in stderr.lower()


# ---------------------------------------------------------------------------
# Case 4: malformed YAML
# ---------------------------------------------------------------------------

def test_case_4_malformed_yaml(tmp_path):
    body = """## vault-bridge: configuration

```yaml
version: 1
file_system:
  type: nas-mcp
  root_path: /archive/
  access_pattern: "blah"
routing:
  patterns:
    - match: "CD"
      subfolder: CD
      extra: :::not-valid::: yaml
  fallback: Admin
```
"""
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "yaml" in stderr.lower() or "malformed" in stderr.lower() or "parse" in stderr.lower()


# ---------------------------------------------------------------------------
# Case 5: missing version field
# ---------------------------------------------------------------------------

def test_case_5_missing_version(tmp_path):
    body = """## vault-bridge: configuration

```yaml
file_system:
  type: nas-mcp
  root_path: /archive/
  access_pattern: "blah"
routing:
  patterns: []
  fallback: Admin
```
"""
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "version" in stderr.lower()
    assert "missing" in stderr.lower() or "required" in stderr.lower()


# ---------------------------------------------------------------------------
# Case 6: unsupported version
# ---------------------------------------------------------------------------

def test_case_6_unsupported_version(tmp_path):
    body = VALID_CONFIG_BLOCK.replace("version: 1", "version: 99")
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "version" in stderr.lower()
    assert "99" in stderr or "not supported" in stderr.lower() or "unsupported" in stderr.lower()


# ---------------------------------------------------------------------------
# Case 7: unknown top-level key
# ---------------------------------------------------------------------------

def test_case_7_unknown_top_level_key(tmp_path):
    body = VALID_CONFIG_BLOCK.replace(
        "version: 1",
        "version: 1\nextra_config:\n  foo: bar",
    )
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "extra_config" in stderr
    assert "unknown" in stderr.lower()


# ---------------------------------------------------------------------------
# Case 8: missing file_system.type
# ---------------------------------------------------------------------------

def test_case_8_missing_file_system_type(tmp_path):
    body = """## vault-bridge: configuration

```yaml
version: 1
file_system:
  root_path: /home/user
  access_pattern: "blah"
routing:
  patterns: []
  fallback: Admin
```
"""
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "file_system" in stderr
    assert "type" in stderr


# ---------------------------------------------------------------------------
# Case 9: invalid file_system.type
# ---------------------------------------------------------------------------

def test_case_9_invalid_file_system_type(tmp_path):
    body = VALID_CONFIG_BLOCK.replace("type: nas-mcp", "type: webdav")
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "file_system" in stderr or "type" in stderr
    assert "webdav" in stderr or "nas-mcp" in stderr or "invalid" in stderr.lower()


# ---------------------------------------------------------------------------
# Case 10: routing pattern missing "match"
# ---------------------------------------------------------------------------

def test_case_10_pattern_missing_match(tmp_path):
    body = """## vault-bridge: configuration

```yaml
version: 1
file_system:
  type: local-path
  root_path: /home/user
  access_pattern: "blah"
routing:
  patterns:
    - subfolder: CD
  fallback: Admin
```
"""
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "match" in stderr


# ---------------------------------------------------------------------------
# Case 11: routing pattern missing "subfolder"
# ---------------------------------------------------------------------------

def test_case_11_pattern_missing_subfolder(tmp_path):
    body = """## vault-bridge: configuration

```yaml
version: 1
file_system:
  type: local-path
  root_path: /home/user
  access_pattern: "blah"
routing:
  patterns:
    - match: "CD"
  fallback: Admin
```
"""
    claude_md = write_claude_md(tmp_path, body)
    code, _, stderr = run_parser(claude_md)
    assert code != 0
    assert "subfolder" in stderr


# ---------------------------------------------------------------------------
# Case 12: empty patterns list + valid fallback is allowed
# ---------------------------------------------------------------------------

def test_case_12_empty_patterns_with_valid_fallback(tmp_path):
    body = """## vault-bridge: configuration

```yaml
version: 1
file_system:
  type: local-path
  root_path: /home/user
  access_pattern: "blah"
routing:
  patterns: []
  fallback: Archive
```
"""
    claude_md = write_claude_md(tmp_path, body)
    code, stdout, stderr = run_parser(claude_md)
    assert code == 0, f"Empty patterns should be allowed, got stderr:\n{stderr}"
    config = json.loads(stdout)
    assert config["routing"]["patterns"] == []
    assert config["routing"]["fallback"] == "Archive"


# ---------------------------------------------------------------------------
# Case 13: all optional fields absent is valid
# ---------------------------------------------------------------------------

def test_case_13_all_optional_fields_absent(tmp_path):
    """version + file_system + routing is the minimum. skip_patterns + style are optional."""
    body = """## vault-bridge: configuration

```yaml
version: 1
file_system:
  type: local-path
  root_path: /home/user
  access_pattern: "blah"
routing:
  patterns: []
  fallback: Inbox
```
"""
    claude_md = write_claude_md(tmp_path, body)
    code, stdout, stderr = run_parser(claude_md)
    assert code == 0, f"All-optional-absent should be valid, got stderr:\n{stderr}"
    config = json.loads(stdout)
    assert "skip_patterns" not in config or config.get("skip_patterns") is None
    assert "style" not in config or config.get("style") is None


# ---------------------------------------------------------------------------
# Extra: file not found
# ---------------------------------------------------------------------------

def test_file_not_found(tmp_path):
    missing = tmp_path / "nonexistent.md"
    code, _, stderr = run_parser(missing)
    assert code != 0
    assert "not found" in stderr.lower() or "exist" in stderr.lower()
