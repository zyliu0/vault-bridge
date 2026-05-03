"""Sanctioned text-write path to the Obsidian vault.

Bug A, v16.2.0. The previous regime had every autocompose driver
inventing its own ``fast_write`` that ``Path.write_text``ed directly
to a guessed vault filesystem root — which produced wrong-vault-path
data-loss in field reports. This module is the one and only sanctioned
write path.

Three public entry points, all going through ``obsidian eval``:

* ``write_note(vault_name, vault_path, content)`` — single-note write.
* ``write_notes_batch(vault_name, items)`` — many notes in one
  ``obsidian eval`` invocation; chunk by ``chunk_size`` to stay under
  ARG_MAX. Per-event subprocess overhead drops from ~150 ms to <2 ms
  with batches of 50, which removes the throughput rationale for
  any FS-direct bypass.
* ``probe(vault_name)`` — round-trips a 1-byte canary into ``_vb-probe/``
  and deletes the namespace. Scan commands MUST call this at start
  and abort on failure; never fall back.

There is intentionally NO ``vault_fs_root()``, ``fast_write()``, or
filesystem helper. The lack of one is the forcing function that keeps
drivers from regressing.

Companion to ``vault_binary.py`` (binary attachments). The contract
mirrors it: ``{ok, vault_path, bytes_written, error}`` for single
writes; the batch entry point adds aggregate counts plus a list of
per-item results.

Python 3.9 compatible.
"""
import json
import subprocess
from typing import Callable, Dict, Iterable, List, Optional, Tuple

# Default chunk size for write_notes_batch. ARG_MAX on macOS is
# typically 1 MiB; with 50 average notes (~2 KiB body) we sit at
# ~100 KiB of JSON in a single argv string, well under the limit.
DEFAULT_CHUNK_SIZE = 50

# Probe namespace — one folder, deleted after every probe. Kept short
# to minimise vault-side noise if cleanup is ever interrupted.
_PROBE_FOLDER = "_vb-probe"
_PROBE_FILENAME = "vault-bridge-probe.md"
# 1-byte canary keeps the round-trip cheap and the size assertion
# trivial — any drift on bytes_written is a real signal, not a
# trailing-newline argument.
_PROBE_CONTENT = "."


# ---------------------------------------------------------------------------
# Subprocess plumbing
# ---------------------------------------------------------------------------

def _default_runner(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def _strip_obsidian_eval_stdout(stdout: str) -> str:
    """``obsidian eval`` prefixes its result with ``=> `` and may wrap
    string returns in double quotes. Peel both off so downstream JSON
    decode sees just the payload."""
    out = (stdout or "").strip()
    if out.startswith("=> "):
        out = out[3:].strip()
    if len(out) >= 2 and out[0] == out[-1] == '"':
        try:
            out = json.loads(out)
        except json.JSONDecodeError:
            pass
    return out


def _parse_eval_result(stdout: str) -> Optional[dict]:
    """Best-effort decode of the JSON our JS snippets return. Returns
    None if stdout is missing or non-JSON."""
    payload = _strip_obsidian_eval_stdout(stdout)
    if not payload:
        return None
    try:
        data = json.loads(payload) if isinstance(payload, str) else payload
    except (json.JSONDecodeError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# ---------------------------------------------------------------------------
# Validation — Bug A firewall
# ---------------------------------------------------------------------------

def _validate_vault_path(vault_path: str) -> str:
    if vault_path is None or not str(vault_path).strip():
        raise ValueError("vault_path must be non-empty")
    p = str(vault_path).strip()
    if p.startswith("/") or p.startswith("\\") or (len(p) >= 2 and p[1] == ":"):
        # Absolute path on POSIX, UNC, or drive-lettered Windows path.
        # All three are signs the caller is reaching for a vault
        # filesystem root — exactly what Bug A forbids.
        raise ValueError(
            f"vault_path must be vault-relative, never absolute: {vault_path!r}. "
            "vault-bridge has no filesystem fallback; use the {domain}/{project}/... form."
        )
    return p


def _validate_vault_name(vault_name: str) -> str:
    if vault_name is None or not str(vault_name).strip():
        raise ValueError("vault_name must be non-empty")
    return str(vault_name).strip()


# ---------------------------------------------------------------------------
# JS snippets
# ---------------------------------------------------------------------------

def _build_write_note_js(vault_path: str, content: str) -> str:
    """Return the JS that writes one note via app.vault.create or
    app.vault.modify. Path and content are JSON-escaped so CJK,
    spaces, apostrophes, quotes, and newlines round-trip cleanly."""
    path_json = json.dumps(vault_path)
    content_json = json.dumps(content)
    return (
        "(async () => {"
        f"  const p = {path_json};"
        f"  const c = {content_json};"
        "  const slash = p.lastIndexOf('/');"
        "  if (slash > 0) {"
        "    const segments = p.substring(0, slash).split('/');"
        "    let acc = '';"
        "    for (const seg of segments) {"
        "      if (!seg) continue;"
        "      acc = acc ? acc + '/' + seg : seg;"
        "      try { await app.vault.createFolder(acc); } catch(e) {}"
        "    }"
        "  }"
        "  const existing = app.vault.getAbstractFileByPath(p);"
        "  let action;"
        "  if (existing) { await app.vault.modify(existing, c); action = 'modified'; }"
        "  else { await app.vault.create(p, c); action = 'created'; }"
        "  return JSON.stringify({"
        "    ok: true,"
        f"    vault_path: {path_json},"
        "    bytes_written: (new TextEncoder()).encode(c).length,"
        "    action: action"
        "  });"
        "})()"
    )


def _build_batch_js(items: List[Tuple[str, str]]) -> str:
    """Return JS that loops the items array, runs the same create-or-modify
    logic per entry, and returns ``{ok, results: [...]}`` covering
    every item — successes AND failures."""
    pairs_json = json.dumps([
        {"path": path, "content": content} for path, content in items
    ])
    return (
        "(async () => {"
        f"  const items = {pairs_json};"
        "  const results = [];"
        "  for (const item of items) {"
        "    const p = item.path;"
        "    const c = item.content;"
        "    try {"
        "      const slash = p.lastIndexOf('/');"
        "      if (slash > 0) {"
        "        const segments = p.substring(0, slash).split('/');"
        "        let acc = '';"
        "        for (const seg of segments) {"
        "          if (!seg) continue;"
        "          acc = acc ? acc + '/' + seg : seg;"
        "          try { await app.vault.createFolder(acc); } catch(e) {}"
        "        }"
        "      }"
        "      const existing = app.vault.getAbstractFileByPath(p);"
        "      let action;"
        "      if (existing) { await app.vault.modify(existing, c); action = 'modified'; }"
        "      else { await app.vault.create(p, c); action = 'created'; }"
        "      results.push({"
        "        ok: true,"
        "        vault_path: p,"
        "        bytes_written: (new TextEncoder()).encode(c).length,"
        "        action: action"
        "      });"
        "    } catch (err) {"
        "      results.push({"
        "        ok: false,"
        "        vault_path: p,"
        "        bytes_written: 0,"
        "        error: String(err && err.message ? err.message : err)"
        "      });"
        "    }"
        "  }"
        "  return JSON.stringify({ok: true, results: results});"
        "})()"
    )


def _build_delete_path_js(vault_path: str) -> str:
    """Best-effort recursive delete used by probe cleanup."""
    path_json = json.dumps(vault_path)
    return (
        "(async () => {"
        f"  const p = {path_json};"
        "  const f = app.vault.getAbstractFileByPath(p);"
        "  if (f) { await app.vault.delete(f, true); return JSON.stringify({ok: true, deleted: p}); }"
        "  return JSON.stringify({ok: true, deleted: null});"
        "})()"
    )


# ---------------------------------------------------------------------------
# write_note — single-note entry point
# ---------------------------------------------------------------------------

def rewrite_in_place(
    vault_name: str,
    existing_vault_path: str,
    content: str,
    runner: Optional[Callable] = None,
) -> Dict:
    """Rewrite an existing note in place, preserving its curated filename.

    v16.3.0 (SSS-F). Thin wrapper around :func:`write_note` that exists
    to make the intent explicit: the caller has an existing vault path
    (typically from :func:`vault_paths.lookup_existing`) and wants to
    overwrite the note WITHOUT renaming it. Pre-v16.3 every operator
    reinvented this: derive a fresh filename from the source basename,
    write that, and accidentally orphan the existing translated/curated
    note.

    Use ``write_note`` for new notes; use ``rewrite_in_place`` when the
    existing path is sourced from the scan index or an audit walk.
    Behaviour is identical (create-or-modify via ``obsidian eval``);
    the function name is the contract.
    """
    return write_note(vault_name, existing_vault_path, content, runner=runner)


def write_note(
    vault_name: str,
    vault_path: str,
    content: str,
    runner: Optional[Callable] = None,
) -> Dict:
    """Write ``content`` to ``vault_path`` in ``vault_name`` via
    ``obsidian eval`` + ``app.vault.create``/``app.vault.modify``.

    Returns ``{ok, vault_path, bytes_written, error}``. On failure,
    ``ok=False`` and ``error`` carries the diagnostic. There is no
    filesystem fallback.
    """
    vault_name = _validate_vault_name(vault_name)
    vault_path = _validate_vault_path(vault_path)
    if content is None:
        content = ""

    if runner is None:
        runner = _default_runner

    js = _build_write_note_js(vault_path, content)
    cmd = ["obsidian", "eval", f"vault={vault_name}", f"code={js}"]

    try:
        result = runner(cmd)
    except Exception as exc:
        return {
            "ok": False,
            "vault_path": vault_path,
            "bytes_written": 0,
            "error": f"runner raised: {exc}",
        }

    if getattr(result, "returncode", 0) != 0:
        err = (getattr(result, "stderr", "") or "").strip()
        if not err:
            err = f"obsidian eval exited {result.returncode}"
        return {
            "ok": False,
            "vault_path": vault_path,
            "bytes_written": 0,
            "error": err,
        }

    parsed = _parse_eval_result(getattr(result, "stdout", "") or "")
    if parsed is None:
        # Non-JSON success stdout — treat as soft success but flag
        # bytes_written=0 so callers that check size catch the drift.
        return {
            "ok": True,
            "vault_path": vault_path,
            "bytes_written": 0,
            "error": None,
        }

    return {
        "ok": bool(parsed.get("ok", True)),
        "vault_path": parsed.get("vault_path", vault_path),
        "bytes_written": int(parsed.get("bytes_written", 0)),
        "error": parsed.get("error"),
    }


# ---------------------------------------------------------------------------
# write_notes_batch — many notes per subprocess
# ---------------------------------------------------------------------------

def write_notes_batch(
    vault_name: str,
    items: Iterable[Tuple[str, str]],
    runner: Optional[Callable] = None,
    chunk_size: int = DEFAULT_CHUNK_SIZE,
) -> Dict:
    """Write a list of ``(vault_path, content)`` tuples in chunked
    ``obsidian eval`` invocations. With the default ``chunk_size=50``
    a 500-note project completes in 10 subprocess calls instead of 500.

    Returns ``{ok, written, failed, results: [{ok, vault_path,
    bytes_written, error}, ...]}``. The per-item results preserve the
    input order so callers can correlate by index.

    Refuses the whole batch up front if any item has an absolute path —
    the firewall must be loud, not silent.
    """
    vault_name = _validate_vault_name(vault_name)

    items_list = list(items)
    for path, _ in items_list:
        _validate_vault_path(path)

    if not items_list:
        return {"ok": True, "written": 0, "failed": 0, "results": []}

    if chunk_size is None or chunk_size <= 0:
        chunk_size = DEFAULT_CHUNK_SIZE

    if runner is None:
        runner = _default_runner

    aggregated: List[Dict] = []

    for start in range(0, len(items_list), chunk_size):
        chunk = items_list[start:start + chunk_size]
        js = _build_batch_js(chunk)
        cmd = ["obsidian", "eval", f"vault={vault_name}", f"code={js}"]

        try:
            result = runner(cmd)
        except Exception as exc:
            err = f"runner raised: {exc}"
            for path, _ in chunk:
                aggregated.append({
                    "ok": False, "vault_path": path,
                    "bytes_written": 0, "error": err,
                })
            continue

        if getattr(result, "returncode", 0) != 0:
            err = (getattr(result, "stderr", "") or "").strip()
            if not err:
                err = f"obsidian eval exited {result.returncode}"
            for path, _ in chunk:
                aggregated.append({
                    "ok": False, "vault_path": path,
                    "bytes_written": 0, "error": err,
                })
            continue

        parsed = _parse_eval_result(getattr(result, "stdout", "") or "")
        if parsed is None or not isinstance(parsed.get("results"), list):
            # The CLI returned a success exit but no parseable per-item
            # results. Treat the chunk as soft success, but flag
            # bytes_written=0 — same shape as the single-note fallback.
            for path, _ in chunk:
                aggregated.append({
                    "ok": True, "vault_path": path,
                    "bytes_written": 0, "error": None,
                })
            continue

        per_item = parsed["results"]
        # Pad/truncate so we always have one result per input item, even
        # if the JS dropped or duplicated an entry. Defensive coding —
        # the JS loop above never trims, but a future refactor might.
        for i, (path, _) in enumerate(chunk):
            entry = per_item[i] if i < len(per_item) else {}
            aggregated.append({
                "ok": bool(entry.get("ok", False)),
                "vault_path": entry.get("vault_path", path),
                "bytes_written": int(entry.get("bytes_written", 0)),
                "error": entry.get("error"),
            })

    written = sum(1 for r in aggregated if r["ok"])
    failed = len(aggregated) - written
    return {
        "ok": failed == 0,
        "written": written,
        "failed": failed,
        "results": aggregated,
    }


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def probe(
    vault_name: str,
    runner: Optional[Callable] = None,
) -> Dict:
    """Round-trip a 1-byte canary note into ``_vb-probe/`` and delete the
    namespace. Returns ``{ok, detail, error}``. Scan commands MUST call
    this at start and abort on failure — never fall back to anything
    else. The probe IS the only sanctioned health check for the write
    path."""
    if runner is None:
        runner = _default_runner

    probe_path = f"{_PROBE_FOLDER}/{_PROBE_FILENAME}"
    expected_bytes = len(_PROBE_CONTENT.encode("utf-8"))

    try:
        write_result = write_note(
            vault_name=vault_name,
            vault_path=probe_path,
            content=_PROBE_CONTENT,
            runner=runner,
        )
    finally:
        # Always attempt cleanup, even if the write failed — a stale
        # _vb-probe folder is worse than a slow probe.
        cleanup_js = _build_delete_path_js(_PROBE_FOLDER)
        try:
            runner(["obsidian", "eval", f"vault={vault_name}", f"code={cleanup_js}"])
        except Exception:
            pass

    if not write_result["ok"]:
        return {
            "ok": False,
            "detail": "probe write failed",
            "error": write_result.get("error"),
        }

    written = write_result.get("bytes_written", 0)
    if written and written != expected_bytes:
        return {
            "ok": False,
            "detail": (
                f"size mismatch: expected {expected_bytes} bytes, "
                f"got {written}"
            ),
            "error": None,
        }

    return {
        "ok": True,
        "detail": f"probe wrote and deleted {probe_path}",
        "error": None,
    }
