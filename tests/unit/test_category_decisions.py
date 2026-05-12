"""Tests for scripts/category_decisions.py — applies retro-scan Step 4.5
operator decisions to the workdir's config.json.

The script is invoked from the retro-scan command spec; previously it
did not exist and the spec referenced a non-existent module. v16.13.0
(JAE Doc Amendment 3) closes that gap.

Python 3.9 compatible.
"""
import json
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import category_decisions  # noqa: E402
import config as _config  # noqa: E402


def _seed_config(workdir: Path, domain_name: str = "arch-projects") -> None:
    """Write a minimal valid config.json so load_config()/save_config() round-trip."""
    cfg = {
        "schema_version": _config.SCHEMA_VERSION,
        "vault_name": "TestVault",
        "vault_path": None,
        "created_at": None,
        "fabrication_stopwords": [],
        "global_style": {},
        "active_domain": domain_name,
        "domains": [
            {
                "name": domain_name,
                "label": "Architecture Projects",
                "template_seed": "architecture",
                "archive_root": "/tmp/archive",
                "transport": "local",
                "default_tags": ["architecture"],
                "fallback": "Admin",
                "style": {},
                "routing_patterns": [
                    {"match": "schematic", "subfolder": "SD"},
                ],
                "content_overrides": [],
                "skip_patterns": ["*.tmp"],
                "calendar_sync": False,
            },
        ],
        "project_overrides": {},
        "discovered_structure": {"last_walked_at": None, "observed_subfolders": []},
        "file_type_config": {},
    }
    cfg_dir = workdir / ".vault-bridge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "config.json").write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def _load_domain(workdir: Path, name: str):
    cfg = _config.load_config(workdir)
    for d in cfg.domains:
        if d.name == name:
            return d
    raise AssertionError(f"domain {name} not found")


# ---------------------------------------------------------------------------
# apply() — happy path
# ---------------------------------------------------------------------------

class TestApply:
    def test_add_decision_persists_routing_pattern(self, tmp_path):
        _seed_config(tmp_path)
        stats = category_decisions.apply(
            tmp_path,
            "arch-projects",
            [{"subfolder_name": "Interior", "action": "add", "target": "SD"}],
        )
        assert stats.added == 1
        assert stats.errors == []

        domain = _load_domain(tmp_path, "arch-projects")
        matches = [r for r in domain.routing_patterns
                   if r.get("match") == "Interior" and r.get("subfolder") == "SD"]
        assert len(matches) == 1

    def test_skip_decision_persists_skip_pattern(self, tmp_path):
        _seed_config(tmp_path)
        stats = category_decisions.apply(
            tmp_path,
            "arch-projects",
            [{"subfolder_name": "Renders", "action": "skip", "target": None}],
        )
        assert stats.added_to_skip_list == 1
        domain = _load_domain(tmp_path, "arch-projects")
        assert "Renders" in domain.skip_patterns

    def test_fallback_decision_does_not_touch_config(self, tmp_path):
        _seed_config(tmp_path)
        config_path = tmp_path / ".vault-bridge" / "config.json"
        before = config_path.read_text(encoding="utf-8")

        stats = category_decisions.apply(
            tmp_path,
            "arch-projects",
            [{"subfolder_name": "Photos", "action": "fallback", "target": None}],
        )
        assert stats.skipped_to_fallback == 1
        # No writes — the routing layer falls back at scan time, no
        # persistent change needed.
        assert config_path.read_text(encoding="utf-8") == before

    def test_mixed_batch_applied_in_one_save(self, tmp_path):
        _seed_config(tmp_path)
        stats = category_decisions.apply(
            tmp_path,
            "arch-projects",
            [
                {"subfolder_name": "Interior", "action": "add", "target": "SD"},
                {"subfolder_name": "Renders", "action": "skip", "target": None},
                {"subfolder_name": "Photos", "action": "fallback", "target": None},
            ],
        )
        assert stats.added == 1
        assert stats.added_to_skip_list == 1
        assert stats.skipped_to_fallback == 1
        assert stats.errors == []

        domain = _load_domain(tmp_path, "arch-projects")
        assert any(r.get("match") == "Interior" for r in domain.routing_patterns)
        assert "Renders" in domain.skip_patterns

    def test_duplicate_add_is_idempotent(self, tmp_path):
        _seed_config(tmp_path)
        # Pre-existing rule with match="schematic" target="SD"; re-adding
        # the same pair must not double-write.
        decisions = [{"subfolder_name": "schematic", "action": "add", "target": "SD"}]
        category_decisions.apply(tmp_path, "arch-projects", decisions)
        category_decisions.apply(tmp_path, "arch-projects", decisions)

        domain = _load_domain(tmp_path, "arch-projects")
        matches = [r for r in domain.routing_patterns
                   if r.get("match") == "schematic" and r.get("subfolder") == "SD"]
        assert len(matches) == 1

    def test_duplicate_skip_is_idempotent(self, tmp_path):
        _seed_config(tmp_path)
        decision = {"subfolder_name": "Renders", "action": "skip", "target": None}
        category_decisions.apply(tmp_path, "arch-projects", [decision])
        category_decisions.apply(tmp_path, "arch-projects", [decision])

        domain = _load_domain(tmp_path, "arch-projects")
        assert domain.skip_patterns.count("Renders") == 1


# ---------------------------------------------------------------------------
# apply() — error paths
# ---------------------------------------------------------------------------

class TestApplyErrors:
    def test_invalid_action_reports_error_but_does_not_raise(self, tmp_path):
        _seed_config(tmp_path)
        stats = category_decisions.apply(
            tmp_path,
            "arch-projects",
            [{"subfolder_name": "Foo", "action": "wat", "target": None}],
        )
        assert stats.added == 0
        assert any("action" in e for e in stats.errors)

    def test_add_without_target_reports_error(self, tmp_path):
        _seed_config(tmp_path)
        stats = category_decisions.apply(
            tmp_path,
            "arch-projects",
            [{"subfolder_name": "Foo", "action": "add", "target": ""}],
        )
        assert stats.added == 0
        assert any("target" in e for e in stats.errors)

    def test_missing_subfolder_name_reports_error(self, tmp_path):
        _seed_config(tmp_path)
        stats = category_decisions.apply(
            tmp_path,
            "arch-projects",
            [{"action": "add", "target": "SD"}],
        )
        assert stats.added == 0
        assert any("subfolder_name" in e for e in stats.errors)

    def test_unknown_domain_reports_error(self, tmp_path):
        _seed_config(tmp_path)
        stats = category_decisions.apply(
            tmp_path,
            "nonexistent-domain",
            [{"subfolder_name": "Foo", "action": "add", "target": "SD"}],
        )
        assert stats.added == 0
        assert any("nonexistent-domain" in e for e in stats.errors)

    def test_domain_falls_back_to_active_domain_when_none_passed(self, tmp_path):
        _seed_config(tmp_path, domain_name="arch-projects")
        stats = category_decisions.apply(
            tmp_path,
            None,
            [{"subfolder_name": "Interior", "action": "add", "target": "SD"}],
        )
        assert stats.added == 1
        assert stats.errors == []

    def test_partial_failure_still_applies_valid_decisions(self, tmp_path):
        _seed_config(tmp_path)
        stats = category_decisions.apply(
            tmp_path,
            "arch-projects",
            [
                {"subfolder_name": "Interior", "action": "add", "target": "SD"},
                {"subfolder_name": "Bad", "action": "wat", "target": None},
            ],
        )
        assert stats.added == 1
        assert len(stats.errors) == 1

        domain = _load_domain(tmp_path, "arch-projects")
        assert any(r.get("match") == "Interior" for r in domain.routing_patterns)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

class TestCli:
    def test_cli_apply_writes_stats_to_stdout(self, tmp_path, capsys):
        _seed_config(tmp_path)
        decisions = json.dumps([
            {"subfolder_name": "Interior", "action": "add", "target": "SD"},
        ])
        rc = category_decisions._main([
            "apply",
            "--workdir", str(tmp_path),
            "--domain", "arch-projects",
            "--decisions-json", decisions,
        ])
        assert rc == 0
        captured = capsys.readouterr()
        out = json.loads(captured.out)
        assert out["added"] == 1
        assert out["errors"] == []

    def test_cli_invalid_json_returns_2(self, tmp_path, capsys):
        _seed_config(tmp_path)
        rc = category_decisions._main([
            "apply",
            "--workdir", str(tmp_path),
            "--decisions-json", "{not valid",
        ])
        assert rc == 2

    def test_cli_decisions_file_arg(self, tmp_path):
        _seed_config(tmp_path)
        decisions_file = tmp_path / "decisions.json"
        decisions_file.write_text(json.dumps([
            {"subfolder_name": "Renders", "action": "skip", "target": None},
        ]), encoding="utf-8")
        rc = category_decisions._main([
            "apply",
            "--workdir", str(tmp_path),
            "--decisions-file", str(decisions_file),
        ])
        assert rc == 0

    def test_cli_exits_nonzero_on_errors_in_stats(self, tmp_path, capsys):
        _seed_config(tmp_path)
        decisions = json.dumps([
            {"subfolder_name": "Foo", "action": "wat", "target": None},
        ])
        rc = category_decisions._main([
            "apply",
            "--workdir", str(tmp_path),
            "--decisions-json", decisions,
        ])
        assert rc == 1
