#!/usr/bin/env python3
"""vault-bridge multi-domain config stored at ~/.vault-bridge/config.json.

v2 config format — replaces the single-preset model with a domains list.
Each domain has its own archive_root, file_system_type, routing patterns,
and default_tags. The vault_name is shared across all domains.

v2 config.json schema:
  config_version — always 2
  vault_name     — the Obsidian vault name (NOT a filesystem path)
  domains        — list of domain dicts, each with:
    name, label, archive_root, file_system_type, routing_patterns,
    content_overrides, fallback, skip_patterns, default_tags, style

Backward compatibility: v1 configs (flat preset) auto-upgrade to a
single-domain v2 config on load.
"""
import json
import os
import sys
from pathlib import Path


VALID_FS_TYPES = {"nas-mcp", "local-path", "external-mount"}

from state import state_dir as _state_dir  # noqa: E402


def _config_path() -> Path:
    return _state_dir() / "config.json"


def load_config() -> dict:
    """Load the config from ~/.vault-bridge/config.json.

    Returns a v2 config dict with config_version, vault_name, and domains.
    Auto-upgrades v1 configs (flat preset) to v2 on load.
    Raises SetupNeeded if the file doesn't exist or is invalid.
    """
    path = _config_path()
    if not path.exists():
        raise SetupNeeded(
            "vault-bridge is not configured yet. "
            "Run /vault-bridge:setup to set your archive path and domains."
        )

    try:
        config = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError) as e:
        raise SetupNeeded(f"vault-bridge config is corrupt: {e}. Re-run /vault-bridge:setup.")

    # Auto-upgrade v1 configs
    if "config_version" not in config:
        config = _upgrade_v1_config(config)

    if config.get("config_version") != 2:
        raise SetupNeeded(
            f"vault-bridge config has unsupported version {config.get('config_version')}. "
            "Re-run /vault-bridge:setup."
        )

    if "vault_name" not in config:
        raise SetupNeeded("vault-bridge config missing vault_name. Re-run /vault-bridge:setup.")

    if not config.get("domains"):
        raise SetupNeeded("vault-bridge config has no domains. Re-run /vault-bridge:setup.")

    return config


def _upgrade_v1_config(v1: dict) -> dict:
    """Convert a v1 config (flat preset) to v2 (domains list)."""
    required_v1 = {"archive_root", "preset", "file_system_type", "vault_name"}
    if not required_v1.issubset(set(v1.keys())):
        raise SetupNeeded(
            "vault-bridge config is missing fields and cannot be auto-upgraded. "
            "Re-run /vault-bridge:setup."
        )

    preset_name = v1["preset"]
    if preset_name == "custom":
        # Custom v1 configs can't be auto-upgraded — they rely on CLAUDE.md
        template = get_domain_template("general")
    elif preset_name in DOMAIN_TEMPLATES:
        template = get_domain_template(preset_name)
    elif preset_name == "photographer":
        template = get_domain_template("photography")
    elif preset_name == "writer":
        template = get_domain_template("writing")
    else:
        template = get_domain_template("general")

    domain = {
        "name": preset_name if preset_name != "custom" else "general",
        "label": preset_name.replace("-", " ").title(),
        "archive_root": v1["archive_root"],
        "file_system_type": v1["file_system_type"],
        **template,
    }

    return {
        "config_version": 2,
        "vault_name": v1["vault_name"],
        "domains": [domain],
    }


def save_config(vault_name: str, domains: list) -> Path:
    """Write the v2 config to ~/.vault-bridge/config.json.

    Args:
        vault_name: The Obsidian vault name (not a path).
        domains: List of domain dicts.

    Returns the written path.
    """
    if os.sep in vault_name or vault_name.startswith("~"):
        raise ValueError(
            f"vault_name should be a vault name, not a path: {vault_name!r}. "
            "Use the name as shown in Obsidian → Settings → About."
        )

    if not domains:
        raise ValueError("domains must contain at least one domain.")

    names = [d["name"] for d in domains]
    if len(names) != len(set(names)):
        raise ValueError(f"domains contains duplicate names: {names}")

    for d in domains:
        if d.get("file_system_type") and d["file_system_type"] not in VALID_FS_TYPES:
            raise ValueError(
                f"Domain '{d.get('name')}' has invalid file_system_type: "
                f"'{d['file_system_type']}'. Valid: {sorted(VALID_FS_TYPES)}"
            )

    config = {
        "config_version": 2,
        "vault_name": vault_name,
        "domains": domains,
    }

    path = _config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(config, indent=2, ensure_ascii=False) + "\n")
    return path


class SetupNeeded(Exception):
    """Raised when the config doesn't exist or is incomplete."""
    pass


# ---------------------------------------------------------------------------
# Domain lookup helpers
# ---------------------------------------------------------------------------

def get_domain_by_name(config: dict, name: str) -> dict:
    """Return a domain dict by name. Raises KeyError if not found."""
    for d in config.get("domains", []):
        if d["name"] == name:
            return d
    raise KeyError(f"no domain named '{name}' in config")


def get_domain_for_path(config: dict, source_path: str):
    """Return the domain whose archive_root is a prefix of source_path.

    Returns the domain dict, or None if no domain matches.
    """
    for d in config.get("domains", []):
        root = d.get("archive_root", "")
        if root and source_path.startswith(root):
            return d
    return None


# ---------------------------------------------------------------------------
# Domain templates — starter configs users pick during setup
# ---------------------------------------------------------------------------

_DEFAULT_STYLE = {
    "note_filename_pattern": "YYYY-MM-DD topic.md",
    "writing_voice": "first-person-diary",
    "summary_word_count": [100, 200],
}

DOMAIN_TEMPLATES = {
    # -----------------------------------------------------------------------
    # Architecture / design practice
    # Vault subfolders: Admin, SD, DD, CD, CA, Meetings, Renderings, Structure
    # -----------------------------------------------------------------------
    "architecture": {
        "routing_patterns": [
            # Phase-based routing (bilingual folder names)
            {"match": "3_施工图 CD", "subfolder": "CD"},
            {"match": " CD", "subfolder": "CD"},
            {"match": "2_方案SD", "subfolder": "SD"},
            {"match": " SD", "subfolder": "SD"},
            {"match": "1_概念Concept", "subfolder": "SD"},
            {"match": " DD", "subfolder": "DD"},
            {"match": "深化", "subfolder": "DD"},
            {"match": " CA", "subfolder": "CA"},
            {"match": "竣工", "subfolder": "CA"},
            # Specialty routing
            {"match": "结构", "subfolder": "Structure"},
            {"match": "Structure", "subfolder": "Structure"},
            {"match": "模型汇总", "subfolder": "Renderings"},
            {"match": "效果图", "subfolder": "Renderings"},
            {"match": "渲染", "subfolder": "Renderings"},
            {"match": "Render", "subfolder": "Renderings"},
            {"match": "0_文档资料Docs", "subfolder": "Admin"},
        ],
        "content_overrides": [
            {"when": "filename contains meeting or 会议 or 汇报 or 汇 or review or memo", "subfolder": "Meetings"},
        ],
        "fallback": "Admin",
        "skip_patterns": [
            "#recycle", "@eaDir", "_embedded_files",
            ".DS_Store", "Thumbs.db",
            "*.dwl", "*.dwl2", "*.bak", "*.tmp",
        ],
        "default_tags": ["architecture"],
        "style": {**_DEFAULT_STYLE, "image_grid_cssclass": "img-grid"},
    },
    # -----------------------------------------------------------------------
    # Photography
    # Vault subfolders: Selects, ContactSheets, Edited, Raw, BTS, Scouting,
    #                   Portfolio
    # -----------------------------------------------------------------------
    "photography": {
        "routing_patterns": [
            {"match": "_Selects", "subfolder": "Selects"},
            {"match": "Selects", "subfolder": "Selects"},
            {"match": "_Contact", "subfolder": "ContactSheets"},
            {"match": "Contact", "subfolder": "ContactSheets"},
            {"match": "Edited", "subfolder": "Edited"},
            {"match": "Final", "subfolder": "Edited"},
            {"match": "Raw", "subfolder": "Raw"},
            {"match": "Original", "subfolder": "Raw"},
            {"match": "BTS", "subfolder": "BTS"},
            {"match": "Behind", "subfolder": "BTS"},
            {"match": "Scout", "subfolder": "Scouting"},
            {"match": "Recce", "subfolder": "Scouting"},
            {"match": "Portfolio", "subfolder": "Portfolio"},
        ],
        "content_overrides": [],
        "fallback": "Archive",
        "skip_patterns": [
            ".DS_Store", "Thumbs.db", "*.xmp", "*.lrcat", "*.lrdata",
            "Previews.lrdata",
        ],
        "default_tags": ["photography"],
        "style": {**_DEFAULT_STYLE},
    },
    # -----------------------------------------------------------------------
    # Writing
    # Vault subfolders: Drafts, Published, Research, Interviews, Meetings
    # -----------------------------------------------------------------------
    "writing": {
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
        "skip_patterns": [".DS_Store", "*.tmp", ".obsidian"],
        "default_tags": ["writing"],
        "style": {**_DEFAULT_STYLE},
    },
    # -----------------------------------------------------------------------
    # Social media / content creation
    # Vault subfolders: Scripts, Short-form, Long-form, Threads, Assets,
    #                   Analytics, Collabs
    # Routing by content type, not by platform — platform goes in tags.
    # -----------------------------------------------------------------------
    "social-media": {
        "routing_patterns": [
            {"match": "Script", "subfolder": "Scripts"},
            {"match": "Vlog", "subfolder": "Scripts"},
            {"match": "Short", "subfolder": "Short-form"},
            {"match": "Reel", "subfolder": "Short-form"},
            {"match": "TikTok", "subfolder": "Short-form"},
            {"match": "Long", "subfolder": "Long-form"},
            {"match": "YouTube", "subfolder": "Long-form"},
            {"match": "Podcast", "subfolder": "Long-form"},
            {"match": "Thread", "subfolder": "Threads"},
            {"match": "Post", "subfolder": "Threads"},
            {"match": "Tweet", "subfolder": "Threads"},
            {"match": "Asset", "subfolder": "Assets"},
            {"match": "Thumbnail", "subfolder": "Assets"},
            {"match": "Cover", "subfolder": "Assets"},
            {"match": "Analytic", "subfolder": "Analytics"},
            {"match": "Metric", "subfolder": "Analytics"},
            {"match": "Collab", "subfolder": "Collabs"},
            {"match": "Sponsor", "subfolder": "Collabs"},
        ],
        "content_overrides": [],
        "fallback": "Inbox",
        "skip_patterns": [".DS_Store", "*.tmp", "Thumbs.db"],
        "default_tags": ["content-creation"],
        "style": {**_DEFAULT_STYLE},
    },
    # -----------------------------------------------------------------------
    # Research / information gathering
    # Vault subfolders: Sources, Notes, Clippings, Bookmarks, References,
    #                   Highlights
    # -----------------------------------------------------------------------
    "research": {
        "routing_patterns": [
            {"match": "Sources", "subfolder": "Sources"},
            {"match": "Papers", "subfolder": "Sources"},
            {"match": "Notes", "subfolder": "Notes"},
            {"match": "Clippings", "subfolder": "Clippings"},
            {"match": "Bookmarks", "subfolder": "Bookmarks"},
            {"match": "Links", "subfolder": "Bookmarks"},
            {"match": "References", "subfolder": "References"},
            {"match": "Bibliography", "subfolder": "References"},
            {"match": "Highlights", "subfolder": "Highlights"},
            {"match": "Annotations", "subfolder": "Highlights"},
        ],
        "content_overrides": [],
        "fallback": "Inbox",
        "skip_patterns": [".DS_Store", "*.tmp"],
        "default_tags": ["research"],
        "style": {**_DEFAULT_STYLE},
    },
    # -----------------------------------------------------------------------
    # General — minimal routing, good starting point for any domain
    # Vault subfolders: Documents, Media, Meetings
    # -----------------------------------------------------------------------
    "general": {
        "routing_patterns": [
            {"match": "Documents", "subfolder": "Documents"},
            {"match": "Media", "subfolder": "Media"},
            {"match": "Meetings", "subfolder": "Meetings"},
        ],
        "content_overrides": [
            {"when": "filename contains meeting or memo or call", "subfolder": "Meetings"},
        ],
        "fallback": "Inbox",
        "skip_patterns": [".DS_Store", "*.tmp", "Thumbs.db"],
        "default_tags": [],
        "style": {**_DEFAULT_STYLE},
    },
}


def get_domain_template(name: str) -> dict:
    """Return a domain template by name. Raises KeyError if not found."""
    return DOMAIN_TEMPLATES[name]


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
    else:
        sys.stderr.write(f"unknown command: {cmd}. Use 'load'.\n")
        sys.exit(2)
