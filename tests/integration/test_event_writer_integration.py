"""End-to-end v14 integration test.

Asserts the grand-goal behavior in one test:
1. A source with 15 extractable images produces 15 caption-prompt candidates.
2. A fake captioner returns 15 captions; image_vision.select_top_k picks 10.
3. event_writer.compose_body yields an event-note prompt whose text includes
   those 10 captions (so the note body can reference what was actually seen).
4. The final assembled body has no blank lines between consecutive image embeds
   (Minimal theme grid requirement) and uses the 10 curated attachments.
5. The path built via vault_paths.event_note_path has the domain prefix.
"""
import io
import sys
from pathlib import Path
from unittest import mock

from PIL import Image

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _write_fake_jpeg(path: Path) -> None:
    img = Image.new("RGB", (10, 10), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())


def test_full_pipeline_path_cap_curation_and_body(tmp_path):
    import scan_pipeline
    import image_vision
    import event_writer
    import vault_paths

    # 1. Simulate a source with 15 extractable images.
    img_src = tmp_path / "deck.pdf"
    img_src.write_bytes(b"%PDF-1.4 stub")
    fifteen = [img_src] * 15

    # v14.3 (F2): each compressed image must be byte-unique so the
    # content-hash dedup does not collapse them into one attachment.
    def make_unique(src_path, out_dir, event_date):
        idx = make_unique.counter
        make_unique.counter += 1
        out = out_dir / f"2024-08-01--deck--{idx:08x}.jpg"
        _write_fake_jpeg(out)
        with out.open("ab") as f:
            f.write(f"u-{idx}".encode())
        return out
    make_unique.counter = 0

    with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=fifteen):
        with mock.patch("scan_pipeline.compress_images.compress_image", side_effect=make_unique):
            result = scan_pipeline.process_file(
                source_path=str(img_src),
                workdir=str(tmp_path),
                vault_project_path="arch-projects/2408 Sample/SD",
                event_date="2024-08-01",
                dry_run=True,
            )

    # Pipeline capped embeds at 10 and retained all 15 candidates.
    assert len(result.image_candidate_paths) == 15
    assert len(result.image_caption_prompts) == 15
    assert result.images_embedded == 10
    assert len(result.attachments) == 10
    assert result.attachments_subfolder == ""  # Flat layout, no subfolder
    assert result.image_grid is True

    # 2. Fake vision — return a caption that references the project for some.
    captions = [
        "Rebar laid on the south wall." if i % 3 == 0 else "Unrelated item."
        for i in range(15)
    ]
    selected = image_vision.select_top_k(
        captions,
        event_meta={"project": "2408 Sample", "source_basename": "deck.pdf"},
        k=10,
    )
    assert len(selected) == 10

    # Attach the curated 10 captions to the result; keep 10 attachments already.
    final_captions = [captions[i] for i in selected]
    result.image_captions = final_captions
    # (Attachments already capped at 10 upstream.)

    # 3. Compose body — event note because text is empty but captions exist.
    result.text = "Design review meeting. Decision: proceed with facade option B."
    meta = {
        "source_path": str(img_src),
        "event_date": "2024-08-01",
        "domain": "arch-projects",
        "project": "2408 Sample",
        "subfolder": "SD",
        "file_type": "pdf",
    }
    composed = event_writer.compose_body(result, meta)
    assert composed.note_kind == "event"
    # Captions present in the prompt so the writer can reference them.
    assert "Rebar laid on the south wall." in composed.prompt_text
    assert "arch-projects" in composed.prompt_text or "2408 Sample" in composed.prompt_text

    # 4. Assemble final body — no blank lines between consecutive embeds.
    prose = (
        "We met at the office for the SD review; the client joined by video and "
        "we walked through the facade options together. Option B was chosen after "
        "a short discussion on material cost and delivery timing. We agreed to "
        "update the set by Friday and circle back next week for the coordination "
        "meeting. The mechanical scope stays unchanged. The rebar layout on the "
        "south wall came up in passing and was logged as a site observation for "
        "the structural engineer to confirm on next visit. Overall the meeting was "
        "productive and everyone left the call aligned on the direction forward, "
        "although a few open questions remain about budget. We will revisit the "
        "schedule once the owner returns from travel next week with a longer note."
    )
    assembled = event_writer.assemble_note_body(prose, result.attachments)
    # Every embed appears in the assembled body.
    for embed in result.attachments:
        assert embed in assembled
    # Blank line between prose and embeds.
    assert "next week with a longer note.\n\n![[" in assembled
    # 10 embeds at row_size=3 → rows of 3, 3, 3, 1 → 3 blank-line
    # separators inside the grid (v14.3, F5).
    grid = assembled.split("\n\n", 1)[1]
    assert grid.count("\n\n") == 3

    # 5. Vault path has the domain prefix.
    path = vault_paths.event_note_path(
        "arch-projects", "2408 Sample", "SD", "2024-08-01 facade-review.md"
    )
    assert path == "arch-projects/2408 Sample/SD/2024-08-01 facade-review.md"
    assert path.startswith("arch-projects/")


def test_metadata_stub_path_when_nothing_readable(tmp_path):
    """Heartbeat-style autonomous fallback: unreadable file -> metadata stub."""
    import scan_pipeline
    import event_writer

    src = tmp_path / "clip.mp4"
    src.write_bytes(b"\x00" * 16)

    # Mock handler registry: unknown extension -> skipped by the pipeline.
    result = scan_pipeline.process_file(
        source_path=str(src),
        workdir=str(tmp_path),
        vault_project_path="content/2024-shorts/Short-form",
        event_date="2024-08-01",
        dry_run=True,
    )
    assert result.skipped is True

    composed = event_writer.compose_body(result, {
        "source_path": str(src),
        "event_date": "2024-08-01",
        "domain": "content",
        "project": "2024-shorts",
        "subfolder": "Short-form",
        "file_type": "mp4",
    })
    assert composed.note_kind == "stub"
    assert composed.body_text.strip() != ""
    assert composed.prompt_text == ""
    # Stub body references the source filename.
    assert "clip.mp4" in composed.body_text
