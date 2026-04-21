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
) -> Tuple[List[str], List[str]]:
    """Caption each image at `image_paths` in order.

    Args:
        image_paths: ordered list of local JPEG/PNG paths.
        event_meta: event context dict (project, event_date, source_basename).
        backend: see module docstring. "auto" picks the first available.
        model: model name forwarded to the backend.
        prompt_builder: defaults to `image_vision.caption_prompt_for`.
            Override for tests.

    Returns:
        (captions, warnings). `captions` is index-aligned with
        `image_paths`; slots for failed images are `""`. `warnings` is
        a list of human-readable strings for the scan's memory report.
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
        captions.append(_clean_caption(caption))
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
    Falls back to empty on non-zero exit or timeout.
    """
    cmd = ["claude", "-p", prompt]
    # --model is optional; some claude versions don't accept it. Try
    # with first and fall through if argparse rejects.
    try_cmd = cmd + ["--model", model]
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
                cmd,
                capture_output=True, text=True,
                timeout=_CAPTION_TIMEOUT_SECS,
            )
        except subprocess.TimeoutExpired:
            return ""
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "claude CLI non-zero exit")
    return r.stdout.strip()


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
