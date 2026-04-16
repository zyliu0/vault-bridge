"""Tests for scripts/vault_binary.py — write binary files to Obsidian vault.

TDD: tests written BEFORE the implementation.

Cases:
1. write_binary composes expected obsidian eval command with correct JS
2. Runner returns non-zero → {ok: False, error: ...}
3. Runner raises exception → {ok: False, error: ...}
4. Source missing → {ok: False, error: "source not found"}
5. Probe round-trip success (runner returns matching JSON-like stdout)
6. Probe size mismatch → {ok: False, detail: "size mismatch"}
7. Path with CJK + spaces escaped correctly (json.dumps in generated JS)
8. Path with apostrophes escaped correctly
"""
import json
import sys
import subprocess
from pathlib import Path
from unittest import mock
from typing import Optional

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_binary  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_success_runner(stdout: str = "", returncode: int = 0):
    """Return a runner that succeeds."""
    def runner(cmd):
        result = mock.MagicMock()
        result.returncode = returncode
        result.stdout = stdout
        result.stderr = ""
        return result
    return runner


def _make_failing_runner(returncode: int = 1, stderr: str = "some error"):
    """Return a runner that returns non-zero."""
    def runner(cmd):
        result = mock.MagicMock()
        result.returncode = returncode
        result.stdout = ""
        result.stderr = stderr
        return result
    return runner


def _make_raising_runner(exc: Exception):
    """Return a runner that raises an exception."""
    def runner(cmd):
        raise exc
    return runner


# ---------------------------------------------------------------------------
# write_binary — command composition
# ---------------------------------------------------------------------------

def test_write_binary_missing_source_returns_error(tmp_path):
    """Source file does not exist → {ok: False, error: 'source not found'}."""
    src = tmp_path / "nonexistent.jpg"
    result = vault_binary.write_binary(
        vault_name="MyVault",
        src_abs_path=src,
        vault_dst_path="_Attachments/test.jpg",
        runner=_make_success_runner(),
    )
    assert result["ok"] is False
    assert "source not found" in result["error"].lower()


def test_write_binary_runner_non_zero_returns_error(tmp_path):
    """Runner returns non-zero → {ok: False, error: ...}."""
    src = tmp_path / "real.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 60)

    result = vault_binary.write_binary(
        vault_name="MyVault",
        src_abs_path=src,
        vault_dst_path="_Attachments/real.jpg",
        runner=_make_failing_runner(returncode=1, stderr="obsidian error"),
    )
    assert result["ok"] is False
    assert result["error"]


def test_write_binary_runner_raises_returns_error(tmp_path):
    """Runner raises exception → {ok: False, error: ...}."""
    src = tmp_path / "real.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 60)

    result = vault_binary.write_binary(
        vault_name="MyVault",
        src_abs_path=src,
        vault_dst_path="_Attachments/real.jpg",
        runner=_make_raising_runner(RuntimeError("subprocess crashed")),
    )
    assert result["ok"] is False
    assert result["error"]


def test_write_binary_command_uses_obsidian_eval(tmp_path):
    """write_binary calls 'obsidian eval' command."""
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 60)

    captured = []

    def capturing_runner(cmd):
        captured.append(cmd)
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"ok": True, "bytes_written": 64, "sha256": "abc"})
        result.stderr = ""
        return result

    vault_binary.write_binary(
        vault_name="MyVault",
        src_abs_path=src,
        vault_dst_path="_Attachments/photo.jpg",
        runner=capturing_runner,
    )
    assert len(captured) >= 1
    cmd = captured[0]
    # Command should include 'obsidian' and 'eval'
    assert any("obsidian" in str(part) for part in cmd)
    assert any("eval" in str(part) for part in cmd)
    # Pin the exact CLI flag — `obsidian eval` requires `code=<javascript>`, not `js=`
    assert any(str(part).startswith("code=") for part in cmd), (
        f"Expected a `code=` argument in the obsidian eval invocation; got {cmd}"
    )
    assert not any(str(part).startswith("js=") for part in cmd), (
        "Found a `js=` argument — obsidian CLI uses `code=`, not `js=`"
    )


def test_write_binary_uses_json_dumps_for_path_escaping_cjk(tmp_path):
    """CJK + spaces in paths are JSON-escaped in the generated JS."""
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 60)

    captured_js = []

    def capturing_runner(cmd):
        # The JS snippet is passed as part of the command
        full_cmd_str = " ".join(str(c) for c in cmd)
        captured_js.append(full_cmd_str)
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"ok": True, "bytes_written": 64, "sha256": "abc"})
        result.stderr = ""
        return result

    cjk_dst = "项目 2024/测试文件.jpg"
    vault_binary.write_binary(
        vault_name="MyVault",
        src_abs_path=src,
        vault_dst_path=cjk_dst,
        runner=capturing_runner,
    )
    assert captured_js, "Runner was not called"
    # The destination path should be JSON-encoded (json.dumps handles CJK/spaces)
    escaped = json.dumps(cjk_dst)
    assert escaped in captured_js[0], (
        f"Expected JSON-escaped path {escaped!r} in JS snippet, got: {captured_js[0][:200]}"
    )


def test_write_binary_uses_json_dumps_for_path_with_apostrophe(tmp_path):
    """Apostrophes in paths are JSON-escaped in the generated JS."""
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 60)

    captured_js = []

    def capturing_runner(cmd):
        full_cmd_str = " ".join(str(c) for c in cmd)
        captured_js.append(full_cmd_str)
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({"ok": True, "bytes_written": 64, "sha256": "abc"})
        result.stderr = ""
        return result

    apos_dst = "O'Brien Archive/file.jpg"
    vault_binary.write_binary(
        vault_name="MyVault",
        src_abs_path=src,
        vault_dst_path=apos_dst,
        runner=capturing_runner,
    )
    escaped = json.dumps(apos_dst)
    assert captured_js
    assert escaped in captured_js[0], (
        f"Expected JSON-escaped path {escaped!r} in JS snippet"
    )


def test_write_binary_success_returns_ok_dict(tmp_path):
    """Successful write returns {ok: True, vault_path: ..., bytes_written: ..., sha256: ...}."""
    src = tmp_path / "photo.jpg"
    src.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 60)

    result = vault_binary.write_binary(
        vault_name="MyVault",
        src_abs_path=src,
        vault_dst_path="_Attachments/photo.jpg",
        runner=_make_success_runner(
            stdout=json.dumps({"ok": True, "bytes_written": 64, "sha256": "deadbeef"})
        ),
    )
    assert result["ok"] is True
    assert result["vault_path"] == "_Attachments/photo.jpg"
    assert isinstance(result["bytes_written"], int)
    assert isinstance(result["sha256"], str)


# ---------------------------------------------------------------------------
# probe_binary_write
# ---------------------------------------------------------------------------

def test_probe_binary_write_success(tmp_path):
    """Probe returns {ok: True} when runner succeeds."""
    # Probe uses a hardcoded 1x1 PNG. The runner just needs to succeed.
    probe_bytes = vault_binary.PROBE_PNG_BYTES
    expected_size = len(probe_bytes)

    def probe_runner(cmd):
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = json.dumps({
            "ok": True,
            "bytes_written": expected_size,
            "sha256": "abc123",
        })
        result.stderr = ""
        return result

    out = vault_binary.probe_binary_write("MyVault", runner=probe_runner)
    assert out["ok"] is True


def test_probe_binary_write_runner_error():
    """Probe runner returns non-zero → {ok: False, error: ...}."""
    out = vault_binary.probe_binary_write(
        "MyVault",
        runner=_make_failing_runner(returncode=1, stderr="write error"),
    )
    assert out["ok"] is False
    assert out.get("error")
