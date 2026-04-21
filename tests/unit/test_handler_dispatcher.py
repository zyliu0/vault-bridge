"""Tests for scripts/handler_dispatcher.py (F1 + F6).

Closes the gap where `file_type_handlers.extract_images` silently
returned [] for cad-*, vector-ai, and raster-psd categories even
though their HandlerConfig advertised extract_images=True.
"""
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import handler_dispatcher  # noqa: E402
import file_type_handlers  # noqa: E402


@pytest.fixture
def workdir_with_dxf_handler(tmp_path):
    """Create a workdir whose .vault-bridge/handlers/cad_dxf_dxf.py
    exposes deterministic read_text and extract_images for tests."""
    handlers_dir = tmp_path / ".vault-bridge" / "handlers"
    handlers_dir.mkdir(parents=True)
    (handlers_dir / "cad_dxf_dxf.py").write_text(
        "from pathlib import Path\n"
        "def read_text(path):\n"
        "    return 'TEXT FROM HANDLER for ' + Path(path).name\n"
        "def extract_images(path, out_dir):\n"
        "    p = Path(out_dir) / (Path(path).stem + '-page001.png')\n"
        "    p.parent.mkdir(parents=True, exist_ok=True)\n"
        "    p.write_bytes(b'\\x89PNG fake')\n"
        "    return [str(p)]\n",
        encoding="utf-8",
    )
    return tmp_path


# ---------------------------------------------------------------------------
# handler_dispatcher direct API
# ---------------------------------------------------------------------------

def test_is_delegated_covers_expected_categories():
    """Categories with workdir handlers are declared; hardcoded ones are not."""
    for cat in ("cad-dxf", "cad-dwg", "cad-3dm", "vector-ai", "raster-psd",
                "document-office-legacy", "spreadsheet-legacy"):
        assert handler_dispatcher.is_delegated(cat)
    for cat in ("document-pdf", "document-office", "image-raster",
                "image-vector", "video", "audio", "text-plain", "archive"):
        assert not handler_dispatcher.is_delegated(cat)


def test_read_text_returns_empty_without_workdir():
    assert handler_dispatcher.read_text(None, "cad-dxf", "x.dxf") == ""


def test_read_text_returns_empty_when_handler_missing(tmp_path):
    """Workdir exists but no handler file → empty, not error."""
    result = handler_dispatcher.read_text(str(tmp_path), "cad-dxf", "x.dxf")
    assert result == ""


def test_read_text_dispatches_to_handler(workdir_with_dxf_handler, tmp_path):
    dxf = tmp_path / "drawing.dxf"
    dxf.write_text("FAKE DXF")
    result = handler_dispatcher.read_text(
        str(workdir_with_dxf_handler), "cad-dxf", str(dxf)
    )
    assert result == "TEXT FROM HANDLER for drawing.dxf"


def test_extract_images_dispatches_to_handler(workdir_with_dxf_handler, tmp_path):
    dxf = tmp_path / "drawing.dxf"
    dxf.write_text("FAKE DXF")
    result = handler_dispatcher.extract_images(
        str(workdir_with_dxf_handler), "cad-dxf", str(dxf)
    )
    assert len(result) == 1
    assert result[0].name == "drawing-page001.png"
    assert result[0].exists()


def test_extract_images_returns_empty_when_handler_throws(tmp_path):
    """Handler raising is logged and returns [] — never propagates."""
    handlers_dir = tmp_path / ".vault-bridge" / "handlers"
    handlers_dir.mkdir(parents=True)
    (handlers_dir / "cad_dxf_dxf.py").write_text(
        "def read_text(path): raise RuntimeError('boom')\n"
        "def extract_images(path, out_dir): raise RuntimeError('boom')\n",
        encoding="utf-8",
    )
    dxf = tmp_path / "bad.dxf"
    dxf.write_text("")
    assert handler_dispatcher.read_text(str(tmp_path), "cad-dxf", str(dxf)) == ""
    assert handler_dispatcher.extract_images(str(tmp_path), "cad-dxf", str(dxf)) == []


# ---------------------------------------------------------------------------
# file_type_handlers integration
# ---------------------------------------------------------------------------

def test_file_type_handlers_extract_images_delegates_to_workdir(workdir_with_dxf_handler, tmp_path):
    dxf = tmp_path / "drawing.dxf"
    dxf.write_text("FAKE")
    result = file_type_handlers.extract_images(str(dxf), workdir=str(workdir_with_dxf_handler))
    assert len(result) == 1
    assert result[0].exists()


def test_file_type_handlers_read_text_delegates_to_workdir(workdir_with_dxf_handler, tmp_path):
    dxf = tmp_path / "drawing.dxf"
    dxf.write_text("FAKE")
    result = file_type_handlers.read_text(str(dxf), workdir=str(workdir_with_dxf_handler))
    assert "TEXT FROM HANDLER" in result


def test_file_type_handlers_extract_images_without_workdir_returns_empty_for_dxf(tmp_path):
    """Before F1 this was the silent-skip path. Now it still returns []
    when no workdir is passed — the caller (scan_pipeline) owns the
    workdir thread-through; this preserves backwards compatibility for
    direct library use while still closing the production gap."""
    dxf = tmp_path / "drawing.dxf"
    dxf.write_text("FAKE")
    assert file_type_handlers.extract_images(str(dxf)) == []


# ---------------------------------------------------------------------------
# v14.5 field-review Issue 1 — stub detection + coverage report
# ---------------------------------------------------------------------------

class TestStubDetection:
    def test_file_with_todo_marker_is_stub(self, tmp_path):
        p = tmp_path / "cad_dxf_dxf.py"
        p.write_text(
            "def read_text(path):\n"
            "    # TODO: implement\n"
            "    return ''\n"
            "def extract_images(path, out_dir):\n"
            "    return []\n",
            encoding="utf-8",
        )
        assert handler_dispatcher.is_stub_module(p)

    def test_real_file_is_not_stub(self, tmp_path):
        p = tmp_path / "cad_dxf_dxf.py"
        p.write_text(
            "import ezdxf\n"
            "def read_text(path):\n"
            "    doc = ezdxf.readfile(path)\n"
            "    return '\\n'.join(e.dxf.text for e in doc.modelspace())\n"
            "def extract_images(path, out_dir):\n"
            "    return ['render.png']\n",
            encoding="utf-8",
        )
        assert not handler_dispatcher.is_stub_module(p)

    def test_trivial_returns_are_stub(self, tmp_path):
        p = tmp_path / "cad_dxf_dxf.py"
        p.write_text(
            "def read_text(path: str) -> str:\n"
            "    return ''\n"
            "def extract_images(path: str, out_dir: str) -> list:\n"
            "    return []\n",
            encoding="utf-8",
        )
        assert handler_dispatcher.is_stub_module(p)

    def test_raises_notimplemented_is_stub(self, tmp_path):
        p = tmp_path / "cad_dxf_dxf.py"
        p.write_text(
            "def read_text(path):\n"
            "    raise NotImplementedError\n"
            "def extract_images(path, out_dir):\n"
            "    raise NotImplementedError\n",
            encoding="utf-8",
        )
        assert handler_dispatcher.is_stub_module(p)


class TestCoverageReport:
    def test_none_workdir_marks_all_categories_missing(self):
        cov = handler_dispatcher.coverage_report(None)
        assert set(cov.missing) == set(handler_dispatcher.DELEGATED_CATEGORIES)
        assert not cov.has_stubs()
        assert cov.real == []

    def test_missing_handlers_dir_marks_all_missing(self, tmp_path):
        cov = handler_dispatcher.coverage_report(str(tmp_path))
        assert set(cov.missing) == set(handler_dispatcher.DELEGATED_CATEGORIES)

    def test_classifies_real_stub_and_missing(self, tmp_path):
        handlers = tmp_path / ".vault-bridge" / "handlers"
        handlers.mkdir(parents=True)
        # cad-dxf: real
        (handlers / "cad_dxf_dxf.py").write_text(
            "import ezdxf\n"
            "def read_text(path):\n"
            "    doc = ezdxf.readfile(path)\n"
            "    return doc.text\n"
            "def extract_images(path, out_dir):\n"
            "    return ['x.png']\n",
            encoding="utf-8",
        )
        # raster-psd: stub (TODO marker)
        (handlers / "raster_psd_psd.py").write_text(
            "def read_text(path):\n"
            "    # TODO: implement\n"
            "    return ''\n"
            "def extract_images(path, out_dir):\n"
            "    return []\n",
            encoding="utf-8",
        )
        # All others: missing
        cov = handler_dispatcher.coverage_report(str(tmp_path))
        assert "cad-dxf" in cov.real
        assert "raster-psd" in cov.stub
        assert "cad-dwg" in cov.missing
        assert cov.has_stubs()

    def test_to_lines_produces_readable_output(self, tmp_path):
        handlers = tmp_path / ".vault-bridge" / "handlers"
        handlers.mkdir(parents=True)
        (handlers / "cad_dxf_dxf.py").write_text(
            "def read_text(path):\n"
            "    # TODO: implement\n"
            "    return ''\n"
            "def extract_images(path, out_dir):\n"
            "    return []\n",
            encoding="utf-8",
        )
        cov = handler_dispatcher.coverage_report(str(tmp_path))
        lines = cov.to_lines()
        assert any("stubs:" in line for line in lines)
