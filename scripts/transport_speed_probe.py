"""Transport read-throughput measurement for vault-bridge setup.

Probes the actual read speed of an archive transport by fetching a small
sample of files, timing each fetch, and computing the median bytes/sec.
The result is stored in Domain.throughput_bps and used by scan_pipeline
to compute per-file read timeouts.

Public API:
    probe_throughput(workdir, transport_name, sample_paths, *, ...) -> ThroughputResult
    save_throughput(workdir, domain_name, throughput_bps) -> None

Python 3.9 compatible.
"""
from __future__ import annotations

import os
import statistics
import sys
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import transport_loader

# Conservative floor used when measurement fails or no samples are available.
FALLBACK_THROUGHPUT_BPS: float = 1_048_576  # 1 MB/s

# Files smaller than this produce noisy measurements (filesystem/cache effects dominate).
_MIN_SAMPLE_BYTES: int = 65_536  # 64 KB

# How long to wait for a single sample fetch before giving up on that sample.
_SAMPLE_FETCH_TIMEOUT_SECS: float = 30.0

# Maximum number of sample files to measure.
_MAX_SAMPLES: int = 5


@dataclass
class ThroughputResult:
    """Result of a transport throughput probe."""
    throughput_bps: float
    samples: List[float] = field(default_factory=list)
    files_sampled: int = 0
    confidence: str = "fallback"      # "high" (≥3 samples) | "low" (1-2) | "fallback"
    fallback_used: bool = False
    detail: str = ""


def probe_throughput(
    workdir: Path,
    transport_name: str,
    sample_paths: List[str],
    *,
    min_sample_bytes: int = _MIN_SAMPLE_BYTES,
    fetch_timeout_secs: float = _SAMPLE_FETCH_TIMEOUT_SECS,
    max_samples: int = _MAX_SAMPLES,
) -> ThroughputResult:
    """Measure transport read throughput using a sample of archive files.

    Fetches up to max_samples files, times each fetch, computes median bytes/sec.
    Never raises — returns FALLBACK_THROUGHPUT_BPS on any failure.

    Args:
        workdir:           Working directory (for transport_loader).
        transport_name:    Named transport slug (e.g. "home-nas-smb").
        sample_paths:      Archive paths to test (uses first max_samples).
        min_sample_bytes:  Skip files smaller than this (measurement noise).
        fetch_timeout_secs: Per-sample fetch timeout.
        max_samples:       Maximum number of files to measure.

    Returns:
        ThroughputResult with median throughput and confidence level.
    """
    candidates = sample_paths[:max_samples]
    sample_bps: List[float] = []
    errors: List[str] = []

    for archive_path in candidates:
        bps, err = _measure_one(
            workdir=workdir,
            transport_name=transport_name,
            archive_path=archive_path,
            min_sample_bytes=min_sample_bytes,
            fetch_timeout_secs=fetch_timeout_secs,
        )
        if bps is not None:
            sample_bps.append(bps)
        elif err:
            errors.append(err)

    if not sample_bps:
        detail = f"no usable samples; errors: {'; '.join(errors)}" if errors else "no sample files provided"
        return ThroughputResult(
            throughput_bps=FALLBACK_THROUGHPUT_BPS,
            samples=[],
            files_sampled=0,
            confidence="fallback",
            fallback_used=True,
            detail=detail,
        )

    median_bps = statistics.median(sample_bps)
    n = len(sample_bps)
    confidence = "high" if n >= 3 else "low"
    mbps = median_bps / 1_048_576
    detail = f"{mbps:.2f} MB/s median over {n} sample(s)"

    return ThroughputResult(
        throughput_bps=float(median_bps),
        samples=sample_bps,
        files_sampled=n,
        confidence=confidence,
        fallback_used=False,
        detail=detail,
    )


def save_throughput(workdir: Path, domain_name: str, throughput_bps: float) -> None:
    """Persist throughput_bps into the named domain's config entry.

    Loads config, updates the domain, saves. No-op if domain not found.
    """
    try:
        from config import load_config, save_config
        cfg = load_config(workdir)
        for domain in cfg.domains:
            if domain.name == domain_name:
                domain.throughput_bps = throughput_bps
                save_config(workdir, cfg)
                return
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _measure_one(
    workdir: Path,
    transport_name: str,
    archive_path: str,
    min_sample_bytes: int,
    fetch_timeout_secs: float,
) -> tuple:
    """Fetch one archive file, measure elapsed time, return (bps, error_str).

    Returns (None, error_str) if the sample is unusable.
    Returns (bps_float, None) on success.
    """
    # Get file size before fetching — stat the archive path directly if possible,
    # otherwise measure the fetched local copy.
    try:
        stat_size = _stat_archive_path(archive_path)
    except Exception:
        stat_size = None

    # Fetch with timeout
    t0 = time.monotonic()
    local_path: Optional[Path] = None

    def _fetch():
        return transport_loader.fetch_to_local(workdir, transport_name, archive_path)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(_fetch)
            try:
                local_path = Path(future.result(timeout=fetch_timeout_secs))
            except FuturesTimeout:
                future.cancel()
                return None, f"{archive_path!r}: fetch timed out after {fetch_timeout_secs}s"
    except transport_loader.TransportMissing as exc:
        return None, f"transport missing: {exc}"
    except transport_loader.TransportInvalid as exc:
        return None, f"transport invalid: {exc}"
    except transport_loader.TransportFailed as exc:
        return None, f"transport failed: {exc}"
    except Exception as exc:
        return None, f"unexpected error: {exc}"

    elapsed = time.monotonic() - t0

    # Determine file size
    file_bytes = stat_size
    if file_bytes is None and local_path is not None:
        try:
            file_bytes = local_path.stat().st_size
        except Exception:
            pass

    if file_bytes is None or file_bytes < min_sample_bytes:
        size_str = f"{file_bytes}B" if file_bytes is not None else "unknown size"
        return None, f"{archive_path!r}: too small ({size_str} < {min_sample_bytes}B)"

    if elapsed <= 0:
        return None, f"{archive_path!r}: elapsed time zero"

    bps = file_bytes / elapsed
    return bps, None


def _stat_archive_path(archive_path: str) -> Optional[int]:
    """Return file size for a local path, or None for remote/unknown paths."""
    p = Path(archive_path)
    if p.exists():
        return p.stat().st_size
    return None
