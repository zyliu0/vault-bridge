"""Per-workdir attachment content-hash index for cross-event dedup.

The naming convention for attachments bakes the `event_date` into the
filename: `YYYY-MM-DD--{stem}--{sha256-prefix-8}.jpg`. That means two
events with byte-identical images still produce DIFFERENT filenames —
the `event_date` prefix differs. Dozens of notes can end up embedding
19 copies of the same client logo (one of the issues in the v14.1.0
field report, F2).

This module fixes that at write time. Before `scan_pipeline` writes a
compressed image to the vault `_Attachments/` folder, it asks the index
if the content hash has been seen before. On a hit, the canonical
filename (first-seen) is returned and the caller embeds that instead,
skipping the redundant vault write.

Index format: a TSV at `<workdir>/.vault-bridge/attachment_hashes.tsv`
with columns `sha256<TAB>filename<TAB>first_seen_iso`. Header line is
optional and ignored on read. Load-once/save-once per scan; callers
either commit the whole thing via `.persist()` or keep changes
in-memory (for dry runs).

The index is workdir-scoped, not vault-wide: each workdir corresponds
to one project in practice, and cross-project attachment sharing is
rare enough that the complexity of a vault-wide index is not worth it.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Dict, Optional


def _index_path(workdir: str) -> Path:
    return Path(workdir) / ".vault-bridge" / "attachment_hashes.tsv"


@dataclass
class AttachmentIndex:
    """sha256 → canonical attachment filename.

    `hits` counts how many times `lookup()` returned an existing entry
    during this session; useful for the memory report.
    """

    mapping: Dict[str, str] = field(default_factory=dict)
    first_seen: Dict[str, str] = field(default_factory=dict)
    hits: int = 0
    dirty: bool = False

    def lookup(self, sha256_hex: str) -> Optional[str]:
        canonical = self.mapping.get(sha256_hex)
        if canonical is not None:
            self.hits += 1
        return canonical

    def record(self, sha256_hex: str, filename: str, today: Optional[str] = None) -> None:
        """Record a new (hash, filename) pair. No-op if already present."""
        if sha256_hex in self.mapping:
            return
        self.mapping[sha256_hex] = filename
        self.first_seen[sha256_hex] = today or date.today().isoformat()
        self.dirty = True

    def persist(self, workdir: str) -> None:
        """Write the index to disk. Safe to call even if not dirty."""
        if not self.dirty and _index_path(workdir).exists():
            return
        path = _index_path(workdir)
        path.parent.mkdir(parents=True, exist_ok=True)
        lines = ["# sha256\tfilename\tfirst_seen"]
        for sha in sorted(self.mapping.keys()):
            lines.append(f"{sha}\t{self.mapping[sha]}\t{self.first_seen.get(sha, '')}")
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        self.dirty = False


def load(workdir: str) -> AttachmentIndex:
    """Load the index from disk. Returns an empty index if missing."""
    idx = AttachmentIndex()
    path = _index_path(workdir)
    if not path.exists():
        return idx
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return idx
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        sha, filename = parts[0], parts[1]
        first_seen = parts[2] if len(parts) > 2 else ""
        idx.mapping[sha] = filename
        idx.first_seen[sha] = first_seen
    return idx


def sha256_of_file(path: Path, chunk_size: int = 65536) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while True:
            buf = f.read(chunk_size)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()
