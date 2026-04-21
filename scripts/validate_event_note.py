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

# Legacy pre-v14 fallback shape that the v14.3.0→v14.4.0 reconcile flags
# as low-fidelity: the pipeline pasted the raw extracted text under a
# `## Excerpt from source` heading instead of synthesising prose. Such
# notes look "valid" by abstract/word-count rules but violate the
# fabrication firewall (verbatim paste is exactly what we forbid at
# write time; see event_writer.STOP_WORDS cousin). Flag explicitly.
_LEGACY_EXCERPT_MARKERS = (
    "## Excerpt from source",
)

# A body is rendered as a project-index MOC when its content starts with
# a level-1 `# <project_name>` heading followed by the `## Status` block.
# Those notes have `note_type: project-index` in frontmatter — the
# audit_note_file path inspects frontmatter directly. For bodies passed
# in without frontmatter, the Status / Timeline / Subfolders triple is
# a reliable fingerprint.
_MOC_BODY_MARKERS = (
    "## Status",
    "## Timeline (all events)",
    "## Subfolders",
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


def _split_frontmatter(text: str) -> tuple:
    """Return (frontmatter_text, body_text).

    `frontmatter_text` is the raw YAML between the `---` fences (without
    the fences themselves); empty string when no frontmatter. `body_text`
    is everything after the closing fence.
    """
    if not text.startswith("---"):
        return "", text
    m = re.search(r"^---\s*$", text[3:], flags=re.MULTILINE)
    if m is None:
        return "", text
    fm = text[3:3 + m.start()]
    body = text[3 + m.end():].lstrip("\n")
    return fm, body


def _detect_note_kind(body: str, frontmatter: str = "") -> str:
    """Classify the note body.

    Returns one of: "moc" | "stub" | "legacy_excerpt" | "event".
    """
    if frontmatter and re.search(
        r"^note_type:\s*project-index\s*$", frontmatter, re.MULTILINE
    ):
        return "moc"
    if all(marker in body for marker in _MOC_BODY_MARKERS):
        return "moc"
    if any(marker in body for marker in _STUB_MARKERS):
        return "stub"
    if any(marker in body for marker in _LEGACY_EXCERPT_MARKERS):
        return "legacy_excerpt"
    return "event"


def _attachment_embed_drift_reasons(body: str, frontmatter: str) -> List[str]:
    """Return a list of drift reasons comparing FM `attachments:` to body embeds.

    Attachment-drift (field-review v14.4.1, Issue 3c): after reconcile
    dedup/size-gating, the `attachments:` YAML list and the `![[...]]`
    embeds in the body can diverge. The scan loop writes them together
    but downstream passes mutate only one side.
    """
    if not frontmatter:
        return []

    # Parse attachments list out of frontmatter — minimal YAML list form.
    attachments = []
    m = re.search(
        r"^attachments:\s*\n((?:\s+-\s.*\n?)+)",
        frontmatter,
        re.MULTILINE,
    )
    if m:
        for line in m.group(1).splitlines():
            entry = re.match(r"\s+-\s+(.*)", line)
            if entry:
                val = entry.group(1).strip().strip("'\"")
                if val:
                    attachments.append(val)

    embeds = re.findall(r"!\[\[([^\]]+?)\]\]", body)
    # Strip any display-name alias after `|`
    embeds = [e.split("|", 1)[0].strip() for e in embeds]

    reasons = []
    if attachments and not embeds:
        reasons.append(
            f"attachments frontmatter lists {len(attachments)} file(s) but the body has NO `![[...]]` embeds"
        )
    elif not attachments and embeds:
        reasons.append(
            f"body has {len(embeds)} `![[...]]` embed(s) but frontmatter attachments is empty"
        )
    elif len(attachments) != len(embeds):
        reasons.append(
            f"attachments count ({len(attachments)}) does not match body embed count ({len(embeds)})"
        )
    else:
        missing_in_body = [a for a in attachments if a not in embeds]
        if missing_in_body:
            reasons.append(
                f"{len(missing_in_body)} attachment(s) listed in frontmatter but not embedded in body: "
                f"{missing_in_body[:3]}"
            )
    return reasons


def audit_body(
    body: str,
    *,
    note_kind: Optional[str] = None,
    path: str = "",
    frontmatter: str = "",
) -> NoteAudit:
    """Audit a single note body. `note_kind` is inferred when omitted."""
    kind = note_kind or _detect_note_kind(body, frontmatter)

    if kind == "moc":
        # MOC bodies are not event notes; abstract/word-count rules do not apply.
        return NoteAudit(path=path, note_kind="moc", ok=True, reasons=[])

    if kind == "stub":
        # Stubs are deterministic; audit attachment drift only.
        drift = _attachment_embed_drift_reasons(body, frontmatter)
        return NoteAudit(
            path=path, note_kind="stub",
            ok=not drift, reasons=drift,
        )

    if kind == "legacy_excerpt":
        # Low-fidelity fallback body from pre-v14 writes — cannot be
        # salvaged via the normal firewall; flag explicitly so the user
        # knows to regenerate via `/vault-bridge:reconcile --rewrite-bodies`.
        return NoteAudit(
            path=path, note_kind="legacy_excerpt", ok=False,
            reasons=[
                "body uses the legacy `## Excerpt from source` fallback shape — "
                "this is a pre-v14 verbatim-paste write. Regenerate via "
                "`/vault-bridge:reconcile --rewrite-bodies` to replace with synthesised prose."
            ],
        )

    # Event note — run the core validator, then add body/FM drift checks.
    result = event_writer.validate_event_note_body(body)
    reasons = list(result.reasons)
    reasons.extend(_attachment_embed_drift_reasons(body, frontmatter))
    return NoteAudit(
        path=path,
        note_kind="event",
        ok=not reasons,
        reasons=reasons,
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
    frontmatter, body = _split_frontmatter(text)
    return audit_body(body, path=path, frontmatter=frontmatter)


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
