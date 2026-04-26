"""Tests for scripts/vault_writer.py — sanctioned text-write path to the
Obsidian vault via the `obsidian eval` CLI (Bug A, v16.2.0).

Companion to vault_binary.py (binary writes). Three public entry points:

    write_note(vault_name, vault_path, content, runner=None) -> dict
    write_notes_batch(vault_name, items, runner=None) -> dict
    probe(vault_name, runner=None) -> dict

All three MUST go through `obsidian eval` — there is no filesystem
fallback, no `vault_fs_root()` helper, no `Path.write_text` path.

Result contract (single + probe): {"ok": bool, "vault_path"|"detail",
"bytes_written", "error"}. Batch result: {"ok", "written", "failed",
"results": [...]}.

TDD — these tests are written before the implementation; running them
now MUST fail with ImportError on the missing module.

Python 3.9 compatible.
"""
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vault_writer  # noqa: E402


# ---------------------------------------------------------------------------
# Test runner helpers
# ---------------------------------------------------------------------------

def _make_success_runner(payload=None):
    """Runner that returns a success result. payload is JSON-encoded as stdout."""
    if payload is None:
        payload = {"ok": True, "vault_path": "x.md", "bytes_written": 0}

    def runner(cmd):
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = json.dumps(payload) if not isinstance(payload, str) else payload
        result.stderr = ""
        return result
    return runner


def _make_failing_runner(returncode=1, stderr="boom"):
    def runner(cmd):
        result = mock.MagicMock()
        result.returncode = returncode
        result.stdout = ""
        result.stderr = stderr
        return result
    return runner


def _make_capturing_runner(payload=None):
    """Runner that captures every command list it sees and returns success."""
    captured = []
    if payload is None:
        payload = {"ok": True, "vault_path": "x.md", "bytes_written": 0}

    def runner(cmd):
        captured.append(list(cmd))
        result = mock.MagicMock()
        result.returncode = 0
        result.stdout = json.dumps(payload) if not isinstance(payload, str) else payload
        result.stderr = ""
        return result
    return runner, captured


# ---------------------------------------------------------------------------
# write_note — single-note write contract
# ---------------------------------------------------------------------------

def test_write_note_uses_obsidian_eval():
    """write_note shells out to `obsidian eval`, never to a Write tool."""
    runner, captured = _make_capturing_runner(
        payload={"ok": True, "vault_path": "Foo/bar.md", "bytes_written": 5},
    )
    vault_writer.write_note("MyVault", "Foo/bar.md", "hello", runner=runner)

    assert len(captured) == 1
    cmd = captured[0]
    assert cmd[0] == "obsidian"
    assert cmd[1] == "eval"
    assert any(str(part) == "vault=MyVault" for part in cmd)
    assert any(str(part).startswith("code=") for part in cmd), (
        f"Expected `code=` arg in obsidian eval invocation; got {cmd}"
    )
    # Belt-and-suspenders: the deprecated `js=` form must not appear.
    assert not any(str(part).startswith("js=") for part in cmd)


def test_write_note_path_and_content_are_json_escaped():
    """CJK, spaces, apostrophes, newlines, quotes — all routed through
    json.dumps inside the JS snippet so no shell-injection or quoting
    bug can sneak in."""
    runner, captured = _make_capturing_runner()
    cjk_path = "项目 2024/O'Brien/测试 \"note\".md"
    content = "line1\nline2 with \"quotes\" and 'apostrophes'\n中文内容\n"
    vault_writer.write_note("MyVault", cjk_path, content, runner=runner)

    cmd_str = " ".join(str(c) for c in captured[0])
    assert json.dumps(cjk_path) in cmd_str
    assert json.dumps(content) in cmd_str


def test_write_note_success_payload():
    """Success returns {ok: True, vault_path, bytes_written, error: None}."""
    payload = {
        "ok": True,
        "vault_path": "Foo/bar.md",
        "bytes_written": 11,
    }
    result = vault_writer.write_note(
        "MyVault", "Foo/bar.md", "hello world",
        runner=_make_success_runner(payload),
    )
    assert result["ok"] is True
    assert result["vault_path"] == "Foo/bar.md"
    assert result["bytes_written"] == 11
    assert result.get("error") in (None, "")


def test_write_note_runner_nonzero_returns_error():
    """Runner returns non-zero exit → ok=False, error populated."""
    result = vault_writer.write_note(
        "MyVault", "Foo/bar.md", "x",
        runner=_make_failing_runner(returncode=2, stderr="vault offline"),
    )
    assert result["ok"] is False
    assert result["error"]


def test_write_note_runner_raises_returns_error():
    """Subprocess crash bubbles up as a structured error, not an exception."""
    def crashing(cmd):
        raise RuntimeError("subprocess crashed")

    result = vault_writer.write_note(
        "MyVault", "Foo/bar.md", "x", runner=crashing,
    )
    assert result["ok"] is False
    assert "subprocess crashed" in (result.get("error") or "")


def test_write_note_rejects_empty_path():
    """Empty path is a programming error — refuse loudly."""
    with pytest.raises(ValueError):
        vault_writer.write_note("MyVault", "", "x", runner=_make_success_runner())
    with pytest.raises(ValueError):
        vault_writer.write_note("MyVault", "   ", "x", runner=_make_success_runner())


def test_write_note_rejects_empty_vault_name():
    with pytest.raises(ValueError):
        vault_writer.write_note("", "Foo.md", "x", runner=_make_success_runner())


def test_write_note_rejects_absolute_path():
    """A `/`-anchored path or a host filesystem path is a sign the caller
    is reaching for a vault root — exactly what Bug A bans."""
    with pytest.raises(ValueError):
        vault_writer.write_note(
            "MyVault", "/Users/mac/Vault/Foo.md", "x",
            runner=_make_success_runner(),
        )


def test_write_note_creates_parent_folders_in_js():
    """Generated JS must call createFolder on every parent path before
    create/modify, so deep paths don't fail with `folder not found`."""
    runner, captured = _make_capturing_runner()
    vault_writer.write_note(
        "MyVault", "arch-projects/2501 LAGI2025/Submission/foo.md", "x",
        runner=runner,
    )
    cmd_str = " ".join(str(c) for c in captured[0])
    assert "createFolder" in cmd_str


def test_write_note_modifies_existing_file_in_js():
    """Generated JS must use vault.modify when the file exists (not just
    vault.create) — otherwise overwrites silently no-op the new content."""
    runner, captured = _make_capturing_runner()
    vault_writer.write_note("MyVault", "Foo/bar.md", "x", runner=runner)
    cmd_str = " ".join(str(c) for c in captured[0])
    # Both create AND modify branches must be present in the JS snippet.
    assert "app.vault.create" in cmd_str
    assert "app.vault.modify" in cmd_str


def test_write_note_returns_dict_when_stdout_is_garbage():
    """If obsidian eval returns non-JSON, treat as soft success but flag.
    Mirrors vault_binary's tolerance: don't crash if the CLI surface drifts."""
    runner = _make_success_runner(payload="not-json garbage")
    result = vault_writer.write_note(
        "MyVault", "Foo/bar.md", "x", runner=runner,
    )
    # Either ok=True with bytes_written=0 (soft success) or ok=False with
    # an explanatory error — both are acceptable, but the result must be
    # a dict with ok set.
    assert isinstance(result, dict)
    assert "ok" in result


# ---------------------------------------------------------------------------
# write_notes_batch — multi-note batched write
# ---------------------------------------------------------------------------

def test_write_notes_batch_uses_single_obsidian_eval_call():
    """The whole point of batching is one subprocess per N notes —
    not N subprocesses. With 50 entries we still expect 1 call."""
    runner, captured = _make_capturing_runner(
        payload={
            "ok": True,
            "results": [
                {"ok": True, "vault_path": f"Foo/{i}.md", "bytes_written": 1}
                for i in range(50)
            ],
        },
    )
    items = [(f"Foo/{i}.md", "x") for i in range(50)]
    vault_writer.write_notes_batch("MyVault", items, runner=runner)
    assert len(captured) == 1


def test_write_notes_batch_returns_per_item_results():
    """Caller can tell which entries succeeded and which failed."""
    payload = {
        "ok": True,
        "results": [
            {"ok": True, "vault_path": "Foo/a.md", "bytes_written": 1},
            {"ok": False, "vault_path": "Foo/b.md", "error": "createFolder failed"},
            {"ok": True, "vault_path": "Foo/c.md", "bytes_written": 1},
        ],
    }
    items = [("Foo/a.md", "1"), ("Foo/b.md", "2"), ("Foo/c.md", "3")]
    out = vault_writer.write_notes_batch(
        "MyVault", items, runner=_make_success_runner(payload),
    )
    assert out["written"] == 2
    assert out["failed"] == 1
    assert len(out["results"]) == 3
    assert out["results"][1]["ok"] is False
    assert "createFolder failed" in out["results"][1]["error"]


def test_write_notes_batch_empty_input_short_circuits():
    """No items → no subprocess invocation; return zero counts."""
    runner, captured = _make_capturing_runner()
    out = vault_writer.write_notes_batch("MyVault", [], runner=runner)
    assert out["written"] == 0
    assert out["failed"] == 0
    assert out["results"] == []
    assert captured == []  # no subprocess was spawned


def test_write_notes_batch_runner_failure_marks_all_failed():
    """When the subprocess itself fails, every item in the batch is
    reported as failed — the caller never sees a phantom success."""
    items = [("Foo/a.md", "1"), ("Foo/b.md", "2")]
    out = vault_writer.write_notes_batch(
        "MyVault", items,
        runner=_make_failing_runner(returncode=1, stderr="vault offline"),
    )
    assert out["written"] == 0
    assert out["failed"] == 2
    for r in out["results"]:
        assert r["ok"] is False
        assert r.get("error")


def test_write_notes_batch_chunk_size_caps_per_call():
    """Very large batches are chunked under the hood so a single
    `obsidian eval` invocation doesn't exceed ARG_MAX. Default chunk
    size is 50; 120 items → 3 subprocess calls."""
    captured = []

    def runner(cmd):
        captured.append(list(cmd))
        # Echo per-item success matching the input size.
        cmd_str = " ".join(str(c) for c in cmd)
        # Count "vault_path" occurrences in the JS payload as a proxy
        # for batch size — every entry sets one.
        # The simpler approach: derive size from the items count fed
        # to *this* call by parsing the JSON list embedded in the JS.
        # For this test we just return ok=True with a generic shape.
        result = mock.MagicMock()
        result.returncode = 0
        # Match whatever the writer asks for; it should be tolerant.
        result.stdout = json.dumps({"ok": True, "results": [
            {"ok": True, "vault_path": f"x{i}.md", "bytes_written": 1}
            for i in range(50)
        ]})
        result.stderr = ""
        return result

    items = [(f"Foo/{i}.md", "x") for i in range(120)]
    vault_writer.write_notes_batch("MyVault", items, runner=runner, chunk_size=50)
    # 120 items / 50 chunk = 3 calls (50 + 50 + 20).
    assert len(captured) == 3


def test_write_notes_batch_rejects_absolute_paths():
    """Same firewall as write_note: any item with an absolute path
    fails the whole batch up front."""
    items = [("Foo/a.md", "1"), ("/Users/mac/Vault/b.md", "2")]
    with pytest.raises(ValueError):
        vault_writer.write_notes_batch(
            "MyVault", items, runner=_make_success_runner(),
        )


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def test_probe_round_trips_note_then_deletes():
    """probe writes a 1-byte canary, verifies it, then deletes its
    parent folder. Returns {ok: True, detail: '...'} on success."""
    runner, captured = _make_capturing_runner(
        payload={"ok": True, "vault_path": "_vb-probe/x.md", "bytes_written": 1},
    )
    out = vault_writer.probe("MyVault", runner=runner)
    assert out["ok"] is True
    # Probe must have invoked the runner at least twice — once to write,
    # once to delete the namespaced probe folder.
    assert len(captured) >= 2
    # Final call should be the cleanup of the _vb-probe namespace.
    cleanup_cmd_str = " ".join(str(c) for c in captured[-1])
    assert "_vb-probe" in cleanup_cmd_str
    # Cleanup uses delete, not create.
    assert "app.vault.delete" in cleanup_cmd_str


def test_probe_failure_returns_structured_error():
    """When obsidian eval returns non-zero, probe reports {ok: False, error: ...}."""
    out = vault_writer.probe(
        "MyVault",
        runner=_make_failing_runner(returncode=1, stderr="not running"),
    )
    assert out["ok"] is False
    assert out.get("error")


def test_probe_size_mismatch_fails():
    """Probe wrote N bytes but vault reports a different count → fail."""
    payload = {"ok": True, "vault_path": "_vb-probe/x.md", "bytes_written": 999}
    out = vault_writer.probe("MyVault", runner=_make_success_runner(payload))
    assert out["ok"] is False
    assert "size" in (out.get("detail") or out.get("error") or "").lower()


# ---------------------------------------------------------------------------
# Module surface — ensure no FS-fallback helper exists
# ---------------------------------------------------------------------------

def test_module_does_not_export_filesystem_fallback():
    """Bug A explicitly forbids any helper that returns a vault filesystem
    root or that writes via `Path.write_text`. The module surface must
    not include such names — the absence is the forcing function."""
    forbidden = [
        "vault_fs_root", "vault_root_path", "fast_write",
        "write_file_direct", "_fs_write", "fs_write",
    ]
    for name in forbidden:
        assert not hasattr(vault_writer, name), (
            f"vault_writer.{name} exists — Bug A forbids any FS fallback. "
            f"Remove the helper; force callers through obsidian eval."
        )
