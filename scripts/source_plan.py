"""Source plan builder for /vault-bridge:research.

Generates a structured plan: WebSearch queries and direct URLs to visit,
based on the research topic and detected Chinese mode.

Python 3.9 compatible.
"""
from typing import Any, Dict, List


# Caveat messages
_CAVEAT_DEFUDDLE = (
    "defuddle CLI is used for HTML extraction; pages behind auth walls will fail."
)
_CAVEAT_XIAOHONGSHU = (
    "Xiaohongshu web pages often return stubs — the command will skip them "
    "and may ask for direct URLs."
)


def build_source_plan(
    topic: str,
    chinese_mode: bool,
    max_sources: int = 15,
) -> Dict[str, Any]:
    """Build a structured source discovery plan for a research topic.

    Parameters
    ----------
    topic:
        The research topic string.
    chinese_mode:
        If True, add Chinese-language search queries and zh.wikipedia.org
        as a direct URL.
    max_sources:
        Budget hint — not applied to the plan itself; used downstream
        by the fetch step to cap how many sources are fetched.

    Returns
    -------
    dict with keys:
        english_searches  List[str]  — WebSearch queries (English)
        chinese_searches  List[str]  — WebSearch queries (Chinese), empty if not chinese_mode
        direct_urls       List[str]  — candidate URLs to try directly
        caveats           List[str]  — human-readable warnings shown to user
    """
    # English queries (always)
    english_searches: List[str] = [
        topic,
        f'"{topic}" company profile',
        f'"{topic}" founder CEO',
        f'"{topic}" recent news',
        f'"{topic}" culture',
    ]

    # Chinese queries (only in chinese_mode)
    chinese_searches: List[str] = []
    if chinese_mode:
        chinese_searches = [
            topic,
            f"{topic} 简介",
            f"{topic} 创始人",
            f"{topic} 最新动态",
            f"{topic} 文化",
        ]

    # Direct URLs
    wiki_topic = topic.replace(" ", "_")
    direct_urls: List[str] = [
        f"https://en.wikipedia.org/wiki/{wiki_topic}",
    ]
    if chinese_mode:
        direct_urls.append(f"https://zh.wikipedia.org/wiki/{wiki_topic}")

    # Caveats
    caveats: List[str] = [_CAVEAT_DEFUDDLE]
    if chinese_mode:
        caveats.append(_CAVEAT_XIAOHONGSHU)

    return {
        "english_searches": english_searches,
        "chinese_searches": chinese_searches,
        "direct_urls": direct_urls,
        "caveats": caveats,
    }
