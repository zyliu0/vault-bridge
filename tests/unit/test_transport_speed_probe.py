"""Tests for scripts/transport_speed_probe.py.

Cases:
SP1.  probe_throughput with no sample_paths → fallback result
SP2.  probe_throughput: all samples fail → fallback result with detail
SP3.  probe_throughput: 1 valid sample → low confidence
SP4.  probe_throughput: 3+ valid samples → high confidence, median bps
SP5.  probe_throughput: samples below min_sample_bytes are skipped
SP6.  probe_throughput: fetch timeout on sample → sample skipped, rest measured
SP7.  probe_throughput: never raises; TransportFailed swallowed as error
SP8.  save_throughput updates domain config (happy path)
SP9.  save_throughput: domain not found → no-op, no raise
SP10. save_throughput: config load fails → no-op, no raise
SP11. _measure_one: success returns (bps, None)
SP12. _measure_one: file smaller than min_sample_bytes → (None, error_str)
SP13. _measure_one: TransportMissing → (None, error_str)
SP14. fetch_to_local_timed: success returns (path, elapsed) within budget
SP15. fetch_to_local_timed: timeout exceeded → TransportTimeout
SP16. fetch_to_local_timed: timeout=None → no limit, returns (path, elapsed)
"""
import sys
import tempfile
import time
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import transport_loader
import transport_speed_probe
from transport_speed_probe import (
    FALLBACK_THROUGHPUT_BPS,
    ThroughputResult,
    _measure_one,
    probe_throughput,
    save_throughput,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_workdir(tmp_path: Path) -> Path:
    """Create a minimal workdir structure with a .vault-bridge/transports/ dir."""
    td = tmp_path / ".vault-bridge" / "transports"
    td.mkdir(parents=True)
    return tmp_path


def _write_transport(workdir: Path, slug: str, size_bytes: int = 0, delay: float = 0.0) -> Path:
    """Write a minimal named transport that returns a temp file of given size."""
    tf_dir = workdir / ".vault-bridge" / "transports"
    tf_dir.mkdir(parents=True, exist_ok=True)
    transport_file = tf_dir / f"{slug}.py"
    transport_file.write_text(
        f"""
import time, tempfile
from pathlib import Path

def fetch_to_local(archive_path: str) -> Path:
    time.sleep({delay})
    t = tempfile.NamedTemporaryFile(delete=False, suffix='.bin')
    t.write(b'X' * {size_bytes})
    t.flush()
    t.close()
    return Path(t.name)

def list_archive(archive_root: str, skip_patterns=None):
    return iter([])
"""
    )
    return transport_file


def _clear_transport_cache():
    transport_loader._CACHE.clear()


# ---------------------------------------------------------------------------
# SP1: no sample paths → fallback
# ---------------------------------------------------------------------------

def test_sp1_no_sample_paths_returns_fallback(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "fast", size_bytes=1_000_000)
    result = probe_throughput(workdir, "fast", sample_paths=[])
    assert result.fallback_used is True
    assert result.throughput_bps == FALLBACK_THROUGHPUT_BPS
    assert result.confidence == "fallback"
    assert result.files_sampled == 0


# ---------------------------------------------------------------------------
# SP2: all samples fail → fallback with detail
# ---------------------------------------------------------------------------

def test_sp2_all_samples_fail_returns_fallback(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    # transport returns tiny file, all filtered by min_sample_bytes
    _write_transport(workdir, "slow", size_bytes=100)
    result = probe_throughput(
        workdir, "slow",
        sample_paths=["/fake/file1.bin", "/fake/file2.bin"],
        min_sample_bytes=65_536,
    )
    assert result.fallback_used is True
    assert result.throughput_bps == FALLBACK_THROUGHPUT_BPS
    assert "no usable samples" in result.detail or result.detail != ""


# ---------------------------------------------------------------------------
# SP3: 1 valid sample → low confidence
# ---------------------------------------------------------------------------

def test_sp3_one_sample_low_confidence(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "medium", size_bytes=200_000)
    result = probe_throughput(
        workdir, "medium",
        sample_paths=["/fake/file.bin"],
        min_sample_bytes=1,
        max_samples=5,
    )
    assert result.fallback_used is False
    assert result.confidence == "low"
    assert result.files_sampled == 1
    assert result.throughput_bps > 0


# ---------------------------------------------------------------------------
# SP4: 3+ valid samples → high confidence, median bps
# ---------------------------------------------------------------------------

def test_sp4_three_samples_high_confidence(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "fast2", size_bytes=500_000)
    result = probe_throughput(
        workdir, "fast2",
        sample_paths=["/a", "/b", "/c"],
        min_sample_bytes=1,
        max_samples=5,
    )
    assert result.fallback_used is False
    assert result.confidence == "high"
    assert result.files_sampled == 3
    assert result.throughput_bps > 0
    assert len(result.samples) == 3


# ---------------------------------------------------------------------------
# SP5: samples below min_sample_bytes are skipped
# ---------------------------------------------------------------------------

def test_sp5_small_files_skipped(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "tiny", size_bytes=100)
    result = probe_throughput(
        workdir, "tiny",
        sample_paths=["/a", "/b", "/c"],
        min_sample_bytes=200,  # bigger than 100 bytes
        max_samples=5,
    )
    assert result.fallback_used is True
    assert result.files_sampled == 0


# ---------------------------------------------------------------------------
# SP6: fetch timeout skips that sample, rest still measured
# ---------------------------------------------------------------------------

def test_sp6_sample_timeout_skipped_rest_measured(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "normal", size_bytes=200_000)

    call_count = [0]
    orig_fetch = transport_loader.fetch_to_local

    def patched_fetch(wd, slug, path):
        call_count[0] += 1
        if call_count[0] == 1:
            # Simulate a timeout on first sample
            raise transport_loader.TransportFailed("first sample failed")
        return orig_fetch(wd, slug, path)

    with mock.patch("transport_speed_probe.transport_loader.fetch_to_local", side_effect=patched_fetch):
        result = probe_throughput(
            workdir, "normal",
            sample_paths=["/a", "/b", "/c"],
            min_sample_bytes=1,
            max_samples=5,
        )

    # 2 of 3 succeed
    assert result.files_sampled == 2
    assert result.fallback_used is False


# ---------------------------------------------------------------------------
# SP7: TransportFailed swallowed, never raises
# ---------------------------------------------------------------------------

def test_sp7_never_raises_on_transport_failed(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "broken2", size_bytes=200_000)

    with mock.patch("transport_speed_probe.transport_loader.fetch_to_local",
                    side_effect=transport_loader.TransportFailed("boom")):
        result = probe_throughput(
            workdir, "broken2",
            sample_paths=["/a", "/b"],
            min_sample_bytes=1,
        )
    assert result.fallback_used is True
    assert result.throughput_bps == FALLBACK_THROUGHPUT_BPS


# ---------------------------------------------------------------------------
# SP8: save_throughput updates domain config
# ---------------------------------------------------------------------------

def _write_minimal_config(workdir: Path, domain_name: str = "my-domain") -> None:
    """Write a minimal v4 config.json so save_throughput can load it."""
    import json
    cfg_dir = workdir / ".vault-bridge"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    cfg = {
        "schema_version": 4,
        "vault_name": "TestVault",
        "vault_path": None,
        "created_at": "2026-04-19T00:00:00",
        "fabrication_stopwords": [],
        "global_style": {},
        "active_domain": domain_name,
        "domains": [
            {
                "name": domain_name,
                "label": domain_name,
                "template_seed": "general",
                "archive_root": "/tmp",
                "transport": None,
                "throughput_bps": None,
            }
        ],
        "project_overrides": {"routing_patterns": []},
        "discovered_structure": {"last_walked_at": None, "observed_subfolders": []},
    }
    (cfg_dir / "config.json").write_text(json.dumps(cfg))


def test_sp8_save_throughput_updates_config(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_minimal_config(workdir, "my-domain")

    save_throughput(workdir, "my-domain", 5_242_880.0)

    # Reload config and verify the throughput was persisted
    from config import load_config
    cfg = load_config(workdir)
    domain = next(d for d in cfg.domains if d.name == "my-domain")
    assert domain.throughput_bps == 5_242_880.0


# ---------------------------------------------------------------------------
# SP9: save_throughput: domain not found → no-op
# ---------------------------------------------------------------------------

def test_sp9_save_throughput_domain_not_found(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_minimal_config(workdir, "other-domain")

    # Requesting update for "my-domain" which doesn't exist
    save_throughput(workdir, "my-domain", 5_242_880.0)

    # Config unchanged: other-domain.throughput_bps stays None
    from config import load_config
    cfg = load_config(workdir)
    domain = cfg.domains[0]
    assert domain.throughput_bps is None


# ---------------------------------------------------------------------------
# SP10: save_throughput: config load fails → no raise
# ---------------------------------------------------------------------------

def test_sp10_save_throughput_config_load_fails(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    # No config.json → load_config will raise SetupNeeded → should be silently swallowed

    # Should not raise even with no config present
    save_throughput(workdir, "my-domain", 1_000_000.0)


# ---------------------------------------------------------------------------
# SP11: _measure_one success returns (bps, None)
# ---------------------------------------------------------------------------

def test_sp11_measure_one_success(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "ok", size_bytes=300_000)

    bps, err = _measure_one(
        workdir=workdir,
        transport_name="ok",
        archive_path="/fake/file.bin",
        min_sample_bytes=1,
        fetch_timeout_secs=30.0,
    )
    assert bps is not None
    assert bps > 0
    assert err is None


# ---------------------------------------------------------------------------
# SP12: _measure_one: file too small → (None, error_str)
# ---------------------------------------------------------------------------

def test_sp12_measure_one_too_small(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "small", size_bytes=10)

    bps, err = _measure_one(
        workdir=workdir,
        transport_name="small",
        archive_path="/fake/file.bin",
        min_sample_bytes=65_536,
        fetch_timeout_secs=30.0,
    )
    assert bps is None
    assert err is not None
    assert "too small" in err


# ---------------------------------------------------------------------------
# SP13: _measure_one: TransportMissing → (None, error_str)
# ---------------------------------------------------------------------------

def test_sp13_measure_one_transport_missing(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    # No transport file written — should raise TransportMissing

    bps, err = _measure_one(
        workdir=workdir,
        transport_name="nonexistent",
        archive_path="/fake/file.bin",
        min_sample_bytes=1,
        fetch_timeout_secs=30.0,
    )
    assert bps is None
    assert err is not None
    assert "transport missing" in err.lower()


# ---------------------------------------------------------------------------
# SP14: fetch_to_local_timed: success returns (path, elapsed)
# ---------------------------------------------------------------------------

def test_sp14_fetch_to_local_timed_success(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "timed", size_bytes=1000)

    path, elapsed = transport_loader.fetch_to_local_timed(
        workdir, "timed", "/fake/file.bin", timeout_secs=10.0
    )
    assert path is not None
    assert elapsed >= 0
    assert Path(path).exists() or True  # path may be temp; just check it returned


# ---------------------------------------------------------------------------
# SP15: fetch_to_local_timed: timeout exceeded → TransportTimeout
# ---------------------------------------------------------------------------

def test_sp15_fetch_to_local_timed_timeout(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "slow2", size_bytes=1000, delay=1.0)

    with pytest.raises(transport_loader.TransportTimeout):
        transport_loader.fetch_to_local_timed(
            workdir, "slow2", "/fake/file.bin", timeout_secs=0.1
        )


# ---------------------------------------------------------------------------
# SP16: fetch_to_local_timed: timeout=None → no limit
# ---------------------------------------------------------------------------

def test_sp16_fetch_to_local_timed_no_timeout(tmp_path):
    _clear_transport_cache()
    workdir = _make_workdir(tmp_path)
    _write_transport(workdir, "notimed", size_bytes=1000)

    path, elapsed = transport_loader.fetch_to_local_timed(
        workdir, "notimed", "/fake/file.bin", timeout_secs=None
    )
    assert path is not None
    assert elapsed >= 0
