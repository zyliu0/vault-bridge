"""Research report builder for /vault-bridge:research.

Generates a full markdown report (frontmatter + body) from a structured
params dict. All section items include footnote citations; unverified
items are prefixed with a warning symbol.

Python 3.9 compatible.

Expected params shape
---------------------
{
    "topic": str,
    "goal": str,
    "chinese_mode": bool,
    "domain": str,                   # active_domain from config
    "project": Optional[str],        # vault folder name or None
    "sources": List[{
        "url": str,
        "tier": int,                 # 1..4
        "title": str,
        "author": Optional[str],
        "published": Optional[str],
        "accessed_date": str,        # YYYY-MM-DD
        "excerpt": str,              # first ~1500 chars of defuddle markdown
        "claims": List[str],         # LLM-extracted bullet list
    }],
    "source_images": List[str],      # <=10 image URLs harvested from defuddle content
    "sections": {                    # each section is a list of {text, source_refs}
        "overview": List[{"text": str, "source_refs": List[int]}],
        "culture": List[...],
        "recent_activities": List[...],
        "main_figures": List[...],
    },
    "analysis": List[{"text": str, "source_refs": List[int]}],
    "open_questions": List[str],
    "tags": List[str],
    "warnings": List[str],
}
"""
import datetime
from typing import Any, Dict, List, Optional


def _today() -> str:
    return datetime.date.today().isoformat()


def _footnote_label(n: int) -> str:
    """Return a footnote label like [^1]."""
    return f"[^{n}]"


def _render_items_with_refs(
    items: List[Dict[str, Any]],
    source_index_map: Dict[int, int],
) -> List[str]:
    """Render a list of {text, source_refs} items as markdown bullets.

    source_index_map maps original source list index (0-based) to
    footnote number (1-based, sorted by tier then first-use).

    Items with empty source_refs are prefixed with ⚠ unverified:.
    """
    lines = []
    for item in items:
        text = item.get("text", "")
        refs = item.get("source_refs", [])
        if not refs:
            lines.append(f"- ⚠ unverified: {text}")
        else:
            footnotes = "".join(
                _footnote_label(source_index_map[r])
                for r in refs
                if r in source_index_map
            )
            lines.append(f"- {text}{footnotes}")
    return lines


def _build_source_order(sources: List[Dict[str, Any]]) -> List[int]:
    """Return original indices of sources sorted by tier ascending.

    Within the same tier, preserve original order (first-use approximation).
    """
    indexed = [(i, s.get("tier", 3)) for i, s in enumerate(sources)]
    indexed.sort(key=lambda x: x[1])
    return [i for i, _ in indexed]


def build_report(params: Dict[str, Any]) -> str:
    """Build a full markdown research report.

    Parameters
    ----------
    params:
        Structured dict as documented in the module docstring.

    Returns
    -------
    str
        Complete markdown string: YAML frontmatter + body sections.
    """
    topic: str = params.get("topic", "")
    goal: str = params.get("goal", "")
    chinese_mode: bool = bool(params.get("chinese_mode", False))
    domain: str = params.get("domain", "")
    project: Optional[str] = params.get("project")
    sources: List[Dict[str, Any]] = params.get("sources", [])
    source_images: List[str] = params.get("source_images", [])
    sections: Dict[str, Any] = params.get("sections", {})
    analysis_items: List[Dict[str, Any]] = params.get("analysis", [])
    open_questions: List[str] = params.get("open_questions", [])
    tags: List[str] = params.get("tags", [])
    warnings: List[str] = params.get("warnings", [])
    captured_date: str = _today()

    # Build source tier counts
    tier_counts: Dict[int, int] = {1: 0, 2: 0, 3: 0, 4: 0}
    for s in sources:
        t = s.get("tier", 3)
        if t in tier_counts:
            tier_counts[t] += 1

    # Build footnote order (sorted by tier asc)
    ordered_indices = _build_source_order(sources)
    # Maps original index -> footnote number (1-based)
    source_index_map: Dict[int, int] = {
        orig_idx: fn_num
        for fn_num, orig_idx in enumerate(ordered_indices, start=1)
    }

    # -----------------------------------------------------------------------
    # Frontmatter
    # -----------------------------------------------------------------------
    fm_lines = ["---"]
    fm_lines.append("schema_version: 2")
    fm_lines.append("plugin: vault-bridge")
    fm_lines.append("note_kind: research")
    fm_lines.append(f"domain: \"{domain}\"")
    fm_lines.append(f"topic: \"{topic}\"")
    fm_lines.append(f"goal: \"{goal}\"")
    fm_lines.append(f"chinese_mode: {'true' if chinese_mode else 'false'}")
    fm_lines.append(f"captured_date: \"{captured_date}\"")
    if project is None:
        fm_lines.append("project: null")
    else:
        fm_lines.append(f"project: \"{project}\"")

    # source_urls
    fm_lines.append("source_urls:")
    for s in sources:
        fm_lines.append(f"  - \"{s.get('url', '')}\"")

    # source_tiers
    fm_lines.append("source_tiers:")
    fm_lines.append(f"  tier1: {tier_counts[1]}")
    fm_lines.append(f"  tier2: {tier_counts[2]}")
    fm_lines.append(f"  tier3: {tier_counts[3]}")
    fm_lines.append(f"  tier4_discarded: {tier_counts[4]}")

    # source_images
    fm_lines.append("source_images:")
    for img_url in source_images:
        fm_lines.append(f"  - \"{img_url}\"")

    fm_lines.append("images_materialized: false")

    # tags
    tag_list = ", ".join(f'"{t}"' for t in tags)
    fm_lines.append(f"tags: [{tag_list}]")
    fm_lines.append("cssclasses: []")
    fm_lines.append("---")

    # -----------------------------------------------------------------------
    # Body
    # -----------------------------------------------------------------------
    body_lines: List[str] = []

    # Abstract callout — synthesize from overview
    overview_items = sections.get("overview", [])
    overview_text = " ".join(
        item.get("text", "") for item in overview_items if item.get("text")
    )
    if not overview_text:
        overview_text = f"Research report on {topic}."
    body_lines.append(f"> [!abstract] Summary")
    # Limit to first 2 sentences approx
    sentences = overview_text.replace(". ", ".|").split("|")
    abstract = " ".join(sentences[:2]).strip()
    body_lines.append(f"> {abstract}")
    body_lines.append("")

    # Overview
    body_lines.append("## Overview")
    body_lines.append("")
    body_lines.extend(_render_items_with_refs(overview_items, source_index_map))
    body_lines.append("")

    # Culture
    culture_items = sections.get("culture", [])
    body_lines.append("## Culture")
    body_lines.append("")
    body_lines.extend(_render_items_with_refs(culture_items, source_index_map))
    body_lines.append("")

    # Recent Activities
    recent_items = sections.get("recent_activities", [])
    body_lines.append("## Recent Activities")
    body_lines.append("")
    body_lines.extend(_render_items_with_refs(recent_items, source_index_map))
    body_lines.append("")

    # Main Figures
    figures_items = sections.get("main_figures", [])
    body_lines.append("## Main Figures")
    body_lines.append("")
    body_lines.extend(_render_items_with_refs(figures_items, source_index_map))
    body_lines.append("")

    # Analysis vs. Goal
    body_lines.append("## Analysis vs. Goal")
    body_lines.append("")
    body_lines.extend(_render_items_with_refs(analysis_items, source_index_map))
    body_lines.append("")

    # Open Questions
    body_lines.append("## Open Questions")
    body_lines.append("")
    for q in open_questions:
        body_lines.append(f"- {q}")
    body_lines.append("")

    # Sources — footnote definitions, sorted by tier asc
    body_lines.append("## Sources")
    body_lines.append("")
    for fn_num, orig_idx in enumerate(ordered_indices, start=1):
        s = sources[orig_idx]
        url = s.get("url", "")
        title = s.get("title", url)
        tier = s.get("tier", 3)
        accessed = s.get("accessed_date", captured_date)
        body_lines.append(
            f"[^{fn_num}]: [{title}]({url}) — tier {tier}, accessed {accessed}"
        )
    body_lines.append("")

    # Source Images metadata-only section
    body_lines.append("## Source Images (metadata only)")
    body_lines.append("")
    body_lines.append(
        "Images not downloaded in this run — use a future command to "
        "materialize them into `_Attachments/`."
    )
    body_lines.append("")
    for img_url in source_images:
        body_lines.append(f"![]({img_url})")
    body_lines.append("")

    # Warnings (only if non-empty)
    if warnings:
        body_lines.append("## Warnings")
        body_lines.append("")
        for w in warnings:
            body_lines.append(f"- {w}")
        body_lines.append("")

    # Assemble
    result = "\n".join(fm_lines) + "\n\n" + "\n".join(body_lines)
    return result
