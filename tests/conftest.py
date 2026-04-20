"""Shared pytest fixtures for vault-bridge tests.

Most unit/integration tests use tiny mocked JPEGs (e.g. 10x10 plain
colour) whose compressed output is ~500 bytes — well under the
production `IMAGE_MIN_BYTES` size gate. Tests here are about pipeline
wiring, not about the gate itself, so we disable it globally. The
gate's own behaviour is covered by targeted tests in
`test_attachment_dedup.py`.
"""
import sys
from pathlib import Path

import pytest

# Make scripts/ importable without relying on individual tests to do it.
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


@pytest.fixture(autouse=True)
def _disable_image_min_bytes(monkeypatch):
    """Zero out the image size gate for tests that use tiny fixtures."""
    try:
        import scan_pipeline
    except ImportError:
        return
    monkeypatch.setattr(scan_pipeline, "IMAGE_MIN_BYTES", 0, raising=False)
