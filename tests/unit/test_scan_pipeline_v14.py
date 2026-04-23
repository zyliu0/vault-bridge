"""v14 behavior for scan_pipeline:
- IMAGE_CANDIDATE_CAP = 20: never compress more than 20 candidate images per event.
- IMAGE_EMBED_CAP = 10: never embed more than 10 images per event.
- No per-event attachments subfolder — attachments land flat in _Attachments/.
- ScanResult carries candidate paths + caption prompts for the command spec to run vision over.
"""
import sys
import time
from pathlib import Path
from unittest import mock

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))


def _write_fake_jpeg(path: Path) -> None:
    import io
    from PIL import Image
    img = Image.new("RGB", (10, 10), color=(100, 100, 100))
    buf = io.BytesIO()
    img.save(buf, "JPEG")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(buf.getvalue())


class TestImageCaps:
    def test_candidate_cap_limits_compression(self, tmp_path):
        """25 raw images → only first 20 compressed (IMAGE_CANDIDATE_CAP)."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)
        compressed = tmp_path / "2026-04-19--photo--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        raw = [img] * 25
        compress_calls = []

        def fake_compress(src, out_dir, date):
            compress_calls.append(src)
            return compressed

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=raw):
            with mock.patch("scan_pipeline.compress_images.compress_image", side_effect=fake_compress):
                scan_pipeline.process_file(
                    source_path=str(img),
                    workdir=str(tmp_path),
                    vault_project_path="arch-projects/2408 Sample/SD",
                    event_date="2026-04-19",
                    dry_run=True,
                )
        assert len(compress_calls) == scan_pipeline.IMAGE_CANDIDATE_CAP == 20

    def test_embed_cap_limits_attachments(self, tmp_path):
        """15 candidates compressed → only 10 embedded (IMAGE_EMBED_CAP)."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        # v14.3 (F2): byte-unique compressed files so the content-hash dedup
        # does not collapse the 15 "identical" mocks into one attachment.
        def make_unique(src_path, out_dir, event_date):
            idx = make_unique.counter
            make_unique.counter += 1
            out = out_dir / f"2026-04-19--photo--{idx:08x}.jpg"
            _write_fake_jpeg(out)
            with out.open("ab") as f:
                f.write(f"unique-{idx}".encode())
            return out
        make_unique.counter = 0

        raw = [img] * 15
        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=raw):
            with mock.patch("scan_pipeline.compress_images.compress_image", side_effect=make_unique):
                with mock.patch("scan_pipeline.vault_binary.write_binary", return_value={"ok": True}):
                    result = scan_pipeline.process_file(
                        source_path=str(img),
                        workdir=str(tmp_path),
                        vault_project_path="arch-projects/2408 Sample/SD",
                        event_date="2026-04-19",
                        vault_name="V",
                        dry_run=False,
                    )
        assert result.images_embedded == scan_pipeline.IMAGE_EMBED_CAP == 10
        assert len(result.attachments) == 10

    def test_no_subfolder_even_with_many_candidates(self, tmp_path):
        """Dropped: per-event _Attachments/{date}--{slug}/ subfolder. Always flat."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)
        compressed = tmp_path / "2026-04-19--photo--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        raw = [img] * 15
        captured = []
        def fake_write(vault_name, src_abs_path, vault_dst_path):
            captured.append(vault_dst_path)
            return {"ok": True}

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=raw):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                with mock.patch("scan_pipeline.vault_binary.write_binary", side_effect=fake_write):
                    result = scan_pipeline.process_file(
                        source_path=str(img),
                        workdir=str(tmp_path),
                        vault_project_path="arch-projects/2408 Sample/SD",
                        event_date="2026-04-19",
                        vault_name="V",
                        dry_run=False,
                    )
        for dst in captured:
            # No `--slug/` between `_Attachments/` and the jpeg filename.
            assert "_Attachments/2026-04-19--" not in dst or dst.rsplit("/", 1)[1].startswith("2026-04-19--")
            # Simplest: every dst ends with `.jpg` and has `/_Attachments/` as direct parent.
            assert "/_Attachments/" in dst


class TestScanResultNewFields:
    def test_has_candidate_paths_field(self, tmp_path):
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)
        compressed = tmp_path / "2026-04-19--photo--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img, img, img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                result = scan_pipeline.process_file(
                    source_path=str(img),
                    workdir=str(tmp_path),
                    vault_project_path="arch-projects/2408 Sample/SD",
                    event_date="2026-04-19",
                    dry_run=True,
                )
        # Newfields present, list of strings (absolute/relative paths).
        assert hasattr(result, "image_candidate_paths")
        assert isinstance(result.image_candidate_paths, list)
        assert len(result.image_candidate_paths) == 3
        assert all(isinstance(p, str) for p in result.image_candidate_paths)

    def test_caption_prompts_field_empty_post_v16(self, tmp_path):
        """v16.0.0: the caption side-channel is gone. The field is
        retained as an always-empty list for back-compat with callers
        that still assign to it; the pipeline never populates it."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)
        compressed = tmp_path / "2026-04-19--photo--abc123ab.jpg"
        _write_fake_jpeg(compressed)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img, img]):
            with mock.patch("scan_pipeline.compress_images.compress_image", return_value=compressed):
                result = scan_pipeline.process_file(
                    source_path=str(img),
                    workdir=str(tmp_path),
                    vault_project_path="arch-projects/2408 Sample/SD",
                    event_date="2026-04-19",
                    dry_run=True,
                )
        assert hasattr(result, "image_caption_prompts")
        assert result.image_caption_prompts == []

    def test_has_image_captions_field_default_empty(self, tmp_path):
        """image_captions defaults to [] until the command spec fills it via vision."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)
        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[]):
            result = scan_pipeline.process_file(
                source_path=str(img),
                workdir=str(tmp_path),
                vault_project_path="arch-projects/2408 Sample/SD",
                event_date="2026-04-19",
                dry_run=True,
            )
        assert hasattr(result, "image_captions")
        assert result.image_captions == []


class TestCandidatePathLifetime:
    """v14.7.1: compressed candidate JPEGs must survive past process_file.

    Prior to this fix, `_stage_extract_images` wrapped the image sub-pipeline
    in `tempfile.TemporaryDirectory()`, so every path in
    `ScanResult.image_candidate_paths` was dangling by the time the
    retro-scan loop called `vision_runner.run_captions` on them.
    """

    def _compress_writes_real_bytes(self, tmp_path):
        """Return a compress side-effect that writes unique files under out_dir."""
        counter = {"n": 0}

        def _impl(src_path, out_dir, event_date):
            counter["n"] += 1
            idx = counter["n"]
            out = Path(out_dir) / f"{event_date}--test--{idx:08x}.jpg"
            _write_fake_jpeg(out)
            # Pad to > IMAGE_MIN_BYTES (10 kB) so size-gate does not drop.
            with out.open("ab") as f:
                f.write(b"x" * 11_000)
            return out

        return _impl

    def test_candidate_paths_exist_after_process_file(self, tmp_path):
        """Every returned candidate path must be readable after process_file returns."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img, img, img]):
            with mock.patch(
                "scan_pipeline.compress_images.compress_image",
                side_effect=self._compress_writes_real_bytes(tmp_path),
            ):
                with mock.patch("scan_pipeline.vault_binary.write_binary", return_value={"ok": True}):
                    result = scan_pipeline.process_file(
                        source_path=str(img),
                        workdir=str(tmp_path),
                        vault_project_path="arch-projects/2408 Sample/SD",
                        event_date="2026-04-19",
                        vault_name="V",
                        dry_run=False,
                    )

        assert len(result.image_candidate_paths) == 3
        for path in result.image_candidate_paths:
            assert Path(path).exists(), (
                f"candidate path {path} must survive process_file — "
                "scan pipeline tempdir was torn down prematurely (v14.7.1 regression)"
            )
            assert Path(path).stat().st_size > 0

    def test_candidate_paths_land_under_scan_tmp_root(self, tmp_path):
        """Candidates live under <workdir>/.vault-bridge/tmp/, not system /tmp."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch(
                "scan_pipeline.compress_images.compress_image",
                side_effect=self._compress_writes_real_bytes(tmp_path),
            ):
                result = scan_pipeline.process_file(
                    source_path=str(img),
                    workdir=str(tmp_path),
                    vault_project_path="arch-projects/2408 Sample/SD",
                    event_date="2026-04-19",
                    dry_run=True,
                )

        scan_tmp = tmp_path / ".vault-bridge" / "tmp"
        assert scan_tmp.exists()
        for path in result.image_candidate_paths:
            assert str(scan_tmp) in path, (
                f"candidate path {path} must land under {scan_tmp}, "
                "not a random system tempdir"
            )

    def test_cleanup_scan_tmp_removes_extract_dirs(self, tmp_path):
        """cleanup_scan_tmp sweeps extract_* dirs regardless of age when max_age is None."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch(
                "scan_pipeline.compress_images.compress_image",
                side_effect=self._compress_writes_real_bytes(tmp_path),
            ):
                scan_pipeline.process_file(
                    source_path=str(img),
                    workdir=str(tmp_path),
                    vault_project_path="arch-projects/2408 Sample/SD",
                    event_date="2026-04-19",
                    dry_run=True,
                )

        scan_tmp = tmp_path / ".vault-bridge" / "tmp"
        extract_dirs_before = [p for p in scan_tmp.iterdir() if p.name.startswith("extract_")]
        assert len(extract_dirs_before) >= 1

        removed = scan_pipeline.cleanup_scan_tmp(str(tmp_path))
        assert removed >= 1
        extract_dirs_after = [p for p in scan_tmp.iterdir() if p.name.startswith("extract_")]
        assert extract_dirs_after == []

    def test_cleanup_scan_tmp_respects_max_age(self, tmp_path):
        """Fresh tmps survive cleanup when max_age_seconds is set high."""
        import scan_pipeline
        img = tmp_path / "photo.jpg"
        _write_fake_jpeg(img)

        with mock.patch("scan_pipeline.file_type_handlers.extract_images", return_value=[img]):
            with mock.patch(
                "scan_pipeline.compress_images.compress_image",
                side_effect=self._compress_writes_real_bytes(tmp_path),
            ):
                scan_pipeline.process_file(
                    source_path=str(img),
                    workdir=str(tmp_path),
                    vault_project_path="arch-projects/2408 Sample/SD",
                    event_date="2026-04-19",
                    dry_run=True,
                )

        # 24h max_age: the dir we just created is far younger, so survives.
        removed = scan_pipeline.cleanup_scan_tmp(str(tmp_path), max_age_seconds=86400)
        assert removed == 0
        scan_tmp = tmp_path / ".vault-bridge" / "tmp"
        assert any(p.name.startswith("extract_") for p in scan_tmp.iterdir())

    def test_process_batch_sweeps_stale_tmp(self, tmp_path):
        """process_batch removes extract_* dirs older than the max age at entry."""
        import scan_pipeline
        scan_tmp = tmp_path / ".vault-bridge" / "tmp"
        scan_tmp.mkdir(parents=True)
        stale = scan_tmp / "extract_oldrun"
        stale.mkdir()
        # Backdate the stale dir to be well beyond the max-age threshold.
        old_ts = time.time() - (scan_pipeline._SCAN_TMP_MAX_AGE_SECS + 3600)
        import os as _os
        _os.utime(stale, (old_ts, old_ts))

        scan_pipeline.process_batch(
            source_paths=[],
            workdir=str(tmp_path),
            vault_project_path="arch-projects/X/SD",
            event_date="2026-04-19",
            dry_run=True,
        )
        assert not stale.exists()
