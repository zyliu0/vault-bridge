"""Package registry for vault-bridge file-type handling.

Provides the canonical list of packages that can handle each file extension,
including preferred vs fallback choices (e.g., pdfplumber over PyPDF2).

Public API
----------
    PackageSpec        — dataclass describing a single package
    BUILTIN_REGISTRY   — dict[str, list[PackageSpec]] keyed by lowercase extension (no dot)
    for_extension(ext) -> list[PackageSpec]
    default_for(ext)   -> PackageSpec | None   (returns preferred=True entry)
    is_installed(spec) -> bool

Python 3.9 compatible.
"""
import importlib.util
from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# PackageSpec dataclass
# ---------------------------------------------------------------------------

@dataclass
class PackageSpec:
    """Descriptor for a single Python package that handles one or more file types.

    Attributes:
        pip_name:       Name used with `pip install`. Empty string for stdlib.
        import_name:    Name used with `import` / `importlib`. Empty for stdlib.
        category:       Handler category slug (e.g. 'document-pdf').
        extensions:     File extensions this package handles (no dot, lowercase).
        extract_text:   Whether this package can extract text.
        extract_images: Whether this package can extract images.
        github_url:     Source repository URL (informational).
        preferred:      True if this is the recommended choice for its extensions.
        notes:          Human-readable notes about this package.
    """
    pip_name: str
    import_name: str
    category: str
    extensions: List[str]
    extract_text: bool
    extract_images: bool
    github_url: str
    preferred: bool
    notes: str = ""


# ---------------------------------------------------------------------------
# Built-in registry
# ---------------------------------------------------------------------------

# Each entry is a list so multiple packages can cover the same extension.
# The preferred=True entry is the one returned by default_for().

_PDFPLUMBER = PackageSpec(
    pip_name="pdfplumber",
    import_name="pdfplumber",
    category="document-pdf",
    extensions=["pdf"],
    extract_text=True,
    extract_images=False,
    github_url="https://github.com/jsvine/pdfplumber",
    preferred=True,
    notes="Preferred PDF text extractor; detailed layout-aware extraction.",
)

_PYPDF2 = PackageSpec(
    pip_name="PyPDF2",
    import_name="PyPDF2",
    category="document-pdf",
    extensions=["pdf"],
    extract_text=True,
    extract_images=False,
    github_url="https://github.com/py-pdf/pypdf",
    preferred=False,
    notes="Fallback PDF reader; being superseded by pypdf.",
)

_PYTHON_DOCX = PackageSpec(
    pip_name="python-docx",
    import_name="docx",
    category="document-office",
    extensions=["docx"],
    extract_text=True,
    extract_images=True,
    github_url="https://github.com/python-openxml/python-docx",
    preferred=True,
    notes="Microsoft Word XML document reader (.docx only).",
)

_PYTHON_PPTX = PackageSpec(
    pip_name="python-pptx",
    import_name="pptx",
    category="document-office",
    extensions=["pptx"],
    extract_text=True,
    extract_images=True,
    github_url="https://github.com/scanny/python-pptx",
    preferred=True,
    notes="Microsoft PowerPoint XML presentation reader (.pptx only).",
)

_OPENPYXL = PackageSpec(
    pip_name="openpyxl",
    import_name="openpyxl",
    category="document-office",
    extensions=["xlsx"],
    extract_text=True,
    extract_images=True,
    github_url="https://github.com/theorchard/openpyxl",
    preferred=True,
    notes="Microsoft Excel spreadsheet reader; returns cell values and embedded images.",
)

_PILLOW = PackageSpec(
    pip_name="Pillow",
    import_name="PIL",
    category="image-raster",
    extensions=["jpg", "jpeg", "png", "webp", "gif", "bmp", "tiff", "tif"],
    extract_text=False,
    extract_images=True,
    github_url="https://github.com/python-pillow/Pillow",
    preferred=True,
    notes="Image processing library; handles all common raster formats.",
)

_PILLOW_HEIF = PackageSpec(
    pip_name="pillow-heif",
    import_name="pillow_heif",
    category="image-raster",
    extensions=["heic", "heif"],
    extract_text=False,
    extract_images=True,
    github_url="https://github.com/bigcat88/pillow_heif",
    preferred=True,
    notes="HEIC/HEIF support for Pillow (Apple Live Photos, iPhone images).",
)

_STDLIB_TEXT = PackageSpec(
    pip_name="",
    import_name="",
    category="text-plain",
    extensions=["txt", "md", "rtf"],
    extract_text=True,
    extract_images=False,
    github_url="",
    preferred=True,
    notes="stdlib — no package needed; read with open().",
)

# ---------------------------------------------------------------------------
# Visual/CAD file-type entries
# ---------------------------------------------------------------------------

_OLEFILE = PackageSpec(
    pip_name="olefile",
    import_name="olefile",
    category="document-office-legacy",
    extensions=["doc", "ppt"],
    extract_text=True,
    extract_images=False,
    github_url="https://github.com/decalage2/olefile",
    preferred=True,
    notes="Legacy binary Office; text extraction via OLE2 stream parsing.",
)

_XLRD = PackageSpec(
    pip_name="xlrd",
    import_name="xlrd",
    category="spreadsheet-legacy",
    extensions=["xls"],
    extract_text=True,
    extract_images=False,
    github_url="https://github.com/python-excel/xlrd",
    preferred=True,
    notes="Legacy XLS binary spreadsheet reader (Excel 97-2003).",
)

_EZDXF_DXF = PackageSpec(
    pip_name="ezdxf[draw]",
    import_name="ezdxf",
    category="cad-dxf",
    extensions=["dxf"],
    extract_text=True,
    extract_images=True,
    github_url="https://github.com/mozman/ezdxf",
    preferred=True,
    notes="Reads text entities; renders to PNG via matplotlib. "
          "ezdxf[draw] includes matplotlib; first install may take 60-120s.",
)

_EZDXF_DWG = PackageSpec(
    pip_name="ezdxf[draw]",
    import_name="ezdxf",
    category="cad-dwg",
    extensions=["dwg"],
    extract_text=True,
    extract_images=True,
    github_url="https://github.com/mozman/ezdxf",
    preferred=True,
    notes="ezdxf native DWG reader covers R2004-R2018; MIT license, no external tools needed.",
)

_PYMUPDF_AI = PackageSpec(
    pip_name="PyMuPDF",
    import_name="fitz",
    category="vector-ai",
    extensions=["ai"],
    extract_text=True,
    extract_images=True,
    github_url="https://github.com/pymupdf/PyMuPDF",
    preferred=True,
    notes="Modern .ai is PDF-compatible; PyMuPDF renders pages.",
)

_PSD_TOOLS = PackageSpec(
    pip_name="psd-tools",
    import_name="psd_tools",
    category="raster-psd",
    extensions=["psd"],
    extract_text=True,
    extract_images=True,
    github_url="https://github.com/psd-tools/psd-tools",
    preferred=True,
    notes="Reads text layers; composites visible layers via Pillow.",
)

_RHINO3DM = PackageSpec(
    pip_name="rhino3dm",
    import_name="rhino3dm",
    category="cad-3dm",
    extensions=["3dm"],
    extract_text=True,
    extract_images=False,
    github_url="https://github.com/mcneel/rhino3dm",
    preferred=True,
    notes="Geometry metadata only; no native rendering. "
          "Rhino 3D files yield geometry metadata and notes.",
)


# Build the registry: ext -> list[PackageSpec]
# Each extension maps to a list; preferred entry comes first.
BUILTIN_REGISTRY: dict = {}


def _register(spec: PackageSpec) -> None:
    """Register a PackageSpec for all its extensions."""
    for ext in spec.extensions:
        ext_clean = ext.lower().lstrip(".")
        if ext_clean not in BUILTIN_REGISTRY:
            BUILTIN_REGISTRY[ext_clean] = []
        # Preferred entries go first
        if spec.preferred:
            BUILTIN_REGISTRY[ext_clean].insert(0, spec)
        else:
            BUILTIN_REGISTRY[ext_clean].append(spec)


_register(_PDFPLUMBER)
_register(_PYPDF2)
_register(_PYTHON_DOCX)
_register(_PYTHON_PPTX)
_register(_OPENPYXL)
_register(_PILLOW)
_register(_PILLOW_HEIF)
_register(_STDLIB_TEXT)
_register(_OLEFILE)
_register(_XLRD)
_register(_EZDXF_DXF)
_register(_EZDXF_DWG)
_register(_PYMUPDF_AI)
_register(_PSD_TOOLS)
_register(_RHINO3DM)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------

def for_extension(ext) -> List[PackageSpec]:
    """Return the list of PackageSpec entries for the given extension.

    Args:
        ext: File extension, with or without leading dot, any case.
             E.g. "pdf", ".PDF", "Docx" all work.

    Returns:
        list[PackageSpec], empty list if extension is unknown or input is None/empty.
    """
    if not ext:
        return []
    try:
        ext_clean = str(ext).lower().lstrip(".")
    except Exception:
        return []
    if not ext_clean:
        return []
    return list(BUILTIN_REGISTRY.get(ext_clean, []))


def default_for(ext) -> Optional[PackageSpec]:
    """Return the preferred PackageSpec for the given extension, or None.

    The preferred spec is the one with preferred=True. If multiple entries
    exist with preferred=True, the first one is returned (registry insertion
    order places preferred specs first).

    Args:
        ext: File extension (same normalization as for_extension).

    Returns:
        PackageSpec with preferred=True, or None if no entry / unknown ext.
    """
    specs = for_extension(ext)
    for spec in specs:
        if spec.preferred:
            return spec
    # If no preferred=True entry, return the first one (single-entry case)
    return specs[0] if specs else None


def is_installed(spec: PackageSpec) -> bool:
    """Check whether a PackageSpec's package is importable in the current environment.

    For stdlib entries (import_name == ""), always returns True.

    Args:
        spec: A PackageSpec instance.

    Returns:
        True if the package is importable, False otherwise.
    """
    if spec.import_name == "":
        return True
    try:
        found = importlib.util.find_spec(spec.import_name)
        return found is not None
    except Exception:
        return False
