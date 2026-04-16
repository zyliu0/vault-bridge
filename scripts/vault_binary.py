"""Write binary files to an Obsidian vault via 'obsidian eval' + app.vault.createBinary.

This module provides:
  write_binary() — write a local file to a vault path as binary
  probe_binary_write() — round-trip test: write probe PNG, verify, delete

The JS snippet uses fs.readFileSync to read source bytes directly — this
avoids ARG_MAX limits that would hit with base64-in-argv for large files.

All subprocess calls use the 'runner' parameter (injectable for tests).
Default runner: subprocess.run(..., capture_output=True, text=True).

Python 3.9 compatible.
"""
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path
from typing import Callable, Dict, Optional


# ---------------------------------------------------------------------------
# Probe constants — a hardcoded 1×1 PNG (67 bytes) for capability testing
# ---------------------------------------------------------------------------

# Minimal valid 1×1 red PNG
PROBE_PNG_BYTES: bytes = (
    b"\x89PNG\r\n\x1a\n"          # PNG signature (8 bytes)
    b"\x00\x00\x00\rIHDR"         # IHDR chunk length + type
    b"\x00\x00\x00\x01"           # width: 1
    b"\x00\x00\x00\x01"           # height: 1
    b"\x08\x02"                   # bit depth: 8, color type: RGB
    b"\x00\x00\x00"               # compression, filter, interlace
    b"\x90wS\xde"                 # CRC
    b"\x00\x00\x00\x0cIDATx\x9c" # IDAT chunk
    b"b\xf8\x0f\x00\x00\x01\x01" # deflate: red pixel
    b"\x00\x05\x18\xd8N"         # remaining IDAT
    b"\x00\x00\x00\x00IEND"       # IEND
    b"\xaeB`\x82"                 # IEND CRC
)


def _default_runner(cmd):
    return subprocess.run(cmd, capture_output=True, text=True)


def write_binary(
    vault_name: str,
    src_abs_path: Path,
    vault_dst_path: str,
    runner: Optional[Callable] = None,
) -> Dict:
    """Write src bytes to vault_dst_path via obsidian eval + app.vault.createBinary.

    Args:
        vault_name: The Obsidian vault name.
        src_abs_path: Absolute path to the source file on the local filesystem.
        vault_dst_path: Destination path within the vault (e.g. "Project/_Attachments/img.jpg").
        runner: Optional callable(cmd) → result with .returncode/.stdout/.stderr.
                Defaults to subprocess.run(..., capture_output=True, text=True).

    Returns:
        {
            "ok": bool,
            "vault_path": str,
            "bytes_written": int,
            "sha256": str,
            "error": Optional[str],
        }
    """
    if runner is None:
        runner = _default_runner

    src_abs_path = Path(src_abs_path)

    if not src_abs_path.exists():
        return {
            "ok": False,
            "vault_path": vault_dst_path,
            "bytes_written": 0,
            "sha256": "",
            "error": f"source not found: {src_abs_path}",
        }

    # Use json.dumps to safely escape paths with CJK, spaces, apostrophes, quotes
    src_json = json.dumps(str(src_abs_path))
    dst_json = json.dumps(vault_dst_path)

    # JS snippet: delete-then-create pattern (createBinary doesn't overwrite)
    js_snippet = (
        "(async () => {"
        "  const fs = require('fs');"
        f"  const srcPath = {src_json};"
        f"  const dstPath = {dst_json};"
        "  const bytes = fs.readFileSync(srcPath);"
        "  const dstDir = dstPath.substring(0, dstPath.lastIndexOf('/'));"
        "  if (dstDir) {"
        "    try { await app.vault.createFolder(dstDir); } catch(e) {}"
        "  }"
        "  try { const existing = app.vault.getAbstractFileByPath(dstPath);"
        "    if (existing) { await app.vault.delete(existing); }"
        "  } catch(e) {}"
        "  const af = await app.vault.createBinary(dstPath, bytes);"
        "  const sha = require('crypto').createHash('sha256').update(bytes).digest('hex');"
        f"  return JSON.stringify({{ok: true, bytes_written: bytes.length, sha256: sha, vault_path: {dst_json}}});"
        "})()"
    )

    cmd = ["obsidian", "eval", f"vault={vault_name}", f"code={js_snippet}"]

    try:
        result = runner(cmd)
    except Exception as exc:
        return {
            "ok": False,
            "vault_path": vault_dst_path,
            "bytes_written": 0,
            "sha256": "",
            "error": f"runner raised: {exc}",
        }

    if result.returncode != 0:
        err = result.stderr.strip() or f"obsidian eval exited {result.returncode}"
        return {
            "ok": False,
            "vault_path": vault_dst_path,
            "bytes_written": 0,
            "sha256": "",
            "error": err,
        }

    # obsidian eval prefixes its result with "=> " and may wrap strings in quotes.
    # Strip both so downstream JSON decode sees just the payload.
    stdout = result.stdout.strip()
    if stdout.startswith("=> "):
        stdout = stdout[3:].strip()
    if len(stdout) >= 2 and stdout[0] == stdout[-1] == '"':
        try:
            stdout = json.loads(stdout)
        except json.JSONDecodeError:
            pass

    try:
        data = json.loads(stdout) if isinstance(stdout, str) else stdout
        if not isinstance(data, dict):
            raise ValueError("not a dict")
        return {
            "ok": bool(data.get("ok", True)),
            "vault_path": data.get("vault_path", vault_dst_path),
            "bytes_written": int(data.get("bytes_written", 0)),
            "sha256": str(data.get("sha256", "")),
            "error": data.get("error"),
        }
    except (json.JSONDecodeError, ValueError, TypeError):
        # Non-JSON success stdout — treat as a soft success but flag unknown size.
        return {
            "ok": True,
            "vault_path": vault_dst_path,
            "bytes_written": 0,
            "sha256": "",
            "error": None,
        }


def _delete_vault_path(vault_name: str, vault_path: str, runner: Callable) -> None:
    """Best-effort delete of a vault file or folder. Errors are swallowed."""
    path_json = json.dumps(vault_path)
    js = (
        "(async () => {"
        f"  const p = {path_json};"
        "  const f = app.vault.getAbstractFileByPath(p);"
        "  if (f) { await app.vault.delete(f, true); return 'deleted'; }"
        "  return 'missing';"
        "})()"
    )
    try:
        runner(["obsidian", "eval", f"vault={vault_name}", f"code={js}"])
    except Exception:
        pass


def probe_binary_write(
    vault_name: str,
    runner: Optional[Callable] = None,
) -> Dict:
    """Write a hardcoded tiny PNG, verify byte count, then delete. Returns {ok, detail, error}."""
    if runner is None:
        runner = _default_runner

    probe_bytes = PROBE_PNG_BYTES
    probe_hash = hashlib.sha256(probe_bytes).hexdigest()[:8]
    probe_dst = f"_Attachments/_probe/{probe_hash}_probe.png"

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        tmp.write(probe_bytes)
        tmp_path = Path(tmp.name)

    try:
        result = write_binary(
            vault_name=vault_name,
            src_abs_path=tmp_path,
            vault_dst_path=probe_dst,
            runner=runner,
        )
    finally:
        tmp_path.unlink(missing_ok=True)

    try:
        if not result["ok"]:
            return {"ok": False, "detail": "write_binary failed", "error": result.get("error")}

        written = result.get("bytes_written", 0)
        if written != len(probe_bytes):
            return {
                "ok": False,
                "detail": f"size mismatch: expected {len(probe_bytes)}, got {written}",
                "error": None,
            }

        return {
            "ok": True,
            "detail": f"probe write succeeded: {probe_dst}",
            "error": None,
        }
    finally:
        # Clean up the probe folder from the vault so we don't leave debris.
        _delete_vault_path(vault_name, "_Attachments/_probe", runner)
