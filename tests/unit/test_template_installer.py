"""Regression tests for scripts/template_installer.py."""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

_SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

import template_installer  # noqa: E402


def test_write_to_vault_fallback_resolves_vault_name_via_effective_config():
    """When vault_name=None, the fallback must use the zero-arg shim.

    v14.7.2 regression: the fallback called ``config.load_config()``
    which requires a ``workdir`` argument, raising TypeError. The fix
    routes through ``effective_config.load_config()`` (zero-arg shim
    that returns a dict).
    """
    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    with patch.object(template_installer, "subprocess") as mock_sub, patch.dict(
        sys.modules, {}, clear=False
    ):
        mock_sub.run.side_effect = fake_run
        fake_ec = type(sys)("effective_config")
        fake_ec.load_config = lambda: {"vault_name": "Test Vault"}
        sys.modules["effective_config"] = fake_ec
        try:
            template_installer._write_to_vault(None, "_Templates/vault-bridge/x.md", "body")
        finally:
            sys.modules.pop("effective_config", None)

    assert len(calls) == 1
    cmd = calls[0]
    assert cmd[0] == "obsidian"
    assert cmd[1] == "create"
    assert "vault=Test Vault" in cmd


def test_write_to_vault_uses_explicit_vault_name_without_loading_config():
    """Happy path: an explicit vault_name skips the load_config fallback."""
    calls: list[list[str]] = []

    def fake_run(cmd, check, capture_output, text):
        calls.append(cmd)

        class _R:
            returncode = 0
            stdout = ""
            stderr = ""

        return _R()

    with patch.object(template_installer, "subprocess") as mock_sub:
        mock_sub.run.side_effect = fake_run
        sys.modules.pop("effective_config", None)
        template_installer._write_to_vault("Explicit Vault", "_Templates/vault-bridge/y.md", "b")

    assert len(calls) == 1
    assert "vault=Explicit Vault" in calls[0]
