#!/usr/bin/env python3
"""Validate a vault-bridge note's frontmatter against the schema.

Called by commands/retro-scan.md (and the other scan commands) after every
Write tool invocation. If the written note drifts from scripts/schema.py in
any way, this script exits non-zero and prints a specific error message to
stderr. The command then halts the scan.

This is the backstop that makes Path 1 (single-prompt composition) safe:
Claude might type a field name wrong, forget a field, use a stale enum
value, shuffle the order, or violate a cross-field invariant — but the
note won't silently ship. It gets rejected at write time.

Exit codes:
  0 = valid
  2 = invalid (with specific stderr message)

Usage:
  python3 validate_frontmatter.py <path-to-note.md>
"""
import datetime
import re
import sys
from pathlib import Path

import yaml

# Make the schema module importable from scripts/
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import schema  # noqa: E402


def die(msg: str) -> None:
    """Print a validation error to stderr and exit 2."""
    sys.stderr.write(f"vault-bridge: {msg}\n")
    sys.exit(2)


def validate(note_path: str) -> None:
    """Validate a note file at a filesystem path (for temp files only)."""
    path = Path(note_path)
    if not path.exists():
        die(f"{note_path}: file does not exist")
    validate_content(path.read_text(), note_path)


def validate_content(content: str, label: str = "<stdin>") -> None:
    """Validate note content from a string. Core validation logic."""
    note_path = label  # used in error messages

    # 1. Extract the frontmatter block between the first two --- lines.
    m = re.match(r"^---\n(.*?)\n---\n", content, re.DOTALL)
    if not m:
        die(f"{note_path}: no frontmatter block found (expected leading --- ... --- fence)")
    fm_yaml = m.group(1)

    # 2. Parse the YAML.
    try:
        fm = yaml.safe_load(fm_yaml)
    except yaml.YAMLError as e:
        die(f"{note_path}: frontmatter YAML is malformed: {e}")

    if not isinstance(fm, dict):
        die(f"{note_path}: frontmatter is not a mapping (got {type(fm).__name__})")

    # PyYAML auto-parses unquoted YAML dates (2026-04-12) as datetime.date
    # objects. The schema declares date fields as `str` (ISO YYYY-MM-DD). These
    # are semantically equivalent — normalize date/datetime back to ISO string
    # before type-checking so the plugin can emit unquoted YAML dates (the
    # human-readable form) without failing validation.
    for field in ("captured_date", "event_date"):
        if field in fm and isinstance(fm[field], (datetime.date, datetime.datetime)):
            fm[field] = fm[field].isoformat()

    # 2b. Determine schema version for version-aware validation.
    sv = fm.get("schema_version")
    if not isinstance(sv, int) or sv not in schema.SUPPORTED_SCHEMA_VERSIONS:
        die(
            f"{note_path}: schema_version must be one of "
            f"{sorted(schema.SUPPORTED_SCHEMA_VERSIONS)}, got {sv!r}"
        )

    req_fields = schema.get_required_fields(sv)
    opt_fields = schema.get_optional_fields(sv)
    field_order = schema.get_field_order(sv)

    # 3. Drift case 1: no unknown fields.
    allowed = set(req_fields) | set(opt_fields)
    unknown = set(fm.keys()) - allowed
    if unknown:
        die(
            f"{note_path}: unknown frontmatter field(s): {sorted(unknown)}. "
            f"Allowed fields: {sorted(allowed)}"
        )

    # 4. Drift case 2: all required fields present.
    missing = set(req_fields) - set(fm.keys())
    if missing:
        die(f"{note_path}: missing required field(s): {sorted(missing)}")

    # 5. Drift case 4: types match FIELD_TYPES.
    # (Checked before enums so we don't try to look up a set member with wrong type.)
    for field, expected_type in schema.FIELD_TYPES.items():
        if field not in fm:
            continue
        value = fm[field]
        # bool is a subclass of int in Python — reject bool for int fields explicitly
        if expected_type is int and isinstance(value, bool):
            die(
                f"{note_path}: {field} must be int, got bool "
                f"({value!r})"
            )
        if not isinstance(value, expected_type):
            die(
                f"{note_path}: {field} must be {expected_type.__name__}, "
                f"got {type(value).__name__} ({value!r})"
            )

    # 6. Drift case 3: enum values in the allowed set.
    for field, allowed_values in schema.ENUMS.items():
        if field not in fm:
            continue
        value = fm[field]
        if value not in allowed_values:
            die(
                f"{note_path}: {field}='{value}' is not a valid enum value. "
                f"Allowed: {sorted(allowed_values)}"
            )

    # 7. Literal fields have the exact required value.
    # schema_version is version-specific — v1 notes must have 1, v2 must have 2.
    # Other literals (plugin) are the same across all versions.
    for field, required_value in schema.LITERAL_VALUES.items():
        if field == "schema_version":
            # Already validated as a supported version in step 2b
            continue
        if fm.get(field) != required_value:
            die(
                f"{note_path}: {field} must be literally {required_value!r}, "
                f"got {fm.get(field)!r}"
            )

    # 8. Drift case 5: cross-field invariants.
    invariant_errors = schema.check_invariants(fm)
    if invariant_errors:
        joined = "; ".join(invariant_errors)
        die(f"{note_path}: invariant violation(s): {joined}")

    # 9. Drift case 6: canonical field order.
    # The on-disk YAML must serialize fields in FIELD_ORDER. Optional fields
    # slot into their canonical position when present; missing optional fields
    # are skipped without disturbing the order of what's present.
    actual_order = _extract_top_level_key_order(fm_yaml)
    expected_order = [f for f in field_order if f in fm]

    if actual_order != expected_order:
        die(
            f"{note_path}: frontmatter field order is wrong.\n"
            f"  Expected: {expected_order}\n"
            f"  Got:      {actual_order}"
        )


def _extract_top_level_key_order(yaml_text: str) -> list:
    """Return the top-level keys from a YAML text block in the order they appear.

    PyYAML's safe_load returns a dict (insertion-ordered in Python 3.7+) but
    PyYAML doesn't guarantee it preserves file order in all cases. Parsing the
    raw text is more reliable for an order check.

    Top-level keys are lines at column 0 that end in ':' — nested keys,
    list items, and continuation lines are skipped.
    """
    order = []
    for line in yaml_text.splitlines():
        # Skip blank lines, comments, list items, indented continuations
        if not line or line.startswith("#"):
            continue
        if line.startswith(" ") or line.startswith("\t") or line.startswith("-"):
            continue
        # Top-level key: "key:" or "key: value"
        if ":" in line:
            key = line.split(":", 1)[0].strip()
            if key:
                order.append(key)
    return order


if __name__ == "__main__":
    if len(sys.argv) == 2 and sys.argv[1] == "--stdin":
        content = sys.stdin.read()
        if not content.strip():
            sys.stderr.write("validate_frontmatter.py: --stdin received empty input\n")
            sys.exit(2)
        validate_content(content, "<stdin>")
    elif len(sys.argv) == 2:
        validate(sys.argv[1])
    else:
        sys.stderr.write("usage: validate_frontmatter.py <note-path> | --stdin\n")
        sys.exit(2)
    # If we reach here, all checks passed.
    sys.exit(0)
