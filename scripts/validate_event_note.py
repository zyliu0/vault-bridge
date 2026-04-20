"""Post-hoc fabrication-firewall audit for existing event notes (F3).

At write time, `event_writer.compose_body` returns a validator that the
scan command is supposed to run against every event-note body before the
vault write. When a careless caller bypasses that step (as the v14.1.0
field-report author did), invalid bodies can still land in the vault
because nothing outside the scan loop checks. This module is the
independent check that runs *after* the fact.

Call paths
----------

    python3 -m validate_event_note note_path.md [--json]
    # → audits a single .md file on disk

    from validate_event_note import audit_note_file
    audit_note_file("/vault/arch-projects/.../2024-08-01 facade-review.md")

    # vault-health integration can walk every event note via obsidian CLI:
    from validate_event_note import audit_body
    result = audit_body(body_text, note_kind="event")

What it checks
--------------

- event-note bodies must pass `event_writer.validate_event_note_body`
  (stop-word list + word-count bounds; see CLAUDE.md Core principle:
  fabrication firewall).
- metadata stubs are exempt — they are deterministic and have no
  prose to audit.
- The audit skips `verbatim-paste detection` post-hoc: that check
  needs the raw source text, which the vault does not preserve by
  design. This is documented; write-time callers still get the full
  check via the closure returned from `compose_body`.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import event_writer  # noqa: E402

# A body has the metadata-stub shape when the first non-blank line is a
# bullet that starts "- " and the body contains the fixed phrase that the
# stub template emits. Keep this loose: a user editing the stub by hand
# should still be recognised. We refuse to audit stubs as event notes.
_STUB_MARKERS = (
    "Not read — metadata only",
    "file type does not support extraction",
)


@dataclass
class NoteAudit:
    """Result of auditing one note body."""

    path: str = ""
    note_kind: str = "event"  # "event" | "stub" | "unknown"
    ok: bool = True
    reasons: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "path": self.path,
            "note_kind": self.note_kind,
            "ok": self.ok,
            "reasons": list(self.reasons),
        }


def _split_frontmatter(text: str) -> str:
    """Return the body (everything after the closing `---` fence).

    When the file has no frontmatter, return the whole text.
    """
    if not text.startswith("---"):
        return text
    # Look for closing fence on its own line.
    m = re.search(r"^---\s*$", text[3:], flags=re.MULTILINE)
    if m is None:
        return text
    return text[3 + m.end():].lstrip("\n")


def _detect_note_kind(body: str) -> str:
    if any(marker in body for marker in _STUB_MARKERS):
        return "stub"
    return "event"


def audit_body(body: str, *, note_kind: Optional[str] = None, path: str = "") -> NoteAudit:
    """Audit a single note body. `note_kind` is inferred when omitted."""
    kind = note_kind or _detect_note_kind(body)
    if kind == "stub":
        return NoteAudit(path=path, note_kind="stub", ok=True, reasons=[])
    result = event_writer.validate_event_note_body(body)
    return NoteAudit(
        path=path,
        note_kind="event",
        ok=result.ok,
        reasons=list(result.reasons),
    )


def audit_note_file(path: str) -> NoteAudit:
    """Audit a note file on disk by reading its body and delegating."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        return NoteAudit(
            path=path, note_kind="unknown", ok=False,
            reasons=[f"could not read file: {exc}"],
        )
    body = _split_frontmatter(text)
    return audit_body(body, path=path)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("note_path", help="Path to a note .md file on disk")
    parser.add_argument("--json", action="store_true", help="Emit JSON result")
    args = parser.parse_args(argv)

    audit = audit_note_file(args.note_path)
    if args.json:
        print(json.dumps(audit.to_dict(), ensure_ascii=False))
    else:
        status = "OK" if audit.ok else "FAIL"
        print(f"{status}  [{audit.note_kind}]  {audit.path}")
        for r in audit.reasons:
            print(f"  - {r}")
    return 0 if audit.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
