"""6-step capability probe for /vault-bridge:setup.

Runs end-to-end checks to verify the full image pipeline works before
scanning begins. Writes a memory report with probe results.

Six checks in order:
1. check_obsidian_binary_write — calls vault_binary.probe_binary_write
2. check_transport_fetch — calls transport_loader.fetch_to_local
3. check_compress — feeds tempfile through compress_images.compress_image
4. check_extract — if container sample given, runs extract_embedded_images.extract
5. check_vision — calls vision_callback; success = non-empty string ≥10 chars
6. check_vault_write_full — writes compressed JPEG to vault, compares sha256, deletes

Python 3.9 compatible.
"""
import hashlib
import sys
import tempfile
from pathlib import Path
from typing import Callable, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import compress_images
import extract_embedded_images
import local_config  # noqa: F401 — kept for backward compat; new code uses config module
import memory_report
import transport_loader
import transport_speed_probe
import vault_binary

# Size of the hardcoded probe PNG for test injection
_PROBE_PNG_SIZE = len(vault_binary.PROBE_PNG_BYTES)


def run_probe(
    workdir: Path,
    vault_name: str,
    sample_archive_paths: List[str],
    sample_container_path: Optional[str],
    vision_callback: Callable[[Path], str],
    runner: Optional[Callable] = None,
    transport_name: Optional[str] = None,
    domain_name: Optional[str] = None,
) -> Dict:
    """Run 6 capability checks plus an optional transport speed probe (check 2.5).

    Args:
        workdir: Working directory with .vault-bridge/transport.py.
        vault_name: Obsidian vault name.
        sample_archive_paths: List of archive paths to test (uses first one).
        sample_container_path: Optional path to a PDF/DOCX/PPTX for extraction test.
        vision_callback: Callable that takes a JPEG path and returns a description string.
        runner: Optional subprocess runner injectable for tests.
        transport_name: Named transport slug. When set, check 2.5 (speed probe) runs.
        domain_name: Domain name used to persist measured throughput_bps in config.

    Returns:
        {
            "ok": bool,
            "checks": List[{"name": str, "ok": bool, "detail": str, "error": Optional[str]}],
            "sample_used": str,
            "vision_description": str,
            "report_path": str,
            "throughput_bps": Optional[float],
        }
    """
    checks: List[Dict] = []
    overall_ok = True
    sample_used = sample_archive_paths[0] if sample_archive_paths else ""
    vision_description = ""
    compressed_path: Optional[Path] = None
    fetched_path: Optional[Path] = None
    measured_throughput_bps: Optional[float] = None

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir_path = Path(tmpdir)

        # ------------------------------------------------------------------
        # Check 1: check_obsidian_binary_write
        # ------------------------------------------------------------------
        check1 = _run_check(
            "check_obsidian_binary_write",
            lambda: _check_obsidian_binary_write(vault_name, runner),
        )
        checks.append(check1)
        if not check1["ok"]:
            overall_ok = False
            # Don't stop on check 1 failure — transport might still work
            # But mark remaining checks as skipped
            # Actually per spec: only check 2 failure stops subsequent checks
            # Check 1 failure is noted but we continue

        # ------------------------------------------------------------------
        # Check 2: check_transport_fetch
        # ------------------------------------------------------------------
        check2 = _run_check(
            "check_transport_fetch",
            lambda: _check_transport_fetch(workdir, sample_used, tmpdir_path, transport_name=transport_name),
        )
        checks.append(check2)
        if not check2["ok"]:
            overall_ok = False
            # Stop subsequent checks on transport failure
            checks.extend(_skipped_checks(
                ["check_compress", "check_extract", "check_vision", "check_vault_write_full"],
                reason="transport fetch failed",
            ))
            return _build_result(
                overall_ok, checks, sample_used, vision_description,
                workdir, vault_name, tmpdir_path,
                throughput_bps=None,
            )

        # Capture fetched path for subsequent checks
        fetched_path = check2.get("_path")

        # ------------------------------------------------------------------
        # Check 2.5: check_transport_speed (optional — only when transport_name given)
        # ------------------------------------------------------------------
        if transport_name:
            check25 = _run_check(
                "check_transport_speed",
                lambda: _check_transport_speed(workdir, transport_name, domain_name, sample_archive_paths),
            )
            checks.append(check25)
            if check25.get("ok"):
                measured_throughput_bps = check25.get("_throughput_bps")
            # Non-fatal: speed probe failure does not block other checks

        # ------------------------------------------------------------------
        # Check 3: check_compress
        # ------------------------------------------------------------------
        compress_dir = tmpdir_path / "compressed"
        compress_dir.mkdir(exist_ok=True)
        check3 = _run_check(
            "check_compress",
            lambda: _check_compress(fetched_path, compress_dir),
        )
        checks.append(check3)
        if not check3["ok"]:
            overall_ok = False
            checks.extend(_skipped_checks(
                ["check_extract", "check_vision", "check_vault_write_full"],
                reason="compress failed",
            ))
            return _build_result(
                overall_ok, checks, sample_used, vision_description,
                workdir, vault_name, tmpdir_path,
            )

        compressed_path = check3.get("_path")

        # ------------------------------------------------------------------
        # Check 4: check_extract
        # ------------------------------------------------------------------
        if sample_container_path is None:
            check4: Dict = {
                "name": "check_extract",
                "ok": True,
                "detail": "skipped — no container sample provided",
                "error": None,
            }
        else:
            extract_dir = tmpdir_path / "extracted"
            extract_dir.mkdir(exist_ok=True)
            check4 = _run_check(
                "check_extract",
                lambda: _check_extract(
                    workdir,
                    sample_container_path,
                    extract_dir,
                    transport_name=transport_name,
                ),
            )
            if not check4["ok"]:
                overall_ok = False
        checks.append(check4)

        # ------------------------------------------------------------------
        # Check 5: check_vision
        # ------------------------------------------------------------------
        check5 = _run_check(
            "check_vision",
            lambda: _check_vision(compressed_path, vision_callback),
        )
        checks.append(check5)
        if check5["ok"]:
            vision_description = check5.get("_description", "")
        else:
            overall_ok = False

        # ------------------------------------------------------------------
        # Check 6: check_vault_write_full
        # ------------------------------------------------------------------
        check6 = _run_check(
            "check_vault_write_full",
            lambda: _check_vault_write_full(vault_name, compressed_path, runner),
        )
        checks.append(check6)
        if not check6["ok"]:
            overall_ok = False

    return _build_result(
        overall_ok, checks, sample_used, vision_description,
        workdir, vault_name, None,
        throughput_bps=measured_throughput_bps,
    )


# ---------------------------------------------------------------------------
# Individual check implementations
# ---------------------------------------------------------------------------

def _check_obsidian_binary_write(vault_name: str, runner: Optional[Callable]) -> Dict:
    """Check 1: probe vault binary write capability."""
    result = vault_binary.probe_binary_write(vault_name, runner=runner)
    return {
        "ok": result["ok"],
        "detail": result.get("detail", ""),
        "error": result.get("error"),
    }


def _check_transport_fetch(
    workdir: Path,
    archive_path: str,
    tmpdir: Path,
    transport_name: Optional[str] = None,
) -> Dict:
    """Check 2: fetch a file via transport.fetch_to_local.

    When `transport_name` is given, use the 3-arg form that picks the named
    transport explicitly. Without it, falls back to the legacy 2-arg form
    (relies on the single-transport convention).
    """
    try:
        if transport_name:
            local = transport_loader.fetch_to_local(workdir, transport_name, archive_path)
        else:
            local = transport_loader.fetch_to_local(workdir, archive_path)
        return {
            "ok": True,
            "detail": f"fetched to {local}",
            "error": None,
            "_path": local,
        }
    except transport_loader.TransportMissing as exc:
        return {"ok": False, "detail": "transport missing", "error": str(exc), "_path": None}
    except transport_loader.TransportInvalid as exc:
        return {"ok": False, "detail": "transport invalid", "error": str(exc), "_path": None}
    except transport_loader.TransportFailed as exc:
        return {"ok": False, "detail": "transport failed", "error": str(exc), "_path": None}
    except Exception as exc:
        return {"ok": False, "detail": "transport error", "error": str(exc), "_path": None}


def _check_compress(src_path: Optional[Path], compress_dir: Path) -> Dict:
    """Check 3: compress a local image and verify JPEG magic."""
    if src_path is None:
        return {"ok": False, "detail": "no source path", "error": "no source path", "_path": None}
    try:
        out = compress_images.compress_image(src_path, compress_dir, "2026-04-16")
        magic = out.read_bytes()[:2]
        if magic != b"\xff\xd8":
            return {
                "ok": False,
                "detail": f"output not JPEG (magic: {magic.hex()})",
                "error": "JPEG magic check failed",
                "_path": out,
            }
        return {
            "ok": True,
            "detail": f"compressed to {out.name} ({out.stat().st_size} bytes)",
            "error": None,
            "_path": out,
        }
    except compress_images.CompressError as exc:
        return {"ok": False, "detail": "compress failed", "error": str(exc), "_path": None}
    except Exception as exc:
        return {"ok": False, "detail": "compress error", "error": str(exc), "_path": None}


def _check_extract(
    workdir: Path,
    container_path: str,
    extract_dir: Path,
    transport_name: Optional[str] = None,
) -> Dict:
    """Check 4: extract images from a container document.

    The `container_path` is an archive path (remote or local). We always
    route it through `transport_loader.fetch_to_local` first so SFTP /
    SMB / cloud transports work the same way the scan pipeline does.
    Without this, SFTP archives raise FileNotFoundError here while
    scanning would actually succeed (issue: setup_probe bypassed fetch).
    """
    try:
        if transport_name:
            local = transport_loader.fetch_to_local(
                workdir, transport_name, container_path
            )
        else:
            local = transport_loader.fetch_to_local(workdir, container_path)
    except transport_loader.TransportMissing as exc:
        return {"ok": False, "detail": "transport missing", "error": str(exc)}
    except transport_loader.TransportInvalid as exc:
        return {"ok": False, "detail": "transport invalid", "error": str(exc)}
    except transport_loader.TransportFailed as exc:
        return {"ok": False, "detail": "fetch failed for container", "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "detail": "fetch error", "error": str(exc)}

    path = Path(local)
    ft = path.suffix.lower().lstrip(".")
    try:
        results = extract_embedded_images.extract(path, extract_dir, ft)
        if not results:
            return {
                "ok": False,
                "detail": "extract returned no images",
                "error": "no images found in container",
            }
        return {
            "ok": True,
            "detail": f"extracted {len(results)} image(s)",
            "error": None,
        }
    except Exception as exc:
        return {"ok": False, "detail": "extract error", "error": str(exc)}


def _check_vision(
    jpeg_path: Optional[Path],
    vision_callback: Callable[[Path], str],
) -> Dict:
    """Check 5: call vision_callback; success = non-empty string ≥10 chars."""
    if jpeg_path is None:
        return {"ok": False, "detail": "no compressed image available", "error": "no input"}
    try:
        description = vision_callback(jpeg_path)
        if not description or len(description) < 10:
            return {
                "ok": False,
                "detail": f"vision returned too-short description: {description!r}",
                "error": "description too short (< 10 chars)",
                "_description": description or "",
            }
        return {
            "ok": True,
            "detail": f"vision returned: {description[:80]}",
            "error": None,
            "_description": description,
        }
    except Exception as exc:
        return {"ok": False, "detail": "vision error", "error": str(exc), "_description": ""}


def _check_transport_speed(
    workdir: Path,
    transport_name: str,
    domain_name: Optional[str],
    sample_paths: List[str],
) -> Dict:
    """Check 2.5: probe transport read throughput and persist it."""
    result = transport_speed_probe.probe_throughput(
        workdir=workdir,
        transport_name=transport_name,
        sample_paths=sample_paths,
    )
    if domain_name:
        transport_speed_probe.save_throughput(workdir, domain_name, result.throughput_bps)

    mbps = result.throughput_bps / 1_048_576
    detail = f"{mbps:.2f} MB/s ({result.confidence} confidence, {result.files_sampled} sample(s))"
    if result.fallback_used:
        detail = f"fallback speed used (1 MB/s): {result.detail}"

    return {
        "ok": not result.fallback_used,
        "detail": detail,
        "error": result.detail if result.fallback_used else None,
        "_throughput_bps": result.throughput_bps,
    }


def _check_vault_write_full(
    vault_name: str,
    compressed_path: Optional[Path],
    runner: Optional[Callable],
) -> Dict:
    """Check 6: write compressed JPEG to vault probe path, then clean up."""
    if compressed_path is None:
        return {"ok": False, "detail": "no compressed image to write", "error": "no input"}
    try:
        probe_hash = hashlib.sha256(compressed_path.read_bytes()).hexdigest()[:8]
        # Probe path namespaced under _vb-probe/ so we don't leave an empty
        # _Attachments/ folder at the vault root after cleanup.
        vault_dst = f"_vb-probe/{probe_hash}_probe.jpg"
        result = vault_binary.write_binary(
            vault_name=vault_name,
            src_abs_path=compressed_path,
            vault_dst_path=vault_dst,
            runner=runner,
        )
        if result["ok"]:
            return {
                "ok": True,
                "detail": f"written to vault: {vault_dst}",
                "error": None,
            }
        return {
            "ok": False,
            "detail": "vault write failed",
            "error": result.get("error"),
        }
    except Exception as exc:
        return {"ok": False, "detail": "vault write error", "error": str(exc)}
    finally:
        # Always clean the probe folder — don't leak test artifacts into the vault.
        effective_runner = runner if runner is not None else vault_binary._default_runner
        vault_binary._delete_vault_path(vault_name, "_vb-probe", effective_runner)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run_check(name: str, fn: Callable) -> Dict:
    """Run a check function and ensure the result has the required keys."""
    try:
        result = fn()
        result.setdefault("name", name)
        result.setdefault("ok", False)
        result.setdefault("detail", "")
        result.setdefault("error", None)
        return result
    except Exception as exc:
        return {
            "name": name,
            "ok": False,
            "detail": f"check raised: {exc}",
            "error": str(exc),
        }


def _skipped_checks(names: List[str], reason: str) -> List[Dict]:
    """Return stub skipped-check dicts for checks that didn't run."""
    return [
        {
            "name": name,
            "ok": False,
            "detail": f"skipped — {reason}",
            "error": reason,
        }
        for name in names
    ]


def _build_result(
    overall_ok: bool,
    checks: List[Dict],
    sample_used: str,
    vision_description: str,
    workdir: Path,
    vault_name: str,
    tmpdir: Optional[Path],
    throughput_bps: Optional[float] = None,
) -> Dict:
    """Build the final result dict and write a memory report."""
    # Clean up internal-only keys before returning
    clean_checks = []
    for c in checks:
        clean = {k: v for k, v in c.items() if not k.startswith("_")}
        clean_checks.append(clean)

    # Write memory report
    stats = {
        "vault_name": vault_name,
        "sample_used": sample_used,
        "probe_results": clean_checks,
        "vision_description": vision_description,
        "counts": {
            "checks_run": len(clean_checks),
            "checks_passed": sum(1 for c in clean_checks if c.get("ok")),
        },
    }
    report_path_str = ""
    try:
        # Check if configured: try new v3 config first, fall back to old is_setup
        _is_configured = False
        try:
            from config import load_config, SetupNeeded
            load_config(workdir)
            _is_configured = True
        except Exception:
            _is_configured = local_config.is_setup(workdir)
        if _is_configured:
            report_path = memory_report.write_report(workdir, "probe", stats)
            report_path_str = str(report_path)
    except Exception as e:
        import sys
        print(f"WARNING: could not write probe report: {e}", file=sys.stderr)

    return {
        "ok": overall_ok,
        "checks": clean_checks,
        "sample_used": sample_used,
        "vision_description": vision_description,
        "report_path": report_path_str,
        "throughput_bps": throughput_bps,
    }
