"""Actually execute per-image caption prompts and populate `ScanResult.image_captions`.

Field-review v14.4.1, Issue 2: the plugin ships `image_vision.caption_prompt_for`
(which builds prompt strings) and `image_vision.select_top_k` (which ranks
captions), but nothing in the plugin actually *calls* a vision model. The
contract was "skill runs it" — retro-scan.md documented the loop, but
markdown instructions do not enforce behaviour. Result: every written note
had `image_captions=[]`, so `compose_body` never had image evidence to
ground the prose with, and users saw short / verbatim-pasted bodies.

This module closes the loop. `run_captions(image_paths, event_meta)`
calls a vision backend for each image and returns a list of captions
(one per image, index-aligned). The scan command sets
`result.image_captions = run_captions(result.image_candidate_paths, meta)`
before calling `event_writer.compose_body`.

Backends
--------
- `"auto"` (default): pick the first available backend at runtime. The
  order is: anthropic SDK (if `anthropic` package installed and
  `ANTHROPIC_API_KEY` set) → claude-cli subprocess (if `claude` is on
  PATH) → stub (always empty captions).
- `"anthropic"`: use the `anthropic` SDK against the Messages API.
  Fast; requires `ANTHROPIC_API_KEY`.
- `"claude_cli"`: shell out to `claude -p <prompt>`. Uses the user's
  existing Claude Code auth — no API key needed. Slower because each
  call spawns a new CLI session.
- `"stub"`: never calls a model; always returns `""` per image. Used
  by dry-runs and unit tests. Makes the `image_captions=[]` observable.

Failure modes
-------------
- Image file missing → caption = `""`, warning recorded.
- Model returns error → caption = `""`, warning recorded, DOES NOT
  abort the scan.
- Empty caption strings are filtered out by the caller (via
  `image_vision.select_top_k`) but persist in the index-aligned list
  so downstream code knows which slots are blank.

Persisted as frontmatter
------------------------
Callers write the returned list into the event-note frontmatter under
`image_captions:`, aligned by index with `attachments:`. Reconciles
that only mutate attachments (e.g. dedup) must update `image_captions`
in lock-step — the schema invariant enforces this.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import shutil
import subprocess
from pathlib import Path
from typing import Callable, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)


# Vision-capable defaults. Users can override via env vars, and the
# `model` parameter to `run_captions` wins over both.
_DEFAULT_MODEL = "claude-haiku-4-5"

# The Messages API v1 endpoint. Hardcoded because the Anthropic SDK
# uses the same constant internally; override via
# ANTHROPIC_BASE_URL env var if testing against a staging endpoint.
_API_ENDPOINT = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"

# Per-caption timeout (seconds). Vision calls on a 1200px JPEG via
# Haiku are typically < 5s; this is the ceiling before we give up and
# record a blank caption.
_CAPTION_TIMEOUT_SECS = 45.0

# Batched-call ceiling. Option D (v14.7.4): one claude -p call covers
# every candidate image in an event. Field test was 22s for 10 images;
# 180s gives headroom for 20 (the IMAGE_CANDIDATE_CAP).
_BATCH_CAPTION_TIMEOUT_SECS = 180.0

# Refusal-pattern catalogue. When a `claude -p` subprocess runs without
# `--dangerously-skip-permissions` (or hits a sandbox that denies file
# reads), the child session returns a polite "I need permission to read
# the image file..." instead of a caption. Pre-v14.7.4 every such string
# flowed through `_clean_caption` and shipped into `image_captions` as
# if it were a real description; scans with `images_embedded=N` were
# silently poisoned. Matching any of these opens the red-line: the run
# aborts, it does NOT degrade to stub/metadata.
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


def is_refusal_caption(caption: str) -> bool:
    """Return True if `caption` looks like a permission-refusal string.

    Public so `validate_frontmatter.py` can share the detector. Match is
    case-insensitive, anchored to the opening 200 chars — refusals are
    always the entire response, never buried inside a valid caption.
    """
    low = (caption or "").strip().lower()
    if not low:
        return False
    head = low[:200]
    return any(pat in head for pat in _REFUSAL_PATTERNS)


def _detect_backend() -> str:
    """Return the first available backend for `backend='auto'`."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401
            return "anthropic"
        except ImportError:
            pass
    if shutil.which("claude"):
        return "claude_cli"
    return "stub"


def run_captions(
    image_paths: Sequence[str],
    event_meta: dict,
    *,
    backend: str = "auto",
    model: str = _DEFAULT_MODEL,
    prompt_builder: Optional[Callable[[str, dict], str]] = None,
    batch: Optional[bool] = None,
) -> Tuple[List[str], List[str]]:
    """Caption each image at `image_paths` in order.

    Args:
        image_paths: ordered list of local JPEG/PNG paths.
        event_meta: event context dict (project, event_date, source_basename).
        backend: see module docstring. "auto" picks the first available.
        model: model name forwarded to the backend.
        prompt_builder: defaults to `image_vision.caption_prompt_for`.
            Override for tests.
        batch: when the backend is `claude_cli`, send all images in a
            single subprocess call (Option D, v14.7.4). Default: True
            when `len(image_paths) > 1`. Pass `False` to force the
            legacy per-image loop (used by tests that stub each call).
            Ignored for other backends.

    Returns:
        (captions, warnings). `captions` is index-aligned with
        `image_paths`; slots for missing image files are `""`. `warnings`
        is a list of human-readable strings for the scan's memory report.

    Raises:
        RuntimeError: the red-line — if any backend response matches a
            permission-refusal pattern, or the batched claude-cli call
            returns no parseable output, `run_captions` raises. Scans
            must never silently ship a note whose `image_captions`
            contains a refusal string masquerading as a description.
        FileNotFoundError: every path in `image_paths` is missing on
            disk (the pipeline tore down its tmp dir early).
    """
    if prompt_builder is None:
        import image_vision
        prompt_builder = image_vision.caption_prompt_for

    actual_backend = backend if backend != "auto" else _detect_backend()

    runner = _BACKEND_REGISTRY.get(actual_backend)
    if runner is None:
        return [""] * len(image_paths), [
            f"vision_runner: unknown backend {actual_backend!r}; "
            f"no captions produced"
        ]

    # Dangling-path probe (v14.7.1). If EVERY candidate path is gone, the
    # upstream scan pipeline has torn down its tempdir before captioning.
    # That used to fall through to empty captions silently — pre-v14.7.1
    # every scan shipped notes with placeholder captions and nobody
    # noticed. Raise loudly now so the bug can never hide again. Per-image
    # missing files (rare edge case: vault moved mid-scan) still fall
    # through to the warning path below.
    if image_paths:
        missing = [p for p in image_paths if not Path(p).exists()]
        if len(missing) == len(image_paths):
            raise FileNotFoundError(
                f"vision_runner: all {len(missing)} image paths are missing "
                f"(first: {image_paths[0]!r}). The scan pipeline likely "
                f"destroyed its tmp dir before captioning — see "
                f"scan_pipeline._scan_tmp_root / cleanup_scan_tmp."
            )

    # Option D (v14.7.4): batched claude_cli path. One subprocess for
    # every image in the event instead of N — 7× faster in field tests
    # (22s vs 160s for 10 images). Other backends stay per-image.
    use_batch = batch if batch is not None else (len(image_paths) > 1)
    if use_batch and actual_backend == "claude_cli" and len(image_paths) > 1:
        return _run_captions_batched_cli(image_paths, event_meta, model)

    captions: List[str] = []
    warnings: List[str] = []
    for path in image_paths:
        if not Path(path).exists():
            captions.append("")
            warnings.append(f"vision_runner: image missing: {path}")
            continue
        prompt = prompt_builder(path, event_meta)
        try:
            caption = runner(path, prompt, model)
        except Exception as exc:
            captions.append("")
            warnings.append(
                f"vision_runner[{actual_backend}] failed for "
                f"{Path(path).name}: {exc}"
            )
            continue
        cleaned = _clean_caption(caption)
        if is_refusal_caption(cleaned):
            raise RuntimeError(
                f"vision_runner[{actual_backend}]: permission-refusal text "
                f"returned for {Path(path).name} instead of a caption. "
                f"The subprocess could not read the image — re-run with "
                f"`--dangerously-skip-permissions` or an interactive session. "
                f"Preview: {cleaned[:160]!r}"
            )
        captions.append(cleaned)

    # Surface the empty-caption ratio so the scan's memory report shows
    # "captions unavailable" instead of burying it per-event (v14.7.1).
    empty_count = sum(1 for c in captions if not c.strip())
    if empty_count and image_paths:
        warnings.append(
            f"vision_runner[{actual_backend}]: {empty_count}/{len(image_paths)} "
            f"captions came back empty — prose synthesis will lack visual grounding"
        )
    return captions, warnings


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------

def _stub_backend(image_path: str, prompt: str, model: str) -> str:
    """Never call a model. Returns empty string."""
    return ""


def _anthropic_backend(image_path: str, prompt: str, model: str) -> str:
    """Call the Anthropic Messages API via the anthropic SDK.

    Requires ANTHROPIC_API_KEY in the environment; the SDK picks it up.
    """
    import anthropic
    client = anthropic.Anthropic()  # picks up ANTHROPIC_API_KEY
    media_type = _guess_media_type(image_path)
    with open(image_path, "rb") as f:
        data = base64.standard_b64encode(f.read()).decode("ascii")
    response = client.messages.create(
        model=model,
        max_tokens=120,
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": media_type,
                            "data": data,
                        },
                    },
                    {"type": "text", "text": prompt},
                ],
            }
        ],
    )
    # Concatenate any text blocks in the response.
    parts = []
    for block in getattr(response, "content", []) or []:
        if getattr(block, "type", "") == "text":
            parts.append(getattr(block, "text", ""))
    return "".join(parts).strip()


def _claude_cli_backend(image_path: str, prompt: str, model: str) -> str:
    """Call `claude -p <prompt>` and return stdout.

    The prompt embeds the image path; the CLI's Read tool will open it.
    Uses `--dangerously-skip-permissions` so the non-interactive
    subprocess does not get stuck on a file-read permission prompt
    (v14.7.4 red-line: pre-v14.7.4 every claude_cli caption returned
    a "I need permission to read the image file..." refusal that was
    silently written to `image_captions`). Falls back to empty on
    timeout; raises on non-zero exit.
    """
    base_cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
    # --model is optional; some claude versions don't accept it. Try
    # with first and fall through if argparse rejects.
    try_cmd = base_cmd + ["--model", model]
    try:
        r = subprocess.run(
            try_cmd,
            capture_output=True, text=True,
            timeout=_CAPTION_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired:
        return ""
    if r.returncode != 0:
        # Retry without --model for older CLIs
        try:
            r = subprocess.run(
                base_cmd,
                capture_output=True, text=True,
                timeout=_CAPTION_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired:
            return ""
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "claude CLI non-zero exit")
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# Batched claude_cli backend (Option D from v14.7.4 field-report Issue 1)
# ---------------------------------------------------------------------------

def _build_batch_prompt(image_paths: Sequence[str], event_meta: dict) -> str:
    """Build a single prompt covering every image in the event."""
    project = event_meta.get("project", "-") or "-"
    event_date = event_meta.get("event_date", "-") or "-"
    source = event_meta.get("source_basename", "-") or "-"
    lines = [
        "You are a vision captioner for the vault-bridge plugin.",
        "",
        "Read every image file listed below and write ONE caption per",
        "image. Use your Read tool in parallel (a single tool-use block",
        "with multiple Read calls).",
        "",
        f"Project: {project}",
        f"Event date: {event_date}",
        f"Source: {source}",
        "",
        "Images to caption:",
    ]
    for i, p in enumerate(image_paths, 1):
        lines.append(f"{i}. {p}")
    lines.extend([
        "",
        "Output format — exactly one line per image, in the same order",
        "as listed, using the image's basename as the key:",
        "",
        "  <basename>: <single-sentence caption, roughly 15-25 words>",
        "",
        "Caption guidance:",
        "  - Describe what is visible: materials, composition, spatial",
        "    relationships, people and their role/state, legible text",
        "    (quote short labels), construction/install state.",
        "  - One sentence. No preamble, no trailing commentary, no",
        "    empty lines between captions.",
        "  - If an image is rotated, say so.",
        "  - Do not request permission — Read directly.",
        "",
        f"Emit exactly {len(image_paths)} lines, no more, no less.",
    ])
    return "\n".join(lines)


def _parse_batch_output(stdout: str, image_paths: Sequence[str]) -> List[str]:
    """Parse `<basename>: <caption>` lines into an index-aligned list.

    Missing entries are returned as "". Extra lines that don't match a
    known basename are discarded. Tolerant of leading list markers
    ("1.", "-", "*", "•") the model sometimes emits.
    """
    by_basename: dict = {}
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        if not line or ":" not in line:
            continue
        head, _, tail = line.partition(":")
        head = head.strip().lstrip("-*•").strip()
        # Strip leading numeric list markers like "1." or "01)"
        while head and head[0].isdigit():
            head = head[1:]
        head = head.lstrip(".)-:").strip()
        tail = tail.strip().strip("\"'")
        if head and tail:
            by_basename[head] = tail
    return [by_basename.get(Path(p).name, "") for p in image_paths]


def _run_captions_batched_cli(
    image_paths: Sequence[str],
    event_meta: dict,
    model: str,
) -> Tuple[List[str], List[str]]:
    """Single `claude -p` call covering every existing image.

    Returns (captions, warnings) aligned with `image_paths`. Missing
    files are skipped with per-image warnings. Raises on timeout,
    non-zero exit, or any caption matching a refusal pattern.
    """
    warnings: List[str] = []
    existing: List[Tuple[int, str]] = []
    for i, p in enumerate(image_paths):
        if Path(p).exists():
            existing.append((i, p))
        else:
            warnings.append(f"vision_runner: image missing: {p}")

    if not existing:
        return [""] * len(image_paths), warnings

    existing_paths = [p for _, p in existing]
    prompt = _build_batch_prompt(existing_paths, event_meta)
    base_cmd = ["claude", "-p", prompt, "--dangerously-skip-permissions"]
    try_cmd = base_cmd + ["--model", model]

    stdout = ""
    try:
        r = subprocess.run(
            try_cmd,
            capture_output=True, text=True,
            timeout=_BATCH_CAPTION_TIMEOUT_SECS,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"vision_runner[claude_cli]: batched caption call timed out after "
            f"{_BATCH_CAPTION_TIMEOUT_SECS}s covering {len(existing_paths)} image(s)"
        ) from exc

    if r.returncode != 0:
        # Older CLIs may reject --model; retry without.
        try:
            r = subprocess.run(
                base_cmd,
                capture_output=True, text=True,
                timeout=_BATCH_CAPTION_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(
                f"vision_runner[claude_cli]: batched caption call timed out after "
                f"{_BATCH_CAPTION_TIMEOUT_SECS}s (retry without --model)"
            ) from exc
        if r.returncode != 0:
            raise RuntimeError(
                f"vision_runner[claude_cli]: batched call returned non-zero exit: "
                f"{(r.stderr or '').strip() or 'no stderr'}"
            )
    stdout = r.stdout or ""

    # Whole-body refusal check — child session refused before producing
    # per-image lines (e.g., sandbox blocked Read even with the flag).
    if is_refusal_caption(stdout):
        raise RuntimeError(
            f"vision_runner[claude_cli]: batched call returned a "
            f"permission-refusal body for {len(existing_paths)} image(s). "
            f"Preview: {stdout[:200]!r}"
        )

    parsed = _parse_batch_output(stdout, existing_paths)
    if not any(c.strip() for c in parsed):
        raise RuntimeError(
            f"vision_runner[claude_cli]: batched call produced no parseable "
            f"`<basename>: <caption>` lines for {len(existing_paths)} image(s). "
            f"Raw stdout preview: {stdout[:200]!r}"
        )

    # Reassemble aligned with original image_paths; per-caption refusal
    # check catches the rare case where the child partially refused.
    captions: List[str] = [""] * len(image_paths)
    for (orig_i, _), raw_cap in zip(existing, parsed):
        cleaned = _clean_caption(raw_cap)
        if is_refusal_caption(cleaned):
            raise RuntimeError(
                f"vision_runner[claude_cli]: caption slot {orig_i} matched a "
                f"refusal pattern within the batched response. "
                f"Preview: {cleaned[:160]!r}"
            )
        captions[orig_i] = cleaned

    empty_count = sum(1 for c in captions if not c.strip())
    if empty_count:
        warnings.append(
            f"vision_runner[claude_cli]: {empty_count}/{len(image_paths)} "
            f"captions came back empty — prose synthesis will lack visual grounding"
        )
    return captions, warnings


_BACKEND_REGISTRY = {
    "stub": _stub_backend,
    "anthropic": _anthropic_backend,
    "claude_cli": _claude_cli_backend,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _guess_media_type(path: str) -> str:
    ext = Path(path).suffix.lower().lstrip(".")
    return {
        "jpg": "image/jpeg",
        "jpeg": "image/jpeg",
        "png": "image/png",
        "gif": "image/gif",
        "webp": "image/webp",
    }.get(ext, "image/jpeg")


# Preambles we observed the LLM emitting despite the prompt's "return
# only the sentence" rule. Case-insensitive match at start of string.
_CAPTION_PREAMBLES = (
    "here's the caption:",
    "here is the caption:",
    "here is a caption:",
    "caption:",
)


def _clean_caption(raw: str) -> str:
    """Strip preamble, surrounding quotes, and collapse whitespace."""
    s = (raw or "").strip()
    # Strip leading bullet chars and quote marks
    s = s.lstrip("-*• \t\"'`")
    s = s.rstrip("\"'`")
    # If multi-line, keep the first non-empty line (caption contract is
    # one sentence; anything after is usually LLM chatter).
    for line in s.splitlines():
        line = line.strip()
        if line:
            s = line
            break
    # Strip known preambles (case-insensitive).
    low = s.lower()
    for preamble in _CAPTION_PREAMBLES:
        if low.startswith(preamble):
            s = s[len(preamble):].lstrip(" \t\"'")
            low = s.lower()
    return s.strip()


# ---------------------------------------------------------------------------
# CLI entry point — run captions for a JSON batch on stdin
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    """CLI: read a JSON batch from stdin, print a JSON result.

    Input (stdin, one JSON object):
        {
            "images": ["/path/1.jpg", "/path/2.jpg"],
            "event_meta": {"project": "...", "event_date": "...", "source_basename": "..."},
            "backend": "auto",
            "model": "claude-haiku-4-5"
        }

    Output (stdout):
        {"captions": ["...", "..."], "warnings": [...]}
    """
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backend", default="auto",
                        choices=list(_BACKEND_REGISTRY.keys()) + ["auto"])
    parser.add_argument("--model", default=_DEFAULT_MODEL)
    args = parser.parse_args(argv)

    import sys
    payload = json.loads(sys.stdin.read() or "{}")
    images = payload.get("images") or []
    meta = payload.get("event_meta") or {}
    backend = payload.get("backend") or args.backend
    model = payload.get("model") or args.model
    captions, warnings = run_captions(
        images, meta, backend=backend, model=model,
    )
    sys.stdout.write(json.dumps({"captions": captions, "warnings": warnings}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
