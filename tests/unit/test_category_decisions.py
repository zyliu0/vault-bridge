"""Tests for scripts/category_decisions.py — apply/plan category decisions.

Covers apply_decisions and plan_decisions_for_heartbeat.
All tests use tmp_path for isolation and mock memory_log.append.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch, call

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import local_config as lc  # noqa: E402
import category_decisions as cd  # noqa: E402
from discover_structure import DiscoveredFolder  # noqa: E402
from effective_config import EffectiveConfig  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _setup_workdir(tmp_path, active_domain="arch-projects", **kwargs) -> Path:
    """Create a properly configured workdir for tests."""
    # Default archive_root but allow override via kwargs
    kwargs.setdefault("archive_root", "/nas/projects/")
    lc.save_local_config(
        tmp_path,
        active_domain=active_domain,
        **kwargs,
    )
    return tmp_path


def _load_settings(workdir: Path) -> dict:
    return lc.load_local_config(workdir)


def _make_effective(
    routing_patterns=None,
    skip_patterns=None,
    fallback="Inbox",
) -> EffectiveConfig:
    return EffectiveConfig(
        vault_name="TestVault",
        domain_name="arch-projects",
        archive_root="/archive",
        file_system_type="local-path",
        routing_patterns=routing_patterns or [],
        skip_patterns=skip_patterns or [],
        fallback=fallback,
    )


def _make_discovered(name: str, tmp_path: Path, child_count: int = 5) -> DiscoveredFolder:
    return DiscoveredFolder(
        name=name,
        absolute_path=str(tmp_path / name),
        child_count=child_count,
        has_files_directly=True,
        has_subfolders=False,
    )


# ---------------------------------------------------------------------------
# apply_decisions — "add" action
# ---------------------------------------------------------------------------

def test_apply_add_appends_routing_pattern(tmp_path):
    """Action 'add' appends a new routing rule to project.json."""
    workdir = _setup_workdir(tmp_path, routing_patterns=[])

    decisions = [
        cd.CategoryDecision(
            subfolder_name="Interior",
            action="add",
            target="SD",
        )
    ]
    with patch("category_decisions.memory_log") as mock_ml:
        cd.apply_decisions(workdir, decisions)

    cfg = _load_settings(workdir)
    # The new rule should be present
    routing = cfg.get("routing_patterns", [])
    assert any(r.get("match") == "Interior" and r.get("subfolder") == "SD" for r in routing)


def test_apply_skip_appends_to_skip_patterns(tmp_path):
    """Action 'skip' appends the subfolder name to skip_patterns in project.json."""
    workdir = _setup_workdir(tmp_path, skip_patterns=[])

    decisions = [
        cd.CategoryDecision(
            subfolder_name="Renders",
            action="skip",
            target=None,
        )
    ]
    with patch("category_decisions.memory_log") as mock_ml:
        cd.apply_decisions(workdir, decisions)

    cfg = _load_settings(workdir)
    assert "Renders" in cfg.get("skip_patterns", [])


def test_apply_fallback_is_noop_for_project_json(tmp_path):
    """Action 'fallback' makes NO change to project.json settings."""
    workdir = _setup_workdir(tmp_path, routing_patterns=[], skip_patterns=[])
    original_cfg = _load_settings(workdir)

    decisions = [
        cd.CategoryDecision(
            subfolder_name="Photos",
            action="fallback",
            target=None,
        )
    ]
    with patch("category_decisions.memory_log") as mock_ml:
        cd.apply_decisions(workdir, decisions)

    after_cfg = _load_settings(workdir)
    # routing_patterns and skip_patterns must be unchanged
    assert after_cfg.get("routing_patterns", []) == original_cfg.get("routing_patterns", [])
    assert after_cfg.get("skip_patterns", []) == original_cfg.get("skip_patterns", [])


def test_apply_all_decisions_log_memory_entries(tmp_path):
    """Each decision produces exactly one memory-log entry."""
    workdir = _setup_workdir(tmp_path)

    decisions = [
        cd.CategoryDecision("Interior", "add", "SD"),
        cd.CategoryDecision("Renders", "skip", None),
        cd.CategoryDecision("Photos", "fallback", None),
    ]

    with patch("category_decisions.memory_log") as mock_ml:
        cd.apply_decisions(workdir, decisions)

    # One append call per decision
    assert mock_ml.append.call_count == 3


def test_apply_fallback_logs_fallback_used(tmp_path):
    """Action 'fallback' logs a 'fallback-used' memory event."""
    workdir = _setup_workdir(tmp_path)

    decisions = [
        cd.CategoryDecision("Photos", "fallback", None),
    ]

    with patch("category_decisions.memory_log") as mock_ml:
        cd.apply_decisions(workdir, decisions)

    call_args = mock_ml.append.call_args_list
    assert len(call_args) == 1
    # Second argument (entry) should have event_type == "fallback-used"
    entry = call_args[0][0][1]
    assert entry.event_type == "fallback-used"


def test_apply_add_logs_category_added(tmp_path):
    """Action 'add' logs a 'category-added' memory event."""
    workdir = _setup_workdir(tmp_path)

    decisions = [cd.CategoryDecision("Interior", "add", "SD")]

    with patch("category_decisions.memory_log") as mock_ml:
        cd.apply_decisions(workdir, decisions)

    entry = mock_ml.append.call_args_list[0][0][1]
    assert entry.event_type == "category-added"


def test_apply_skip_logs_category_skipped(tmp_path):
    """Action 'skip' logs a 'category-skipped' memory event."""
    workdir = _setup_workdir(tmp_path)

    decisions = [cd.CategoryDecision("Renders", "skip", None)]

    with patch("category_decisions.memory_log") as mock_ml:
        cd.apply_decisions(workdir, decisions)

    entry = mock_ml.append.call_args_list[0][0][1]
    assert entry.event_type == "category-skipped"


def test_apply_dedupes_repeat_adds(tmp_path):
    """Applying the same 'add' decision twice results in only one routing rule."""
    workdir = _setup_workdir(tmp_path, routing_patterns=[])

    decisions = [
        cd.CategoryDecision("Interior", "add", "SD"),
        cd.CategoryDecision("Interior", "add", "SD"),  # exact duplicate
    ]
    with patch("category_decisions.memory_log"):
        cd.apply_decisions(workdir, decisions)

    cfg = _load_settings(workdir)
    routing = cfg.get("routing_patterns", [])
    interior_rules = [r for r in routing if r.get("match") == "Interior" and r.get("subfolder") == "SD"]
    assert len(interior_rules) == 1


def test_apply_returns_stats(tmp_path):
    """apply_decisions returns a stats dict with counts of each action type."""
    workdir = _setup_workdir(tmp_path)

    decisions = [
        cd.CategoryDecision("Interior", "add", "SD"),
        cd.CategoryDecision("Renders", "skip", None),
        cd.CategoryDecision("Photos", "fallback", None),
    ]
    with patch("category_decisions.memory_log"):
        stats = cd.apply_decisions(workdir, decisions)

    assert isinstance(stats, dict)
    assert stats.get("added") == 1
    assert stats.get("added_to_skip_list") == 1
    assert stats.get("skipped_to_fallback") == 1


def test_apply_preserves_existing_project_fields(tmp_path):
    """Fields like archive_root, vault_name survive apply_decisions unchanged."""
    workdir = _setup_workdir(
        tmp_path,
        vault_name="MyVault",
        archive_root="/nas/arch/",
        file_system_type="nas-mcp",
    )

    decisions = [cd.CategoryDecision("Interior", "add", "SD")]
    with patch("category_decisions.memory_log"):
        cd.apply_decisions(workdir, decisions)

    cfg = _load_settings(workdir)
    assert cfg.get("vault_name") == "MyVault"
    assert cfg.get("archive_root") == "/nas/arch/"
    assert cfg.get("file_system_type") == "nas-mcp"


def test_apply_dedupes_skip_patterns(tmp_path):
    """Applying the same 'skip' decision twice results in only one skip pattern."""
    workdir = _setup_workdir(tmp_path, skip_patterns=[])

    decisions = [
        cd.CategoryDecision("Renders", "skip", None),
        cd.CategoryDecision("Renders", "skip", None),  # duplicate
    ]
    with patch("category_decisions.memory_log"):
        cd.apply_decisions(workdir, decisions)

    cfg = _load_settings(workdir)
    renders_count = cfg.get("skip_patterns", []).count("Renders")
    assert renders_count == 1


# ---------------------------------------------------------------------------
# plan_decisions_for_heartbeat
# ---------------------------------------------------------------------------

def test_plan_heartbeat_no_persistence(tmp_path):
    """plan_decisions_for_heartbeat returns decisions without touching project.json."""
    workdir = _setup_workdir(tmp_path, routing_patterns=[])
    original_cfg = _load_settings(workdir)

    effective = _make_effective()
    discovered = [
        _make_discovered("Interior", tmp_path),
        _make_discovered("Facade", tmp_path),
    ]

    # Should NOT write project.json
    result = cd.plan_decisions_for_heartbeat(discovered, effective)

    after_cfg = _load_settings(workdir)
    assert after_cfg == original_cfg  # unchanged
    assert isinstance(result, list)


def test_plan_heartbeat_classifies_unknowns_as_fallback(tmp_path):
    """All unmatched discovered folders get action='fallback' in heartbeat plan."""
    effective = _make_effective(
        routing_patterns=[{"match": "Admin", "subfolder": "Admin"}]
    )
    discovered = [
        _make_discovered("Interior", tmp_path),  # new
        _make_discovered("Facade", tmp_path),     # new
        _make_discovered("Admin", tmp_path),       # known — but still included in plan
    ]

    result = cd.plan_decisions_for_heartbeat(discovered, effective)

    # Interior and Facade are unknown → action="fallback"
    unknown_decisions = [d for d in result if d.subfolder_name in ("Interior", "Facade")]
    for decision in unknown_decisions:
        assert decision.action == "fallback"


def test_plan_heartbeat_only_includes_unknown_subfolders(tmp_path):
    """plan_decisions_for_heartbeat only returns decisions for UNKNOWN subfolders."""
    effective = _make_effective(
        routing_patterns=[{"match": "Admin", "subfolder": "Admin"}]
    )
    discovered = [
        _make_discovered("Interior", tmp_path),   # new
        _make_discovered("Admin", tmp_path),        # known
    ]

    result = cd.plan_decisions_for_heartbeat(discovered, effective)

    names = [d.subfolder_name for d in result]
    assert "Interior" in names
    assert "Admin" not in names


def test_plan_heartbeat_returns_empty_when_all_known(tmp_path):
    """plan_decisions_for_heartbeat returns [] when all discovered are already known."""
    effective = _make_effective(
        routing_patterns=[
            {"match": "SD", "subfolder": "SD"},
            {"match": "Admin", "subfolder": "Admin"},
        ]
    )
    discovered = [
        _make_discovered("SD", tmp_path),
        _make_discovered("Admin", tmp_path),
    ]

    result = cd.plan_decisions_for_heartbeat(discovered, effective)
    assert result == []


def test_plan_heartbeat_does_not_call_memory_log(tmp_path):
    """plan_decisions_for_heartbeat NEVER writes to memory log."""
    effective = _make_effective()
    discovered = [_make_discovered("Interior", tmp_path)]

    with patch("category_decisions.memory_log") as mock_ml:
        cd.plan_decisions_for_heartbeat(discovered, effective)

    mock_ml.append.assert_not_called()


# ---------------------------------------------------------------------------
# CLI: apply subcommand
# ---------------------------------------------------------------------------

def test_cli_apply_via_main(tmp_path, monkeypatch, capsys):
    """CLI 'apply' command writes routing rule and prints stats JSON."""
    import runpy

    workdir = _setup_workdir(tmp_path, routing_patterns=[], skip_patterns=[])
    decisions_json = json.dumps([
        {"subfolder_name": "Interior", "action": "add", "target": "SD"}
    ])
    monkeypatch.setattr(sys, "argv", [
        "category_decisions.py",
        "apply",
        "--workdir", str(workdir),
        "--decisions-json", decisions_json,
    ])
    with patch("category_decisions.memory_log"):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(SCRIPTS / "category_decisions.py"), run_name="__main__")
    assert exc.value.code == 0

    cfg = _load_settings(workdir)
    routing = cfg.get("routing_patterns", [])
    assert any(r.get("match") == "Interior" for r in routing)


def test_cli_apply_invalid_json(tmp_path, monkeypatch, capsys):
    """CLI 'apply' with malformed JSON exits 2 and prints an error."""
    import runpy

    workdir = _setup_workdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [
        "category_decisions.py",
        "apply",
        "--workdir", str(workdir),
        "--decisions-json", "{ not valid json",
    ])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "category_decisions.py"), run_name="__main__")
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "invalid" in captured.err.lower() or "json" in captured.err.lower()


def test_cli_no_subcommand_prints_help(tmp_path, monkeypatch, capsys):
    """CLI with no subcommand returns 2 (help)."""
    import runpy

    monkeypatch.setattr(sys, "argv", ["category_decisions.py"])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "category_decisions.py"), run_name="__main__")
    assert exc.value.code == 2


def test_cli_apply_skip_action(tmp_path, monkeypatch, capsys):
    """CLI 'apply' with skip action appends to skip_patterns."""
    import runpy

    workdir = _setup_workdir(tmp_path, skip_patterns=[])
    decisions_json = json.dumps([
        {"subfolder_name": "Renders", "action": "skip", "target": None}
    ])
    monkeypatch.setattr(sys, "argv", [
        "category_decisions.py",
        "apply",
        "--workdir", str(workdir),
        "--decisions-json", decisions_json,
    ])
    with patch("category_decisions.memory_log"):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(SCRIPTS / "category_decisions.py"), run_name="__main__")
    assert exc.value.code == 0

    cfg = _load_settings(workdir)
    assert "Renders" in cfg.get("skip_patterns", [])


def test_cli_apply_fallback_action_no_change(tmp_path, monkeypatch):
    """CLI 'apply' with fallback action makes no changes to project.json."""
    import runpy

    workdir = _setup_workdir(tmp_path, routing_patterns=[], skip_patterns=[])
    original_cfg = _load_settings(workdir)
    decisions_json = json.dumps([
        {"subfolder_name": "Photos", "action": "fallback", "target": None}
    ])
    monkeypatch.setattr(sys, "argv", [
        "category_decisions.py",
        "apply",
        "--workdir", str(workdir),
        "--decisions-json", decisions_json,
    ])
    with patch("category_decisions.memory_log"):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(SCRIPTS / "category_decisions.py"), run_name="__main__")
    assert exc.value.code == 0

    after_cfg = _load_settings(workdir)
    assert after_cfg.get("routing_patterns", []) == original_cfg.get("routing_patterns", [])
    assert after_cfg.get("skip_patterns", []) == original_cfg.get("skip_patterns", [])


def test_cli_plan_heartbeat_bad_config(tmp_path, monkeypatch, capsys):
    """CLI 'plan-heartbeat' exits 2 when effective config cannot be loaded."""
    import runpy

    # No settings.json in workdir → load_effective_config will raise SetupNeeded
    monkeypatch.setattr(sys, "argv", [
        "category_decisions.py",
        "plan-heartbeat",
        "--workdir", str(tmp_path),
    ])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(SCRIPTS / "category_decisions.py"), run_name="__main__")
    assert exc.value.code == 2
    captured = capsys.readouterr()
    assert "cannot load effective config" in captured.err.lower() or "config" in captured.err.lower()


def test_cli_plan_heartbeat_success(tmp_path, monkeypatch, capsys):
    """CLI 'plan-heartbeat' outputs stats JSON when config loads successfully."""
    import runpy
    from unittest.mock import MagicMock

    workdir = _setup_workdir(tmp_path)
    # Create a real subfolder in a fake archive root
    archive_root = tmp_path / "archive"
    archive_root.mkdir()
    (archive_root / "Interior").mkdir()
    for i in range(4):
        (archive_root / "Interior" / f"file{i}.pdf").touch()

    # Mock effective_config.load_effective_config to return a controlled EffectiveConfig
    mock_effective = _make_effective(routing_patterns=[], skip_patterns=[])
    # Override archive_root to the temp dir
    mock_effective = EffectiveConfig(
        vault_name="TestVault",
        domain_name="arch-projects",
        archive_root=str(archive_root),
        file_system_type="local-path",
        routing_patterns=[],
        skip_patterns=[],
        fallback="Inbox",
    )

    monkeypatch.setattr(sys, "argv", [
        "category_decisions.py",
        "plan-heartbeat",
        "--workdir", str(workdir),
    ])

    with patch("effective_config.load_effective_config", return_value=mock_effective):
        with pytest.raises(SystemExit) as exc:
            runpy.run_path(str(SCRIPTS / "category_decisions.py"), run_name="__main__")

    assert exc.value.code == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "unknown_subfolders" in data
    assert data["unknown_subfolders"] == 1
    assert "Interior" in data["subfolder_names"]
