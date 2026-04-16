"""URL source tier classification for /vault-bridge:research.

Classifies a URL into one of four trust tiers:
    1 — Authoritative (encyclopedia, major newswire, financial press)
    2 — Trade press (tech, design, business media)
    3 — Verified social / gated / unknown (default for unknowns)
    4 — Low-trust UGC (Reddit, Quora, Zhihu, personal blogs)

Python 3.9 compatible.
"""
from typing import List, Optional
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Tier allowlists (eTLD+1 values, lower-case)
# ---------------------------------------------------------------------------

TIER_1_DOMAINS = {
    "wikipedia.org",
    "reuters.com",
    "bloomberg.com",
    "apnews.com",
    "ap.org",
    "bbc.com",
    "bbc.co.uk",
    "nytimes.com",
    "ft.com",
    "caixin.com",
    "caixinglobal.com",
    "xinhuanet.com",
}

TIER_2_DOMAINS = {
    "techcrunch.com",
    "theverge.com",
    "36kr.com",
    "huxiu.com",
    "thepaper.cn",
    "jiemian.com",
    "archdaily.com",
    "dezeen.com",
    "designboom.com",
    "hbr.org",
    "theinformation.com",
}

TIER_3_DOMAINS = {
    "mp.weixin.qq.com",
    "m.weibo.cn",
    "linkedin.com",
}

TIER_4_DOMAINS = {
    "reddit.com",
    "quora.com",
    "zhihu.com",
    "xiaohongshu.com",
    "xhslink.com",
    "wordpress.com",
    "blogspot.com",
    "medium.com",
}


def _extract_etld1(url: str) -> str:
    """Extract eTLD+1 using a simple heuristic (last 2 labels of netloc).

    Strips leading 'www.' before returning.
    Handles special cases like bbc.co.uk (last 3 labels form the eTLD+1).
    """
    try:
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if not netloc:
            # Possibly a bare hostname without scheme
            netloc = url.split("/")[0].lower()
        # Strip port if present
        if ":" in netloc:
            netloc = netloc.rsplit(":", 1)[0]
        # Strip www.
        if netloc.startswith("www."):
            netloc = netloc[4:]
        labels = netloc.split(".")
        if len(labels) >= 2:
            # Handle known two-part TLDs: co.uk, com.cn, co.jp, etc.
            two_part_tlds = {"co.uk", "com.cn", "co.jp", "co.nz", "co.za", "com.au", "com.br"}
            if len(labels) >= 3 and ".".join(labels[-2:]) in two_part_tlds:
                return ".".join(labels[-3:])
            return ".".join(labels[-2:])
        return netloc
    except Exception:
        return ""


def classify_url(url: str, trusted_domains: Optional[List[str]] = None) -> int:
    """Classify a URL into a trust tier (1–4).

    Parameters
    ----------
    url:
        The URL string to classify. Malformed URLs don't crash — they
        return tier 3.
    trusted_domains:
        Optional list of eTLD+1 strings. Any URL matching one of these
        domains is promoted to tier 1 regardless of the built-in lists.

    Returns
    -------
    int
        1 = authoritative, 2 = trade press,
        3 = verified social / unknown, 4 = low-trust UGC.
    """
    try:
        etld1 = _extract_etld1(url)
        parsed = urlparse(url)
        netloc = parsed.netloc.lower()
        if netloc.startswith("www."):
            netloc = netloc[4:]

        # 1. trusted_domains override → tier 1
        if trusted_domains:
            for td in trusted_domains:
                td_norm = td.lower().lstrip("www.").strip(".")
                if etld1 == td_norm:
                    return 1

        # 2. tier 1 check — against both netloc and eTLD+1
        if etld1 in TIER_1_DOMAINS or netloc in TIER_1_DOMAINS:
            return 1

        # 3. tier 2
        if etld1 in TIER_2_DOMAINS or netloc in TIER_2_DOMAINS:
            return 2

        # 4. tier 3 — check full netloc for weixin subdomain pattern
        if netloc in TIER_3_DOMAINS or etld1 in TIER_3_DOMAINS:
            return 3

        # 5. tier 4
        if etld1 in TIER_4_DOMAINS or netloc in TIER_4_DOMAINS:
            return 4

        # 6. default — unknown domain → tier 3
        return 3

    except Exception:
        return 3
