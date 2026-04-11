#!/usr/bin/env python3
"""Parse vault-bridge config from the vault's CLAUDE.md.

Finds the `## vault-bridge: configuration` heading, extracts the fenced
YAML codeblock under it, validates every required field and enum against
the schema in the design doc's Plugin Configuration Schema section.

Exits 0 with parsed JSON on stdout if valid.
Exits 2 with specific stderr message if invalid.

Every scan command runs this as step 1 and fails fast on any error — no
silent fallback to Admin/ or guessing defaults.

Usage:
  python3 parse_config.py [path-to-CLAUDE.md]

If no path is given, defaults to ./CLAUDE.md.
"""
import json
import re
import sys
from pathlib import Path

import yaml

# Schema constants for the CONFIG (not the note frontmatter — that's in schema.py).
# The config schema is separate and documented in the Plugin Configuration Schema
# section of the design doc.

SUPPORTED_VERSIONS = {1}

ALLOWED_TOP_LEVEL_KEYS = {
    "version",
    "file_system",
    "routing",
    "skip_patterns",
    "style",
}

REQUIRED_TOP_LEVEL = {"version", "file_system", "routing"}

REQUIRED_FS_FIELDS = {"type", "root_path", "access_pattern"}

VALID_FS_TYPES = {"nas-mcp", "local-path", "external-mount"}

REQUIRED_ROUTING_FIELDS = {"patterns", "fallback"}

# Regex for the heading + YAML codeblock.
# - Look for `## vault-bridge: configuration` at start of line
# - Then any content (non-greedy) until the next ```yaml fence
# - Stop at the closing ``` fence
# - But do NOT cross another `## ` heading (that ends the section)
_CONFIG_BLOCK_RE = re.compile(
    r"^## vault-bridge: configuration\s*\n"   # heading
    r"(?:(?!^## )[\s\S])*?"                    # anything not starting with `## `
    r"^```yaml\s*\n"                           # opening yaml fence
    r"([\s\S]*?)"                              # the YAML content (captured)
    r"^```",                                   # closing fence
    re.MULTILINE,
)


def die(msg: str) -> None:
    sys.stderr.write(f"vault-bridge: {msg}\n")
    sys.exit(2)


def parse_config(claude_md_path: str) -> dict:
    path = Path(claude_md_path)
    if not path.exists():
        die(f"CLAUDE.md not found at {claude_md_path}")

    content = path.read_text()

    m = _CONFIG_BLOCK_RE.search(content)
    if not m:
        # Distinguish "no heading at all" from "heading but no codeblock"
        if "## vault-bridge: configuration" in content:
            die(
                "config heading found in CLAUDE.md but no ```yaml codeblock underneath it. "
                "Add a fenced ```yaml ... ``` block under the '## vault-bridge: configuration' heading. "
                "See README §Setup for a template."
            )
        die(
            "no config found in CLAUDE.md. Expected a heading "
            "'## vault-bridge: configuration' followed by a ```yaml codeblock. "
            "See README §Setup for a template."
        )

    yaml_text = m.group(1)
    try:
        config = yaml.safe_load(yaml_text)
    except yaml.YAMLError as e:
        die(f"config YAML is malformed: {e}")

    if not isinstance(config, dict):
        die(f"config must be a YAML mapping, got {type(config).__name__}")

    # Unknown top-level keys
    unknown = set(config.keys()) - ALLOWED_TOP_LEVEL_KEYS
    if unknown:
        die(
            f"unknown config key(s): {sorted(unknown)}. "
            f"Allowed top-level keys: {sorted(ALLOWED_TOP_LEVEL_KEYS)}"
        )

    # Missing required top-level keys
    missing = REQUIRED_TOP_LEVEL - set(config.keys())
    if missing:
        die(f"config missing required fields: {sorted(missing)}")

    # version
    if config["version"] not in SUPPORTED_VERSIONS:
        die(
            f"config version {config['version']} not supported. "
            f"This plugin supports versions: {sorted(SUPPORTED_VERSIONS)}"
        )

    # file_system section
    fs = config["file_system"]
    if not isinstance(fs, dict):
        die(f"file_system must be a mapping, got {type(fs).__name__}")
    missing_fs = REQUIRED_FS_FIELDS - set(fs.keys())
    if missing_fs:
        die(f"file_system missing required fields: {sorted(missing_fs)}")
    if fs["type"] not in VALID_FS_TYPES:
        die(
            f"file_system.type '{fs['type']}' is not valid. "
            f"Must be one of: {sorted(VALID_FS_TYPES)}"
        )

    # routing section
    routing = config["routing"]
    if not isinstance(routing, dict):
        die(f"routing must be a mapping, got {type(routing).__name__}")
    missing_r = REQUIRED_ROUTING_FIELDS - set(routing.keys())
    if missing_r:
        die(f"routing missing required fields: {sorted(missing_r)}")

    patterns = routing["patterns"]
    if not isinstance(patterns, list):
        die(f"routing.patterns must be a list, got {type(patterns).__name__}")
    for i, pattern in enumerate(patterns):
        if not isinstance(pattern, dict):
            die(
                f"routing.patterns[{i}] must be a mapping with 'match' and "
                f"'subfolder' fields, got {type(pattern).__name__}"
            )
        if "match" not in pattern:
            die(f"routing.patterns[{i}] missing required field 'match'")
        if "subfolder" not in pattern:
            die(f"routing.patterns[{i}] missing required field 'subfolder'")

    return config


if __name__ == "__main__":
    claude_md = sys.argv[1] if len(sys.argv) > 1 else "CLAUDE.md"
    config = parse_config(claude_md)
    json.dump(config, sys.stdout, indent=2, ensure_ascii=False)
    print()
