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
        assert warnings == []

    def test_aligns_with_input_length(self, tmp_path):
        paths = []
        for i in range(5):
            p = tmp_path / f"i{i}.jpg"
            p.write_bytes(b"\xff\xd8fake\xff\xd9")
            paths.append(str(p))
        captions, _ = vision_runner.run_captions(
            paths, {}, backend="stub", prompt_builder=_fake_prompt,
        )
        assert captions == ["", "", "", "", ""]


class TestMissingImage:
    def test_nonexistent_image_gets_empty_caption_and_warning(self):
        captions, warnings = vision_runner.run_captions(
            ["/nonexistent/does-not-exist.jpg"],
            {}, backend="stub", prompt_builder=_fake_prompt,
        )
        assert captions == [""]
        assert any("missing" in w.lower() for w in warnings)


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
