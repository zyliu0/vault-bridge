"""Extract embedded images from container documents.

Supports:
  - PDF: uses PyPDF2 page.images (3.x API) with manual /XObject traversal fallback
  - DOCX: python-docx related_parts with image/* content_type
  - PPTX: python-pptx slide shapes with MSO_SHAPE_TYPE.PICTURE
  - XLSX: stub — returns []
  - Image types (jpg/png/gif/webp/bmp/etc.): returns [src_path] unchanged
  - Anything else: returns []

Never raises — returns [] on any internal failure.

Python 3.9 compatible.
"""
import logging
from pathlib import Path
from typing import List

logger = logging.getLogger(__name__)

# File types considered direct images (no container extraction needed)
_IMAGE_TYPES = frozenset({
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "tif",
    "heic", "heif", "psd", "ai", "svg",
})

# Container types we can extract from
_CONTAINER_TYPES = frozenset({"pdf", "docx", "pptx"})


def extract(src_path: Path, out_dir: Path, file_type: str) -> List[Path]:
    """Extract embedded images from src_path into out_dir.

    Args:
        src_path: Path to the source document.
        out_dir: Directory to write extracted images into (created if needed).
        file_type: Lowercase file type hint: 'pdf', 'docx', 'pptx', 'xlsx',
                   or any image type ('jpg', 'png', etc.).

    Returns:
        List of Paths to extracted/returned image files.
        For image types: [src_path] (unchanged, no extraction).
        For container types: list of written image paths (may be empty).
        For xlsx or unknown: [].

    Never raises — returns [] on any internal failure.
    """
    ft = file_type.lower().lstrip(".")

    # Image passthrough — no extraction needed
    if ft in _IMAGE_TYPES:
        return [src_path]

    if ft == "xlsx":
        return []

    if ft not in _CONTAINER_TYPES:
        return []

    # Ensure output directory exists
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except Exception as exc:
        logger.warning("extract: could not create out_dir %s: %s", out_dir, exc)
        return []

    if ft == "pdf":
        return _extract_pdf(src_path, out_dir)
    elif ft == "docx":
        return _extract_docx(src_path, out_dir)
    elif ft == "pptx":
        return _extract_pptx(src_path, out_dir)

    return []


def _sniff_extension(data: bytes) -> str:
    """Return a file extension based on image magic bytes."""
    if data[:2] == b"\xff\xd8":
        return ".jpg"
    if data[:4] == b"\x89PNG":
        return ".png"
    if data[:4] == b"GIF8":
        return ".gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    return ".bin"


def _unique_path(out_dir: Path, stem: str, ext: str) -> Path:
    """Return a unique path in out_dir, incrementing suffix on collision."""
    candidate = out_dir / f"{stem}{ext}"
    if not candidate.exists():
        return candidate
    counter = 1
    while True:
        candidate = out_dir / f"{stem}-{counter}{ext}"
        if not candidate.exists():
            return candidate
        counter += 1


def _extract_pdf(src_path: Path, out_dir: Path) -> List[Path]:
    """Extract images from a PDF file using PyPDF2."""
    try:
        import PyPDF2
    except ImportError:
        logger.warning("extract_pdf: PyPDF2 not available")
        return []

    results: List[Path] = []
    src_stem = src_path.stem

    try:
        reader = PyPDF2.PdfReader(str(src_path))
    except Exception as exc:
        logger.warning("extract_pdf: could not open %s: %s", src_path, exc)
        return []

    for page_idx, page in enumerate(reader.pages):
        # Try the 3.x page.images API first
        try:
            page_images = list(page.images)
        except Exception:
            page_images = []

        if page_images:
            for img_idx, img_file in enumerate(page_images):
                try:
                    data = img_file.data
                    ext = _sniff_extension(data)
                    stem = f"{src_stem}--page{page_idx}--img{img_idx}"
                    out_path = _unique_path(out_dir, stem, ext)
                    out_path.write_bytes(data)
                    results.append(out_path)
                except Exception as exc:
                    logger.warning("extract_pdf: error writing image: %s", exc)
            continue

        # Fallback: manual /XObject /Subtype /Image traversal
        try:
            resources = page.get("/Resources")
            if resources is None:
                continue
            xobjects = resources.get("/XObject")
            if xobjects is None:
                continue
            # Resolve indirect reference if needed
            if hasattr(xobjects, "get_object"):
                xobjects = xobjects.get_object()
            for name in list(xobjects.keys()):
                try:
                    obj = xobjects[name]
                    if hasattr(obj, "get_object"):
                        obj = obj.get_object()
                    if not hasattr(obj, "get"):
                        continue
                    subtype = obj.get("/Subtype")
                    if str(subtype) != "/Image":
                        continue
                    data = obj.get_data()
                    ext = _sniff_extension(data)
                    stem = f"{src_stem}--page{page_idx}--xobj{name.lstrip('/')}"
                    out_path = _unique_path(out_dir, stem, ext)
                    out_path.write_bytes(data)
                    results.append(out_path)
                except Exception as exc:
                    logger.warning("extract_pdf: xobject error: %s", exc)
        except Exception as exc:
            logger.warning("extract_pdf: page %d traversal error: %s", page_idx, exc)

    return results


def _extract_docx(src_path: Path, out_dir: Path) -> List[Path]:
    """Extract embedded images from a DOCX file using python-docx."""
    try:
        from docx import Document
    except ImportError:
        logger.warning("extract_docx: python-docx not available")
        return []

    results: List[Path] = []
    src_stem = src_path.stem

    try:
        doc = Document(str(src_path))
    except Exception as exc:
        logger.warning("extract_docx: could not open %s: %s", src_path, exc)
        return []

    idx = 0
    try:
        for rel_id, part in doc.part.related_parts.items():
            content_type = getattr(part, "content_type", "") or ""
            if not content_type.startswith("image/"):
                continue
            try:
                data = part.blob
                # Determine extension from content_type or magic bytes
                if content_type == "image/jpeg":
                    ext = ".jpg"
                elif content_type == "image/png":
                    ext = ".png"
                elif content_type == "image/gif":
                    ext = ".gif"
                elif content_type == "image/webp":
                    ext = ".webp"
                else:
                    ext = _sniff_extension(data)
                stem = f"{src_stem}--embedded-{idx}"
                out_path = _unique_path(out_dir, stem, ext)
                out_path.write_bytes(data)
                results.append(out_path)
                idx += 1
            except Exception as exc:
                logger.warning("extract_docx: error writing part %s: %s", rel_id, exc)
    except Exception as exc:
        logger.warning("extract_docx: error iterating related_parts: %s", exc)

    return results


def _extract_pptx(src_path: Path, out_dir: Path) -> List[Path]:
    """Extract embedded images from a PPTX file using python-pptx."""
    try:
        from pptx import Presentation
        from pptx.enum.shapes import MSO_SHAPE_TYPE
    except ImportError:
        logger.warning("extract_pptx: python-pptx not available")
        return []

    results: List[Path] = []
    src_stem = src_path.stem

    try:
        prs = Presentation(str(src_path))
    except Exception as exc:
        logger.warning("extract_pptx: could not open %s: %s", src_path, exc)
        return []

    idx = 0
    for slide_idx, slide in enumerate(prs.slides):
        for shape in slide.shapes:
            try:
                if shape.shape_type != MSO_SHAPE_TYPE.PICTURE:
                    continue
                data = shape.image.blob
                ext = _sniff_extension(data)
                stem = f"{src_stem}--slide{slide_idx}--img{idx}"
                out_path = _unique_path(out_dir, stem, ext)
                out_path.write_bytes(data)
                results.append(out_path)
                idx += 1
            except Exception as exc:
                logger.warning(
                    "extract_pptx: error extracting shape %s slide %d: %s",
                    getattr(shape, "name", "?"), slide_idx, exc
                )

    return results
