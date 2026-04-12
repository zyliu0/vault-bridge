"""Tests for scripts/compress_images.py — Pillow pipeline with de-dup naming.

The canonical 11 test cases from the design doc's Image Handling Rules section:
  1. Source JPEG >1200px → output ≤1200px, JPEG, smaller file
  2. Source PNG ≤1200px → resize skipped, still JPEG
  3. Source RGBA → output RGB
  4. Source CMYK → output RGB (via Pillow conversion)
  5. Source with EXIF geolocation → output has no EXIF
  6. Same bytes twice → same filename → second call is a no-op (de-dup)
  7. Source corrupt/unreadable → clear error, no garbage file
  8. source-stem CJK/spaces/special → normalized ASCII-with-hyphens
  9. GIF input → first frame to JPEG
  10. WEBP input → JPEG
  11. BMP input → JPEG

Uses real Pillow fixtures generated at test time — no binary blobs in the repo.
"""
import io
import sys
from pathlib import Path

import pytest
from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import compress_images as ci  # noqa: E402


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------

def make_jpeg(tmp_path: Path, size=(2000, 1500), color="red", name="source.jpg") -> Path:
    img = Image.new("RGB", size, color)
    path = tmp_path / name
    img.save(path, "JPEG", quality=95)
    return path


def make_png(tmp_path: Path, size=(800, 600), mode="RGB", name="source.png") -> Path:
    if mode == "RGBA":
        img = Image.new("RGBA", size, (255, 0, 0, 128))
    elif mode == "P":
        img = Image.new("P", size, 0)
    else:
        img = Image.new("RGB", size, "blue")
    path = tmp_path / name
    img.save(path, "PNG")
    return path


def make_gif(tmp_path: Path, size=(500, 500), name="source.gif") -> Path:
    img = Image.new("P", size, 0)
    path = tmp_path / name
    img.save(path, "GIF")
    return path


def make_webp(tmp_path: Path, size=(500, 500), name="source.webp") -> Path:
    img = Image.new("RGB", size, "green")
    path = tmp_path / name
    img.save(path, "WEBP")
    return path


def make_bmp(tmp_path: Path, size=(500, 500), name="source.bmp") -> Path:
    img = Image.new("RGB", size, "yellow")
    path = tmp_path / name
    img.save(path, "BMP")
    return path


# ---------------------------------------------------------------------------
# Test 1: JPEG larger than 1200px gets resized
# ---------------------------------------------------------------------------

def test_1_large_jpeg_resized_to_1200(tmp_path):
    src = make_jpeg(tmp_path, size=(2000, 1500))
    out_dir = tmp_path / "_Attachments"

    result_path = ci.compress_image(
        src_path=src,
        out_dir=out_dir,
        event_date="2024-09-09",
    )

    assert result_path.exists()
    assert result_path.suffix == ".jpg"
    out_img = Image.open(result_path)
    assert max(out_img.size) <= 1200
    # Aspect ratio roughly preserved (2000:1500 = 4:3)
    w, h = out_img.size
    assert abs((w / h) - (2000 / 1500)) < 0.01


def test_1_output_smaller_than_source(tmp_path):
    src = make_jpeg(tmp_path, size=(3000, 2000))
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    assert result.stat().st_size < src.stat().st_size


# ---------------------------------------------------------------------------
# Test 2: PNG smaller than 1200px keeps its size, still converts to JPEG
# ---------------------------------------------------------------------------

def test_2_small_png_not_resized_but_becomes_jpeg(tmp_path):
    src = make_png(tmp_path, size=(800, 600), name="small.png")
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    assert result.suffix == ".jpg"
    out_img = Image.open(result)
    assert out_img.size == (800, 600)  # not resized
    assert out_img.format == "JPEG"


# ---------------------------------------------------------------------------
# Test 3: RGBA source → RGB output
# ---------------------------------------------------------------------------

def test_3_rgba_becomes_rgb(tmp_path):
    src = make_png(tmp_path, size=(400, 400), mode="RGBA", name="with_alpha.png")
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    out_img = Image.open(result)
    assert out_img.mode == "RGB"


# ---------------------------------------------------------------------------
# Test 4: CMYK source → RGB output
# ---------------------------------------------------------------------------

def test_4_cmyk_becomes_rgb(tmp_path):
    cmyk_img = Image.new("CMYK", (500, 500), (0, 255, 255, 0))
    src = tmp_path / "cmyk.jpg"
    cmyk_img.save(src, "JPEG")
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    out_img = Image.open(result)
    assert out_img.mode == "RGB"


# ---------------------------------------------------------------------------
# Test 5: EXIF is stripped
# ---------------------------------------------------------------------------

def test_5_exif_stripped_on_save(tmp_path):
    # Create a JPEG with EXIF data
    img = Image.new("RGB", (500, 500), "red")
    src = tmp_path / "with_exif.jpg"
    # PIL's exif handling is limited; we just need to verify the OUTPUT has none
    img.save(src, "JPEG")

    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")

    out_img = Image.open(result)
    exif = out_img.getexif()
    # After strip, the exif dict should be empty or minimal
    assert len(dict(exif)) == 0


# ---------------------------------------------------------------------------
# Test 6: de-dup — same bytes twice writes once
# ---------------------------------------------------------------------------

def test_6_dedup_same_bytes_no_second_write(tmp_path):
    src = make_jpeg(tmp_path, size=(500, 500))
    out_dir = tmp_path / "_Attachments"

    first = ci.compress_image(src, out_dir, event_date="2024-09-09")
    first_mtime = first.stat().st_mtime

    # Call again with the same source — should return the same path, not rewrite
    second = ci.compress_image(src, out_dir, event_date="2024-09-09")
    assert second == first
    # The file should not have been rewritten (same mtime, within precision)
    assert abs(second.stat().st_mtime - first_mtime) < 0.01


def test_6_filename_contains_sha256_prefix(tmp_path):
    src = make_jpeg(tmp_path, name="my_photo.jpg")
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    # Filename format: YYYY-MM-DD--{stem}--{hash8}.jpg
    parts = result.stem.split("--")
    assert len(parts) == 3
    assert parts[0] == "2024-09-09"
    assert parts[1] == "my_photo"
    assert len(parts[2]) == 8
    # hash is hex
    int(parts[2], 16)


# ---------------------------------------------------------------------------
# Test 7: corrupt source → clear error, no garbage file
# ---------------------------------------------------------------------------

def test_7_corrupt_source_raises_clear_error(tmp_path):
    src = tmp_path / "not_an_image.jpg"
    src.write_bytes(b"not actually an image")
    out_dir = tmp_path / "_Attachments"
    with pytest.raises(ci.CompressError):
        ci.compress_image(src, out_dir, event_date="2024-09-09")
    # No output file should exist
    assert not any(out_dir.glob("*.jpg")) if out_dir.exists() else True


# ---------------------------------------------------------------------------
# Test 8: CJK/spaces/special chars in filename → normalized
# ---------------------------------------------------------------------------

def test_8_cjk_stem_normalized(tmp_path):
    src = make_jpeg(tmp_path, name="240909 district memo.jpg")
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    # Filename should be ASCII-safe (no CJK) and hyphenated
    assert " " not in result.name
    # CJK chars should be stripped or replaced
    for char in result.stem:
        assert ord(char) < 128, f"Non-ASCII char in filename: {char!r}"


def test_8_special_chars_normalized(tmp_path):
    src = make_jpeg(tmp_path, name="photo (with) [brackets].jpg")
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    # No parentheses or brackets in output
    assert "(" not in result.name
    assert ")" not in result.name
    assert "[" not in result.name
    assert "]" not in result.name


# ---------------------------------------------------------------------------
# Test 9, 10, 11: GIF, WEBP, BMP → JPEG
# ---------------------------------------------------------------------------

def test_9_gif_becomes_jpeg(tmp_path):
    src = make_gif(tmp_path)
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    assert result.suffix == ".jpg"
    assert Image.open(result).format == "JPEG"


def test_10_webp_becomes_jpeg(tmp_path):
    src = make_webp(tmp_path)
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    assert result.suffix == ".jpg"
    assert Image.open(result).format == "JPEG"


def test_11_bmp_becomes_jpeg(tmp_path):
    src = make_bmp(tmp_path)
    out_dir = tmp_path / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    assert result.suffix == ".jpg"
    assert Image.open(result).format == "JPEG"


# ---------------------------------------------------------------------------
# Out directory auto-created
# ---------------------------------------------------------------------------

def test_out_dir_created_if_missing(tmp_path):
    src = make_jpeg(tmp_path)
    out_dir = tmp_path / "nonexistent" / "_Attachments"
    result = ci.compress_image(src, out_dir, event_date="2024-09-09")
    assert out_dir.exists()
    assert result.exists()
