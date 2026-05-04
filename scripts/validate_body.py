#!/usr/bin/env python3
"""Body-quality validators for vault-bridge notes.

Two heuristic detectors used by ``vault-health`` and ``reconcile`` to
flag notes that need re-running through the scan pipeline:

* :func:`is_stale_legacy_stub` — detects pre-v16 ``> [!abstract] 摘要``
  stubs that the v16 stub-kind removal left behind (SSS-A field
  report, 2026-05-04). 24 notes in the SSS project carried a
  hardcoded ``来自源文档：`` prefix + CMap-decode garbage glyphs;
  no current reconcile codepath spotted them.
* :func:`is_garbled_extract` — detects long unbroken runs of digits
  and capital letters that PyPDF2 / python-pptx emit when they hit
  CID-keyed fonts they can't decode (SSS-B, also affects East-Asian
  PDFs). Used as a write-time gate in ``file_type_handlers`` to
  drop garbage before it lands in a note.

Neither detector raises. They return ``True`` / ``False`` for use as
filters or as guard conditions inside extractors.

Python 3.9 compatible.
"""
from __future__ import annotations

import re
from typing import List, Optional


# ---------------------------------------------------------------------------
# SSS-A — stale legacy abstract stub
# ---------------------------------------------------------------------------

# v16+ notes never use this Chinese abstract callout. Pre-v16 stubs used
# `> [!abstract] 摘要 — 源文档摘录` as the literal first line; the body
# then opened with `来自源文档：` (lit. "From source doc:") followed by
# whatever the extractor returned — including CMap-decode garbage like
# `2CBVVVVVVVVVVVVVVVVVVBB`. The combined signature is unique enough
# that false positives are essentially impossible.
_LEGACY_ABSTRACT_RE = re.compile(
    r">\s*\[!abstract\][^\n]*?(?:摘要|源文档摘录)",
    re.IGNORECASE,
)
_LEGACY_LEAD_RE = re.compile(r"来自源文档：")
# Stub bodies routinely contain CMap garbage of the form
# `2CBVVVVVVV…VVBB` — long runs of capitals. Match ANY 30+ char run
# that is exclusively letters/digits with no spaces. We require the
# body to also match the abstract OR lead pattern, so the run alone
# isn't a false positive in legitimate prose.
_GARBLE_RUN_RE = re.compile(r"[A-Z0-9]{40,}")
# Optional CMap signature sometimes printed verbatim by PyPDF2.
_CMAP_RE = re.compile(r"\bUVWXY\b|/UniGB|UniGB-UTF|/90ms-RKSJ", re.IGNORECASE)


def is_stale_legacy_stub(body: str) -> bool:
    """Return True when ``body`` looks like a pre-v16 abstract-stub note.

    The signature is the **conjunction** of two traits — the legacy
    Chinese ``> [!abstract] 摘要 — 源文档摘录`` callout AND either the
    ``来自源文档：`` lead-in or a CMap-garbage run. Notes written by
    the current pipeline don't ship this combination.

    Used by ``/vault-bridge:reconcile`` and ``/vault-bridge:vault-health``
    to flag notes for re-run through ``scan_pipeline.process_file``.
    """
    if not body:
        return False
    abstract_hit = bool(_LEGACY_ABSTRACT_RE.search(body))
    if not abstract_hit:
        return False
    if _LEGACY_LEAD_RE.search(body):
        return True
    if _GARBLE_RUN_RE.search(body) or _CMAP_RE.search(body):
        return True
    return False


# ---------------------------------------------------------------------------
# SSS-B — garbled CID-font extract dumps
# ---------------------------------------------------------------------------

# East-Asian-font PDFs / pre-2010 PowerPoints routinely produce
# multi-hundred-char unbroken digit runs ("567884008400520084006750…")
# when the extractor hits a CID-keyed font with no ToUnicode CMap.
# Same for unbroken capital runs and "UVWXY" / CMap-debug strings.
#
# Heuristic: text is "garbled" when long unbroken non-space runs make
# up a SUBSTANTIAL fraction of the total. We use two thresholds —
#   1. presence of any 60+ char unbroken non-space run AND
#   2. >25% of total chars consumed by 40+ char runs.
# Either signal alone risks false positives; both together is robust
# against legitimate URLs, hashes, and base64 blobs that occasionally
# show up in real prose.

_LONG_DIGIT_RUN_RE = re.compile(r"\d{40,}")
_LONG_CAPS_RUN_RE = re.compile(r"[A-Z]{40,}")
_LONG_NONSPACE_RUN_RE = re.compile(r"\S{60,}")
_CMAP_DEBUG_RE = re.compile(
    r"Advanced encoding /\S+ not implemented yet"
    r"|/UniGB-UTF16-H"
    r"|/Adobe-GB1"
    r"|/Adobe-Japan1"
    r"|/90ms-RKSJ-H",
)


def is_garbled_extract(text: str) -> bool:
    """Return True when extracted text looks like CID-font garbage.

    Use as a write-time gate in extractors:

        text = pdf_text(path)
        if is_garbled_extract(text):
            return ""   # better empty than misleading

    The detection is conservative — explicit CMap-debug strings
    short-circuit to ``True`` because PyPDF2 only emits them on
    failed decodes; everything else requires both a long unbroken
    run AND a high coverage ratio so legitimate content passes.
    """
    if not text:
        return False
    if _CMAP_DEBUG_RE.search(text):
        return True

    total = len(text)
    if total < 30:
        # Too short to judge — let it through. A real garbled extract
        # has at least one 60-char unbroken run.
        return False

    # CID-font garbage is characterised by MULTIPLICITY and
    # digit-domination, not a single homogeneous block. The
    # discriminators are deliberately conservative — `"D" * 101`
    # (a content-confidence test fixture) is not real garbage even
    # though it's a long unbroken cap run. Real garbage hits at
    # least one of:
    #   1. ≥ 2 distinct 40+ char digit runs, OR
    #   2. a single digit run ≥ 100 chars, OR
    #   3. a long unbroken non-space run AND >25% of total covered
    #      by 40+ char digit runs.
    digit_runs = list(_LONG_DIGIT_RUN_RE.finditer(text))
    if len(digit_runs) >= 2:
        return True
    if digit_runs:
        longest_digit = max(m.end() - m.start() for m in digit_runs)
        if longest_digit >= 100:
            return True
        digit_chars = sum(m.end() - m.start() for m in digit_runs)
        coverage = digit_chars / total
        has_extreme_run = bool(_LONG_NONSPACE_RUN_RE.search(text))
        if has_extreme_run and coverage > 0.25:
            return True
    # v16.7.0 (TLS Bug 2) — token-soup detector. DWG TEXT/MTEXT/ATTRIB
    # entities extract as 1-3 char tokens (station numbers, dimension
    # tags, layer labels, contour elevations). The result is honest
    # DXF text but useless for confidence-tagging — pre-v16.7 a 170 KB
    # output of `4 6 6 4 5 6 4 3 5 5 5 6 2 3 ...` got tagged
    # ``content_confidence: high``, which then made reconcile skip the
    # event in subsequent runs. The garbage became load-bearing.
    if is_token_soup(text):
        return True
    return False


_TOKEN_SOUP_MIN_CHARS = 5_000
_TOKEN_SOUP_MIN_TOKENS = 500
_TOKEN_SOUP_SHORT_RATIO = 0.85
_TOKEN_SOUP_DIGIT_RATIO = 0.60


def is_token_soup(text: str) -> bool:
    """Return True when ``text`` is dominated by short, digit-heavy
    tokens with no narrative — the canonical DWG TEXT/MTEXT extract
    shape.

    v16.7.0 (TLS Bug 2). Conservative — requires:
      * total length ≥ 5 000 chars (smaller is too easy to mis-flag)
      * ≥ 500 tokens after whitespace split
      * ≥ 85% of tokens are 1-3 chars
      * ≥ 60% of tokens are pure digits

    Real prose (CJK or otherwise) never hits this combination because
    word-tokens average longer than 3 chars and the digit ratio stays
    below 30% in any human-readable language. CAD coordinate dumps,
    survey-station labels, and dimension-tag exports trip every gate.
    """
    if not text or len(text) < _TOKEN_SOUP_MIN_CHARS:
        return False
    tokens = text.split()
    if len(tokens) < _TOKEN_SOUP_MIN_TOKENS:
        return False
    short_count = 0
    digit_count = 0
    for tok in tokens:
        if len(tok) <= 3:
            short_count += 1
        if tok.isdigit():
            digit_count += 1
    short_ratio = short_count / len(tokens)
    digit_ratio = digit_count / len(tokens)
    return (
        short_ratio >= _TOKEN_SOUP_SHORT_RATIO
        and digit_ratio >= _TOKEN_SOUP_DIGIT_RATIO
    )


def token_soup_stats(text: str) -> dict:
    """Return diagnostic stats describing token-soup characteristics.

    Used by ``garbled_extract_reasons`` to surface a meaningful
    warning to the user instead of a generic "text dropped" message.
    """
    if not text:
        return {"tokens": 0, "short_ratio": 0.0, "digit_ratio": 0.0}
    tokens = text.split()
    if not tokens:
        return {"tokens": 0, "short_ratio": 0.0, "digit_ratio": 0.0}
    short_count = sum(1 for t in tokens if len(t) <= 3)
    digit_count = sum(1 for t in tokens if t.isdigit())
    return {
        "tokens": len(tokens),
        "short_ratio": short_count / len(tokens),
        "digit_ratio": digit_count / len(tokens),
    }


def garbled_extract_reasons(text: str) -> List[str]:
    """Return a list of human-readable reasons explaining why ``text``
    is flagged as garbled. Empty list when not garbled.

    Used by extractors to populate ``ScanResult.warnings`` when they
    drop garbage instead of returning it.
    """
    reasons: List[str] = []
    if not text:
        return reasons
    if _CMAP_DEBUG_RE.search(text):
        reasons.append(
            "extracted text contains CMap-debug strings — PyPDF2/python-pptx "
            "could not decode a CID-keyed font; text dropped"
        )
        return reasons
    if not is_garbled_extract(text):
        return reasons
    # v16.7.0 — surface the token-soup case explicitly because it has
    # a different remedy (use page-image render, not a different text
    # extractor) than the CMap/digit-run cases.
    if is_token_soup(text):
        stats = token_soup_stats(text)
        reasons.append(
            f"token-soup: {stats['tokens']} tokens, "
            f"{int(stats['short_ratio'] * 100)}% short (≤3 chars), "
            f"{int(stats['digit_ratio'] * 100)}% digit-only"
        )
        reasons.append(
            "text dropped — likely CAD coordinate labels / station "
            "numbers / dimension tags. The handler ran but produced "
            "no narrative content; consider page-image render instead."
        )
        return reasons
    digit_runs = _LONG_DIGIT_RUN_RE.findall(text)
    if digit_runs:
        longest = max(len(r) for r in digit_runs)
        reasons.append(
            f"{len(digit_runs)} unbroken digit run(s) of 40+ chars "
            f"(longest: {longest})"
        )
    if reasons:
        reasons.append(
            "text dropped — likely CID-keyed font that the extractor could not decode"
        )
    return reasons


# ---------------------------------------------------------------------------
# CLI entry point — useful from /vault-bridge:vault-health
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse
    import sys
    parser = argparse.ArgumentParser(description="Validate a note body.")
    parser.add_argument(
        "--check",
        choices=["stale-stub", "garbled-extract"],
        required=True,
        help="Which detector to run.",
    )
    parser.add_argument(
        "path",
        nargs="?",
        help="Path to a file. If omitted, reads from stdin.",
    )
    args = parser.parse_args()

    if args.path:
        from pathlib import Path
        body = Path(args.path).read_text(encoding="utf-8", errors="replace")
    else:
        body = sys.stdin.read()

    if args.check == "stale-stub":
        flagged = is_stale_legacy_stub(body)
        print("STALE_STUB" if flagged else "OK")
        sys.exit(1 if flagged else 0)
    else:
        flagged = is_garbled_extract(body)
        print("GARBLED" if flagged else "OK")
        if flagged:
            for r in garbled_extract_reasons(body):
                print(f"  - {r}", file=sys.stderr)
        sys.exit(1 if flagged else 0)
