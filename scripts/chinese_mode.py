"""Chinese-mode detection for /vault-bridge:research.

Determines whether a research topic targets Chinese-language sources.

Python 3.9 compatible.
"""
from typing import List, Optional
from urllib.parse import urlparse


# URL fragment patterns that indicate Chinese platforms
_CHINESE_NETLOC_FRAGMENTS = {
    "weibo",
    "weixin",
    "xiaohongshu",
    "thepaper",
    "huxiu",
    "jiemian",
    "caixin",
    "36kr",
}


def _contains_han(text: str) -> bool:
    """Return True if text contains any CJK Unified Ideograph character."""
    for ch in text:
        cp = ord(ch)
        # CJK Unified Ideographs: U+4E00–U+9FFF
        # CJK Unified Ideographs Extension A: U+3400–U+4DBF
        if 0x4E00 <= cp <= 0x9FFF or 0x3400 <= cp <= 0x4DBF:
            return True
    return False


def _url_hints_chinese(urls: List[str]) -> bool:
    """Return True if any hinted URL looks like a Chinese platform."""
    for url in urls:
        try:
            parsed = urlparse(url)
            netloc = parsed.netloc.lower()
            # Check for .cn TLD or .com.cn
            if netloc.endswith(".cn") or ".com.cn" in netloc:
                return True
            # Check for known Chinese platform name fragments
            for fragment in _CHINESE_NETLOC_FRAGMENTS:
                if fragment in netloc:
                    return True
        except Exception:
            pass
    return False


def detect_chinese_mode(
    topic: str,
    urls_hinted: Optional[List[str]] = None,
    explicit_lang: Optional[str] = None,
) -> bool:
    """Detect whether research should operate in Chinese mode.

    Parameters
    ----------
    topic:
        The research topic string (possibly containing CJK characters).
    urls_hinted:
        Optional list of URLs already known / hinted by the user.
        Chinese platform URLs trigger Chinese mode even for ASCII topics.
    explicit_lang:
        One of "zh", "en", "auto", or None. "zh" forces True; "en" forces
        False. "auto" and None both fall through to the heuristic.

    Returns
    -------
    bool
        True  → use Chinese-mode search strategy.
        False → English-only search strategy.
    """
    # Explicit overrides
    if explicit_lang == "zh":
        return True
    if explicit_lang == "en":
        return False

    # Heuristic: Han characters in topic
    if _contains_han(topic):
        return True

    # Heuristic: Chinese platform URL hints
    if urls_hinted and _url_hints_chinese(urls_hinted):
        return True

    return False
