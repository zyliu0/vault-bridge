#!/usr/bin/env python3
"""Compress a source image to vault-bridge's _Attachments/ directory.

Called by retro-scan and heartbeat-scan for every image that needs to be
embedded in a vault note. Handles:
  - Resize to max 1200px longest side (EXIF orientation respected)
  - Format conversion to JPEG (from PNG, GIF, WEBP, BMP, CMYK, RGBA, P)
  - EXIF metadata stripped (privacy — no geolocation / camera serials)
  - De-duplication via content-hash prefix in the filename
  - CJK/special-char normalization in the filename stem

Naming: [Project]/_Attachments/YYYY-MM-DD--{source-stem}--{sha256-prefix-8}.jpg

Same source bytes → same filename → second call is a no-op.
"""
import hashlib
import re
import unicodedata
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps

# Pinned compression parameters
MAX_DIMENSION = 1200
JPEG_QUALITY = 82
HASH_PREFIX_LEN = 8


class CompressError(Exception):
    """Raised when a source image cannot be read or processed."""
    pass


def compress_image(
    src_path: Path,
    out_dir: Path,
    event_date: str,
) -> Path:
    """Compress src_path and save the result to out_dir.

    Args:
        src_path: Path to the source image file.
        out_dir: Path to the _Attachments directory. Created if missing.
        event_date: ISO date string (YYYY-MM-DD) used as the filename prefix.

    Returns:
        Path to the written JPEG. If the same bytes have already been written
        (same hash prefix), returns the existing path without rewriting.

    Raises:
        CompressError: if the source cannot be read as an image.
    """
    src_path = Path(src_path)
    out_dir = Path(out_dir)

    if not src_path.exists():
        raise CompressError(f"Source image does not exist: {src_path}")

    # Compute the content hash from source bytes for de-dup keying.
    try:
        src_bytes = src_path.read_bytes()
    except OSError as e:
        raise CompressError(f"Could not read {src_path}: {e}")
    hash_prefix = hashlib.sha256(src_bytes).hexdigest()[:HASH_PREFIX_LEN]

    # Build the target filename.
    source_stem = _normalize_stem(src_path.stem)
    target_name = f"{event_date}--{source_stem}--{hash_prefix}.jpg"
    out_dir.mkdir(parents=True, exist_ok=True)
    target_path = out_dir / target_name

    # De-dup: if the target already exists, return it without rewriting.
    if target_path.exists():
        return target_path

    # Open the source image via Pillow.
    try:
        img = Image.open(src_path)
        img.load()
    except (OSError, Image.UnidentifiedImageError) as e:
        raise CompressError(f"Could not open {src_path} as an image: {e}")

    # Apply EXIF orientation transform BEFORE we strip metadata.
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:
        # Some images have no EXIF or corrupt EXIF — that's fine, skip
        pass

    # Convert color space to RGB (handles RGBA, CMYK, P, LA, 1, etc.)
    if img.mode != "RGB":
        if img.mode in ("RGBA", "LA"):
            # Composite against white so transparent pixels become white
            background = Image.new("RGB", img.size, (255, 255, 255))
            mask = img.split()[-1] if img.mode == "RGBA" else img.split()[1]
            background.paste(img.convert("RGB"), mask=mask)
            img = background
        else:
            img = img.convert("RGB")

    # Resize if the longest side exceeds MAX_DIMENSION.
    if max(img.size) > MAX_DIMENSION:
        img.thumbnail((MAX_DIMENSION, MAX_DIMENSION), Image.LANCZOS)

    # Save as JPEG with no EXIF. Pillow's default behavior strips EXIF unless
    # we explicitly pass it, which we don't. Also strip via empty exif=b"".
    img.save(
        target_path,
        "JPEG",
        quality=JPEG_QUALITY,
        optimize=True,
        progressive=True,
        exif=b"",
    )

    return target_path


def _normalize_stem(stem: str) -> str:
    """Normalize a source filename stem to ASCII-with-hyphens.

    - Strip CJK and other non-ASCII characters (using NFKD, then ASCII-only)
    - Replace whitespace, parentheses, brackets, and other separators with hyphens
    - Collapse runs of hyphens
    - Strip leading/trailing hyphens
    - Return "image" as fallback if the normalized result is empty
    """
    # NFKD decomposition drops accents to separate combining chars, which
    # ASCII encoding can then strip. CJK characters are dropped entirely.
    decomposed = unicodedata.normalize("NFKD", stem)
    ascii_only = decomposed.encode("ascii", "ignore").decode("ascii")

    # Replace separators and special chars with hyphens.
    normalized = re.sub(r"[\s\(\)\[\]\{\}<>:;,!?\"'`]+", "-", ascii_only)
    # Collapse multiple hyphens.
    normalized = re.sub(r"-+", "-", normalized)
    # Strip leading/trailing hyphens.
    normalized = normalized.strip("-")

    return normalized or "image"


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 4:
        sys.stderr.write(
            "usage: compress_images.py <src> <out-dir> <event-date>\n"
        )
        sys.exit(2)
    try:
        result = compress_image(Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3])
        print(result)
    except CompressError as e:
        sys.stderr.write(f"compress-images: {e}\n")
        sys.exit(2)
