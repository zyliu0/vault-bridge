#!/usr/bin/env python3
"""vault-bridge config stored at ~/.vault-bridge/config.json.

Replaces the requirement for a YAML block in the user's vault CLAUDE.md.
Setup writes this once; every scan command reads it.

Fields:
  archive_root   — absolute path where the user's file archive lives
  preset         — "architecture" | "photographer" | "writer" | "custom"
  file_system_type — "nas-mcp" | "local-path" (auto-detected during setup)
  vault_root     — absolute path to the Obsidian vault

For users who still want a vault CLAUDE.md config block (advanced override),
parse_config.py remains functional and takes precedence if present.
"""
import json
import os
import sys
from pathlib import Path


VALID_PRESETS = {"architecture", "photographer", "writer", "custom"}
VALID_FS_TYPES = {"nas-mcp", "local-path", "external-mount"}


from state import state_dir as _state_dir  # noqa: E402 — shared impl


def _config_path() -> Path:
    return _state_dir() / "config.json"


def load_config() -> dict:
    """Load the config from ~/.vault-bridge/config.json.

    Returns the parsed dict. Raises SetupNeeded if the file doesn't exist
    or is missing required fields.
    """
    path = _config_path()
    if not path.exists():
        raise SetupNeeded(
            "vault-bridge is not configured yet. "
            "Run /vault-bridge:setup to set your archive path and preset."
        )

    try:
        config = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise SetupNeeded(f"vault-bridge config is corrupt: {e}. Re-run /vault-bridge:setup.")

    required = {"archive_root", "preset", "file_system_type", "vault_root"}
    missing = required - set(config.keys())
    if missing:
        raise SetupNeeded(
            f"vault-bridge config missing fields: {sorted(missing)}. "
            f"Re-run /vault-bridge:setup."
        )

    if config["preset"] not in VALID_PRESETS:
        raise SetupNeeded(
            f"vault-bridge config has unknown preset '{config['preset']}'. "
            f"Valid: {sorted(VALID_PRESETS)}"
        )

    if config["file_system_type"] not in VALID_FS_TYPES:
        raise SetupNeeded(
            f"vault-bridge config has unknown file_system_type '{config['file_system_type']}'. "
            f"Valid: {sorted(VALID_FS_TYPES)}"
        )

    return config


def save_config(
    archive_root: str,
    preset: str,
    file_system_type: str,
    vault_root: str,
) -> Path:
    """Write the config to ~/.vault-bridge/config.json.

    Returns the written path.
    """
    if preset not in VALID_PRESETS:
        raise ValueError(f"Invalid preset: {preset}. Valid: {sorted(VALID_PRESETS)}")
    if file_system_type not in VALID_FS_TYPES:
        raise ValueError(f"Invalid file_system_type: {file_system_type}")

    config = {
        "archive_root": archive_root,
        "preset": preset,
        "file_system_type": file_system_type,
        "vault_root": vault_root,
    }

    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return path


class SetupNeeded(Exception):
    """Raised when the config doesn't exist or is incomplete."""
    pass


# ---------------------------------------------------------------------------
# Preset routing tables — the plugin's built-in knowledge
# ---------------------------------------------------------------------------

PRESETS = {
    "architecture": {
        "routing_patterns": [
            {"match": "3_施工图 CD", "subfolder": "CD"},
            {"match": " CD", "subfolder": "CD"},
            {"match": "2_方案SD", "subfolder": "SD"},
            {"match": " SD", "subfolder": "SD"},
            {"match": "1_概念Concept", "subfolder": "SD"},
            {"match": "结构", "subfolder": "Structure"},
            {"match": "Structure", "subfolder": "Structure"},
            {"match": "模型汇总", "subfolder": "Renderings"},
            {"match": "效果图", "subfolder": "Renderings"},
            {"match": "渲染", "subfolder": "Renderings"},
            {"match": "0_文档资料Docs", "subfolder": "Admin"},
        ],
        "content_overrides": [
            {"when": "filename contains meeting or 会议 or 汇报 or 汇", "subfolder": "Meetings"},
        ],
        "fallback": "Admin",
        "skip_patterns": [
            "#recycle", "@eaDir", "_embedded_files",
            ".DS_Store", "Thumbs.db",
            "*.dwl", "*.dwl2", "*.bak", "*.tmp",
        ],
        "style": {
            "note_filename_pattern": "YYYY-MM-DD topic.md",
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
            "image_grid_cssclass": "img-grid",
        },
    },
    "photographer": {
        "routing_patterns": [
            {"match": "_Selects", "subfolder": "Selects"},
            {"match": "_Contact", "subfolder": "ContactSheets"},
            {"match": "Edited", "subfolder": "Edited"},
            {"match": "Raw", "subfolder": "Raw"},
            {"match": "Portfolio", "subfolder": "Portfolio"},
        ],
        "content_overrides": [],
        "fallback": "Archive",
        "skip_patterns": [
            ".DS_Store", "Thumbs.db", "*.xmp", "*.lrcat", "*.lrdata",
        ],
        "style": {
            "note_filename_pattern": "YYYY-MM-DD topic.md",
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
        },
    },
    "writer": {
        "routing_patterns": [
            {"match": "Drafts", "subfolder": "Drafts"},
            {"match": "Published", "subfolder": "Published"},
            {"match": "Research", "subfolder": "Research"},
            {"match": "Interviews", "subfolder": "Interviews"},
            {"match": "Meetings", "subfolder": "Meetings"},
        ],
        "content_overrides": [
            {"when": "filename contains meeting or notes or call", "subfolder": "Meetings"},
        ],
        "fallback": "Inbox",
        "skip_patterns": [
            ".DS_Store", "*.tmp", ".obsidian",
        ],
        "style": {
            "note_filename_pattern": "YYYY-MM-DD topic.md",
            "writing_voice": "first-person-diary",
            "summary_word_count": [100, 200],
        },
    },
}


def get_preset(name: str) -> dict:
    """Return the routing/style/skip config for a named preset.

    Raises KeyError if the preset doesn't exist.
    """
    if name == "custom":
        # Custom means "user has a vault CLAUDE.md config block" — fall through
        # to parse_config.py. Not handled here.
        raise KeyError(
            "The 'custom' preset means the user has a vault CLAUDE.md config "
            "block. Use scripts/parse_config.py to load it."
        )
    return PRESETS[name]


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.stderr.write("usage: setup_config.py <load|save> [args...]\n")
        sys.exit(2)

    cmd = sys.argv[1]
    if cmd == "load":
        try:
            config = load_config()
            json.dump(config, sys.stdout, indent=2)
            print()
        except SetupNeeded as e:
            sys.stderr.write(f"vault-bridge: {e}\n")
            sys.exit(2)
    elif cmd == "save":
        if len(sys.argv) != 6:
            sys.stderr.write(
                "usage: setup_config.py save <archive_root> <preset> "
                "<file_system_type> <vault_root>\n"
            )
            sys.exit(2)
        path = save_config(sys.argv[2], sys.argv[3], sys.argv[4], sys.argv[5])
        print(f"Config saved to {path}")
    else:
        sys.stderr.write(f"unknown command: {cmd}\n")
        sys.exit(2)
