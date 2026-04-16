"""End-to-end image processing pipeline for vault-bridge scan commands.

Chain: transport.fetch_to_local → extract_embedded_images (if container)
       → compress_images → vault_binary.write_binary → wiki-embed

Entry point: process_source_for_images()

Python 3.9 compatible.
"""
import logging
import sys
from pathlib import Path
from typing import Callable, Dict, List, Optional

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

import compress_images
import extract_embedded_images
import transport_loader
import vault_binary

logger = logging.getLogger(__name__)

# File types that are containers (need image extraction)
_CONTAINER_TYPES = frozenset({"pdf", "docx", "pptx"})

# File types that are images (skip extraction, compress directly)
_IMAGE_TYPES = frozenset({
    "jpg", "jpeg", "png", "gif", "webp", "bmp", "tiff", "tif",
    "psd", "ai",
})


def process_source_for_images(
    workdir: Path,
    vault_name: str,
    archive_path: str,
    file_type: str,
    event_date: str,
    project_vault_path: str,
    out_tempdir: Path,
    runner: Optional[Callable] = None,
) -> Dict:
    """Full chain: fetch → extract (if container) → compress → vault_binary.write_binary.

    Args:
        workdir: Working directory with .vault-bridge/transport.py.
        vault_name: Obsidian vault name.
        archive_path: The archive-side path (passed to transport.fetch_to_local).
        file_type: File type hint ('jpg', 'pdf', 'docx', etc.).
        event_date: ISO date string (YYYY-MM-DD) for compress_image naming.
        project_vault_path: Vault folder path for the project (e.g. "2408 Project/SD").
        out_tempdir: Temporary directory for intermediate files.
        runner: Optional runner callable for vault_binary (injectable for tests).

    Returns:
        {
            "source_images": List[str],      # archive paths seen
            "compressed_paths": List[Path],  # local compressed JPEGs
            "vault_wiki_embeds": List[str],  # ['![[name1.jpg]]', '![[name2.jpg]]']
            "attachments": List[str],        # same filenames as in wiki_embeds
            "images_embedded": int,          # == len(attachments) on full success
            "warnings": List[str],
            "errors": List[str],
        }
    """
    result: Dict = {
        "source_images": [archive_path],
        "compressed_paths": [],
        "vault_wiki_embeds": [],
        "attachments": [],
        "images_embedded": 0,
        "warnings": [],
        "errors": [],
    }

    ft = file_type.lower().lstrip(".")

    # Step 1: Fetch from archive via transport
    try:
        local_path = transport_loader.fetch_to_local(workdir, archive_path)
    except transport_loader.TransportMissing as exc:
        result["errors"].append(f"transport missing: {exc}")
        return result
    except transport_loader.TransportInvalid as exc:
        result["errors"].append(f"transport invalid: {exc}")
        return result
    except transport_loader.TransportFailed as exc:
        result["errors"].append(f"transport failed: {exc}")
        return result
    except Exception as exc:
        result["errors"].append(f"transport error: {exc}")
        return result

    # Step 2: Extract images (or pass through for direct image types)
    extract_dir = out_tempdir / "extracted"
    extract_dir.mkdir(parents=True, exist_ok=True)

    if ft in _IMAGE_TYPES:
        # Direct image — no extraction needed
        raw_images: List[Path] = [local_path]
    elif ft in _CONTAINER_TYPES:
        raw_images = extract_embedded_images.extract(local_path, extract_dir, ft)
        if not raw_images:
            # Container had no extractable images — not an error
            return result
    else:
        # Unknown type — nothing to embed
        return result

    # Step 3: Compress each image and write to vault
    compress_dir = out_tempdir / "compressed"
    compress_dir.mkdir(parents=True, exist_ok=True)

    for img_path in raw_images:
        # Compress
        try:
            compressed = compress_images.compress_image(img_path, compress_dir, event_date)
        except compress_images.CompressError as exc:
            result["warnings"].append(f"compress failed for {img_path.name}: {exc}")
            continue

        result["compressed_paths"].append(compressed)

        # Write to vault
        vault_dst = f"{project_vault_path}/_Attachments/{compressed.name}"
        write_result = vault_binary.write_binary(
            vault_name=vault_name,
            src_abs_path=compressed,
            vault_dst_path=vault_dst,
            runner=runner,
        )

        if write_result.get("ok"):
            filename = compressed.name
            result["attachments"].append(filename)
            result["vault_wiki_embeds"].append(f"![[{filename}]]")
            result["images_embedded"] += 1
        else:
            err = write_result.get("error", "unknown vault_binary error")
            result["errors"].append(f"vault write failed for {compressed.name}: {err}")

    return result
