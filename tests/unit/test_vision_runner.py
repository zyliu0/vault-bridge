"""Tests for scripts/vision_runner.py (field-review v14.4.1, Issue 2)."""
import io
import json
import sys
from pathlib import Path
from unittest import mock

import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
sys.path.insert(0, str(SCRIPTS))

import vision_runner  # noqa: E402


@pytest.fixture
def fake_image(tmp_path):
    p = tmp_path / "img.jpg"
    p.write_bytes(b"\xff\xd8\xff\xe0fake jpeg\xff\xd9")
    return str(p)


def _fake_prompt(path, meta):
    return f"PROMPT for {path} in project {meta.get('project', '-')}"


class TestStubBackend:
    def test_returns_empty_strings(self, fake_image):
        captions, warnings = vision_runner.run_captions(
            [fake_image], {"project": "P"},
            backend="stub", prompt_builder=_fake_prompt,
        )
        assert captions == [""]
        # v14.7.1: stub backend always yields empty captions, so the
        # run surfaces a memory-report warning ("1/1 came back empty")
        # instead of silently claiming success.
        assert any("came back empty" in w for w in warnings)

    def test_aligns_with_input_length(self, tmp_path):
        paths = []
        for i in range(5):
            p = tmp_path / f"i{i}.jpg"
            p.write_bytes(b"\xff\xd8fake\xff\xd9")
            paths.append(str(p))
        captions, warnings = vision_runner.run_captions(
            paths, {}, backend="stub", prompt_builder=_fake_prompt,
        )
        assert captions == ["", "", "", "", ""]
        assert any("5/5" in w and "came back empty" in w for w in warnings)


class TestMissingImage:
    def test_partial_missing_image_gets_empty_caption_and_warning(self, fake_image):
        """One real image + one missing → captions aligned, per-image warning."""
        captions, warnings = vision_runner.run_captions(
            [fake_image, "/nonexistent/does-not-exist.jpg"],
            {}, backend="stub", prompt_builder=_fake_prompt,
        )
        assert captions == ["", ""]
        assert any("missing" in w.lower() for w in warnings)

    def test_all_missing_paths_raises(self):
        """v14.7.1: all-missing means the scan pipeline ate its tempdir — raise loudly."""
        with pytest.raises(FileNotFoundError, match="all 1 image paths are missing"):
            vision_runner.run_captions(
                ["/nonexistent/does-not-exist.jpg"],
                {}, backend="stub", prompt_builder=_fake_prompt,
            )

    def test_all_missing_multiple_paths_raises(self):
        with pytest.raises(FileNotFoundError, match="all 3 image paths are missing"):
            vision_runner.run_captions(
                ["/a.jpg", "/b.jpg", "/c.jpg"],
                {}, backend="stub", prompt_builder=_fake_prompt,
            )

    def test_empty_paths_does_not_raise(self):
        """Empty input is fine — nothing to caption."""
        captions, warnings = vision_runner.run_captions(
            [], {}, backend="stub", prompt_builder=_fake_prompt,
        )
        assert captions == []
        assert warnings == []


class TestCustomBackend:
    def test_custom_backend_receives_path_prompt_model(self, fake_image, monkeypatch):
        calls = []

        def fake_runner(path, prompt, model):
            calls.append((path, prompt, model))
            return "  here's the caption: a red cube  "

        monkeypatch.setattr(
            vision_runner, "_BACKEND_REGISTRY",
            {**vision_runner._BACKEND_REGISTRY, "test": fake_runner},
        )
        captions, _ = vision_runner.run_captions(
            [fake_image], {"project": "Proj"},
            backend="test", prompt_builder=_fake_prompt,
        )
        assert len(calls) == 1
        assert calls[0][0] == fake_image
        assert "Proj" in calls[0][1]
        # Cleaning: preamble stripped, whitespace collapsed.
        assert captions == ["a red cube"]

    def test_backend_raise_produces_warning(self, fake_image, monkeypatch):
        def broken(path, prompt, model):
            raise RuntimeError("network bad")

        monkeypatch.setattr(
            vision_runner, "_BACKEND_REGISTRY",
            {**vision_runner._BACKEND_REGISTRY, "broken": broken},
        )
        captions, warnings = vision_runner.run_captions(
            [fake_image], {}, backend="broken", prompt_builder=_fake_prompt,
        )
        assert captions == [""]
        assert any("failed" in w.lower() and "network bad" in w for w in warnings)


class TestRefusalDetection:
    """v14.7.4 red-line: backend responses that look like permission
    refusals must raise, not leak into `image_captions`.
    """

    def test_is_refusal_caption_matches_known_patterns(self):
        assert vision_runner.is_refusal_caption(
            "I need permission to read the image file."
        )
        assert vision_runner.is_refusal_caption(
            "I need your permission to read the image file. "
            "A permission prompt should appear."
        )
        assert vision_runner.is_refusal_caption(
            "Please approve the file read when prompted."
        )

    def test_is_refusal_caption_passes_real_captions(self):
        assert not vision_runner.is_refusal_caption(
            "Four painted canvas sample swatches arranged with Chinese labels."
        )
        assert not vision_runner.is_refusal_caption("")
        assert not vision_runner.is_refusal_caption("   ")

    def test_per_image_refusal_raises(self, fake_image, monkeypatch):
        """A backend returning refusal text must make run_captions raise,
        not silently record an empty caption and a warning."""
        def refusing(path, prompt, model):
            return "I need permission to read the image file. Please approve."

        monkeypatch.setattr(
            vision_runner, "_BACKEND_REGISTRY",
            {**vision_runner._BACKEND_REGISTRY, "refusing": refusing},
        )
        with pytest.raises(RuntimeError, match="permission-refusal"):
            vision_runner.run_captions(
                [fake_image], {}, backend="refusing",
                prompt_builder=_fake_prompt, batch=False,
            )


class TestBatchedClaudeCli:
    """Option D (v14.7.4): `claude_cli` with >1 image uses one subprocess call."""

    @pytest.fixture
    def three_images(self, tmp_path):
        paths = []
        for i in range(3):
            p = tmp_path / f"img_{i+1:02d}.jpg"
            p.write_bytes(b"\xff\xd8\xff\xe0fake\xff\xd9")
            paths.append(str(p))
        return paths

    def test_batched_path_makes_single_subprocess_call(self, three_images, monkeypatch):
        calls = []

        def fake_run(cmd, capture_output, text, timeout):
            calls.append(cmd)

            class _R:
                returncode = 0
                stdout = (
                    "img_01.jpg: Four painted canvas sample swatches arranged "
                    "with Chinese labels.\n"
                    "img_02.jpg: Illuminated white textured panel mounted on "
                    "wall with wiring.\n"
                    "img_03.jpg: Construction site with two workers on green "
                    "protective flooring.\n"
                )
                stderr = ""

            return _R()

        monkeypatch.setattr(vision_runner.subprocess, "run", fake_run)
        monkeypatch.setattr(vision_runner.shutil, "which",
                            lambda cmd: "/usr/local/bin/claude")
        captions, warnings = vision_runner.run_captions(
            three_images, {"project": "P"},
            backend="claude_cli", prompt_builder=_fake_prompt,
        )
        assert len(calls) == 1, "batched path must use ONE subprocess call"
        assert "--dangerously-skip-permissions" in calls[0]
        assert all("painted" in captions[0] for _ in [0])
        assert "wiring" in captions[1]
        assert "workers" in captions[2]

    def test_batched_refusal_raises(self, three_images, monkeypatch):
        """A whole-body refusal must raise, not record empty captions."""
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 0
                stdout = (
                    "I need permission to read the image files. "
                    "Please approve the file read when prompted."
                )
                stderr = ""

            return _R()

        monkeypatch.setattr(vision_runner.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="permission-refusal"):
            vision_runner.run_captions(
                three_images, {}, backend="claude_cli",
                prompt_builder=_fake_prompt,
            )

    def test_batched_empty_output_raises(self, three_images, monkeypatch):
        """No parseable `<basename>: <caption>` lines must raise —
        never fall through to empty captions."""
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 0
                stdout = "sure, here are the images"
                stderr = ""

            return _R()

        monkeypatch.setattr(vision_runner.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="no parseable"):
            vision_runner.run_captions(
                three_images, {}, backend="claude_cli",
                prompt_builder=_fake_prompt,
            )

    def test_batched_nonzero_exit_raises(self, three_images, monkeypatch):
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 1
                stdout = ""
                stderr = "claude: bad args"

            return _R()

        monkeypatch.setattr(vision_runner.subprocess, "run", fake_run)
        with pytest.raises(RuntimeError, match="non-zero exit"):
            vision_runner.run_captions(
                three_images, {}, backend="claude_cli",
                prompt_builder=_fake_prompt,
            )

    def test_batched_partial_parse_fills_missing_with_empty(self, three_images, monkeypatch):
        """If the model returns lines for only 2 of 3 images, the third
        slot stays empty and the caller sees a memory-report warning.
        No refusal, no raise — this is a legitimate degraded result."""
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 0
                stdout = (
                    "img_01.jpg: Four painted canvas sample swatches with labels.\n"
                    "img_02.jpg: Illuminated white textured panel on wall.\n"
                )
                stderr = ""

            return _R()

        monkeypatch.setattr(vision_runner.subprocess, "run", fake_run)
        captions, warnings = vision_runner.run_captions(
            three_images, {}, backend="claude_cli",
            prompt_builder=_fake_prompt,
        )
        assert captions[0].startswith("Four painted")
        assert captions[1].startswith("Illuminated")
        assert captions[2] == ""
        assert any("1/3" in w and "came back empty" in w for w in warnings)

    def test_batched_tolerates_list_marker_prefixes(self, three_images, monkeypatch):
        """Parser strips '1.', '-', '*' prefixes the model sometimes adds."""
        def fake_run(cmd, capture_output, text, timeout):
            class _R:
                returncode = 0
                stdout = (
                    "1. img_01.jpg: Four painted canvas sample swatches arranged in row.\n"
                    "- img_02.jpg: Illuminated white textured panel mounted on wall.\n"
                    "* img_03.jpg: Construction site with two workers on green flooring.\n"
                )
                stderr = ""

            return _R()

        monkeypatch.setattr(vision_runner.subprocess, "run", fake_run)
        captions, _ = vision_runner.run_captions(
            three_images, {}, backend="claude_cli",
            prompt_builder=_fake_prompt,
        )
        assert captions[0].startswith("Four painted")
        assert captions[1].startswith("Illuminated")
        assert captions[2].startswith("Construction")

    def test_singleton_claude_cli_uses_per_image_path(self, fake_image, monkeypatch):
        """One-image calls don't take the batched path — they go through
        `_claude_cli_backend` as before so existing singleton behaviour
        (per-image prompt from image_vision) is preserved."""
        calls = []

        def fake_backend(image_path, prompt, model):
            calls.append((image_path, prompt))
            return "One painted canvas sample swatch with a Chinese label."

        monkeypatch.setattr(
            vision_runner, "_BACKEND_REGISTRY",
            {**vision_runner._BACKEND_REGISTRY, "claude_cli": fake_backend},
        )
        captions, _ = vision_runner.run_captions(
            [fake_image], {"project": "P"}, backend="claude_cli",
            prompt_builder=_fake_prompt,
        )
        assert len(calls) == 1
        assert captions == ["One painted canvas sample swatch with a Chinese label."]


class TestBatchPromptBuilder:
    def test_prompt_lists_every_image(self):
        prompt = vision_runner._build_batch_prompt(
            ["/a/img1.jpg", "/b/img2.jpg", "/c/img3.jpg"],
            {"project": "Proj", "event_date": "2024-09-09", "source_basename": "foo"},
        )
        assert "/a/img1.jpg" in prompt
        assert "/b/img2.jpg" in prompt
        assert "/c/img3.jpg" in prompt
        assert "Proj" in prompt
        assert "2024-09-09" in prompt
        # Enforces exact line count so the child doesn't drop images.
        assert "exactly 3" in prompt.lower()


class TestBatchOutputParser:
    def test_parses_plain_lines(self):
        paths = ["/x/img_01.jpg", "/x/img_02.jpg"]
        out = (
            "img_01.jpg: First caption describing contents in detail.\n"
            "img_02.jpg: Second caption with enough words to be real.\n"
        )
        result = vision_runner._parse_batch_output(out, paths)
        assert result[0].startswith("First caption")
        assert result[1].startswith("Second caption")

    def test_returns_empty_for_unmatched_basename(self):
        paths = ["/x/img_01.jpg", "/x/img_missing.jpg"]
        out = "img_01.jpg: Only the first one is described here today.\n"
        result = vision_runner._parse_batch_output(out, paths)
        assert result[0].startswith("Only the first")
        assert result[1] == ""

    def test_ignores_lines_without_colon(self):
        paths = ["/x/img_01.jpg"]
        out = "preamble junk\nimg_01.jpg: A real caption about the image contents.\n"
        result = vision_runner._parse_batch_output(out, paths)
        assert result == ["A real caption about the image contents."]


class TestCleanCaption:
    def test_strips_quote_marks(self):
        assert vision_runner._clean_caption('"A red cube."') == "A red cube."

    def test_keeps_first_line(self):
        assert vision_runner._clean_caption(
            "First line of caption.\nSecond line chatter."
        ) == "First line of caption."

    def test_strips_bullet_prefix(self):
        assert vision_runner._clean_caption("- A diagram.") == "A diagram."

    def test_empty_becomes_empty(self):
        assert vision_runner._clean_caption("") == ""
        assert vision_runner._clean_caption("   \n  ") == ""


class TestDetectBackend:
    def test_stub_when_no_anthropic_and_no_cli(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(vision_runner.shutil, "which", lambda _: None)
        assert vision_runner._detect_backend() == "stub"

    def test_claude_cli_when_cli_present_but_no_api_key(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
        monkeypatch.setattr(vision_runner.shutil, "which",
                            lambda cmd: "/usr/local/bin/claude" if cmd == "claude" else None)
        assert vision_runner._detect_backend() == "claude_cli"


class TestCLI:
    def test_cli_reads_stdin_and_prints_json(self, fake_image, monkeypatch, capsys):
        monkeypatch.setattr(
            sys, "stdin",
            io.StringIO(json.dumps({
                "images": [fake_image],
                "event_meta": {"project": "P"},
                "backend": "stub",
            })),
        )
        rc = vision_runner.main(["--backend", "stub"])
        assert rc == 0
        out = capsys.readouterr().out
        parsed = json.loads(out)
        assert parsed["captions"] == [""]
