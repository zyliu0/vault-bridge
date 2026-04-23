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


# Kept in sync with scripts/vision_runner.py:_REFUSAL_PATTERNS. Inlined
# (rather than imported) so validate_frontmatter.py stays runnable as a
# standalone script without triggering vision_runner's heavier imports.
_REFUSAL_PATTERNS = (
    "i need permission",
    "i need your permission",
    "please approve",
    "permission prompt",
    "file read when prompted",
    "don't have permission",
    "do not have permission",
    "cannot read the image",
    "unable to read the image",
    "i'm unable to read",
    "i am unable to read",
)


def _looks_like_refusal(caption: str) -> bool:
    low = (caption or "").strip().lower()
    if not low:
        return False
    head = low[:200]
    return any(pat in head for pat in _REFUSAL_PATTERNS)


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

    # 2c. Branch on note_type (v15.1.0). Project-index MOCs have their own
    # required/optional field set — MOC-specific fields (status,
    # timeline_start/end, parties, budget) are valid there but unknown on
    # event notes. Pre-v15.1 every MOC the plugin generated failed its own
    # validator because the generic event-note branch flagged these.
    if schema.is_moc_frontmatter(fm):
        _validate_moc(fm, note_path)
        return

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

    # 7b. Semantic check on image_captions (v14.7.4 red-line).
    #
    # The cross-field invariant (attachments length vs image_captions
    # length) is checked in schema.check_invariants; here we catch the
    # stronger failure mode: captions that are permission-refusal text
    # ("I need permission to read the image file...") or suspiciously
    # short non-empty strings. Empty slots stay valid — a legitimately
    # failed per-image caption records "" and the scan surfaces it via
    # memory-report warnings, but poisoned captions must never reach
    # disk. See scripts/vision_runner.py:is_refusal_caption.
    captions = fm.get("image_captions")
    if isinstance(captions, list):
        for i, cap in enumerate(captions):
            if not isinstance(cap, str):
                continue  # schema's type check will flag this
            stripped = cap.strip()
            if not stripped:
                continue
            if _looks_like_refusal(stripped):
                die(
                    f"{note_path}: image_captions[{i}] matches a "
                    f"permission-refusal pattern — the vision backend "
                    f"returned a refusal instead of a caption: "
                    f"{stripped[:120]!r}"
                )
            word_count = len(stripped.split())
            if word_count < 5:
                die(
                    f"{note_path}: image_captions[{i}] is too short "
                    f"({word_count} word(s)); expected at least 5 words "
                    f"of description: {stripped!r}"
                )

    # 8. Drift case 5: cross-field invariants.
    invariant_errors = schema.check_invariants(fm)
    if invariant_errors:
        joined = "; ".join(invariant_errors)
        die(f"{note_path}: invariant violation(s): {joined}")

    # 9. Field order: NOT enforced (v15.0.0 — Issue 2 priority 4a).
    # YAML dicts are unordered; Obsidian, dataviews, bases, and every
    # downstream consumer tolerate any order. Pre-v15 the validator
    # rejected notes whose frontmatter was shuffled — a rule only the
    # validator cared about. Reordering tolerance keeps the required
    # field list, the type checks, the enum checks, and the invariants;
    # field order is advisory only.


def _validate_moc(fm: dict, note_path: str) -> None:
    """Validate a project-index MOC frontmatter.

    MOC fields come from ``scripts/schema.py``:
      Required: schema_version, plugin, domain, project, note_type.
      Optional: status, timeline_start, timeline_end, parties, budget,
                tags, cssclasses.
    """
    req = set(schema.get_moc_required_fields())
    opt = set(schema.get_moc_optional_fields())
    allowed = req | opt

    unknown = set(fm.keys()) - allowed
    if unknown:
        die(
            f"{note_path}: project-index MOC has unknown field(s): "
            f"{sorted(unknown)}. Allowed: {sorted(allowed)}"
        )

    missing = req - set(fm.keys())
    if missing:
        die(
            f"{note_path}: project-index MOC missing required field(s): "
            f"{sorted(missing)}"
        )

    for field, expected_type in schema.MOC_FIELD_TYPES.items():
        if field not in fm:
            continue
        value = fm[field]
        if expected_type is int and isinstance(value, bool):
            die(f"{note_path}: {field} must be int, got bool ({value!r})")
        if not isinstance(value, expected_type):
            die(
                f"{note_path}: {field} must be {expected_type.__name__}, "
                f"got {type(value).__name__} ({value!r})"
            )

    for field, allowed_values in schema.MOC_ENUMS.items():
        if field not in fm:
            continue
        value = fm[field]
        if value not in allowed_values:
            die(
                f"{note_path}: {field}='{value}' is not a valid enum "
                f"value. Allowed: {sorted(allowed_values)}"
            )

    for field, required_value in schema.MOC_LITERALS.items():
        if field == "schema_version":
            continue  # already validated
        if fm.get(field) != required_value:
            die(
                f"{note_path}: {field} must be literally "
                f"{required_value!r}, got {fm.get(field)!r}"
            )

    invariant_errors = schema.check_moc_invariants(fm)
    if invariant_errors:
        joined = "; ".join(invariant_errors)
        die(f"{note_path}: MOC invariant violation(s): {joined}")


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
