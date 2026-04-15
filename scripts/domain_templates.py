#!/usr/bin/env python3
"""vault-bridge built-in domain templates.

Narrowed from setup_config.py (Phase 1 of v2.0 restructure).
This module owns only the templates dict, the valid FS-type set,
and the template getter. All config I/O lives in effective_config.py.
"""

VALID_FS_TYPES = {"nas-mcp", "local-path", "external-mount"}

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
    """Return a copy of the domain template by name. Raises KeyError if not found."""
    return DOMAIN_TEMPLATES[name]
