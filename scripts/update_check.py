"""GitHub update-check for vault-bridge and companion plugins.

Non-blocking, silent on failure, cache-throttled. Invoked via the
UserPromptSubmit hook when the user runs any /vault-bridge: command.
"""
import argparse
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

# Ensure update_cache can be found when this script runs as __main__
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

import update_cache  # noqa: E402


DEFAULT_REPOS = [
    {
        "url": "https://github.com/zyliu0/vault-bridge",
        "label": "zyliu0/vault-bridge",
        "is_self": True,
    },
    {
        "url": "https://github.com/axtonliu/axton-obsidian-visual-skills",
        "label": "axtonliu/axton-obsidian-visual-skills",
        "is_self": False,
    },
    {
        "url": "https://github.com/kepano/obsidian-skills",
        "label": "kepano/obsidian-skills",
        "is_self": False,
    },
]

_HEX40_RE = re.compile(r"^[0-9a-f]{40}$")


def is_relevant_prompt(prompt: Optional[str]) -> bool:
    """Return True if the prompt contains a /vault-bridge: command (case-insensitive)."""
    if not prompt:
        return False
    return "/vault-bridge:" in prompt.lower()


def is_disabled() -> bool:
    """Return True when update checks are disabled via env var.

    VAULT_BRIDGE_UPDATE_CHECK in {off, 0, false, no} (case-insensitive) → True.
    Empty / unset → False.
    """
    val = os.environ.get("VAULT_BRIDGE_UPDATE_CHECK", "").strip().lower()
    return val in {"off", "0", "false", "no"}


def get_repos() -> List[dict]:
    """Return list of repos to check.

    If VAULT_BRIDGE_UPDATE_REPOS env is set, parse comma-separated URLs;
    each URL becomes {"url": ..., "label": <last 2 path segments>, "is_self": ...}.
    Otherwise return DEFAULT_REPOS.
    """
    env_val = os.environ.get("VAULT_BRIDGE_UPDATE_REPOS")
    if not env_val:
        return list(DEFAULT_REPOS)
    repos = []
    for raw_url in env_val.split(","):
        url = raw_url.strip()
        if not url:
            continue
        parts = [p for p in url.rstrip("/").split("/") if p]
        label = "/".join(parts[-2:]) if len(parts) >= 2 else url
        is_self = "vault-bridge" in url
        repos.append({"url": url, "label": label, "is_self": is_self})
    return repos


def fetch_remote_sha(url: str, timeout: float = 5.0) -> Optional[str]:
    """Fetch the HEAD sha from a remote GitHub repo via git ls-remote.

    Returns 40-char hex string on success, None on any failure.
    """
    try:
        result = subprocess.run(
            ["git", "ls-remote", url, "HEAD"],
            timeout=timeout,
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            return None
        first_line = result.stdout.strip().split("\n")[0] if result.stdout.strip() else ""
        if not first_line:
            return None
        sha = first_line.split("\t")[0].strip()
        if _HEX40_RE.match(sha):
            return sha
        return None
    except Exception:
        return None


def get_local_sha(path: str) -> Optional[str]:
    """Get the current HEAD sha of the local git repo at path.

    Returns 40-char hex string on success, None on any failure.
    """
    try:
        result = subprocess.run(
            ["git", "-C", path, "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5.0,
        )
        if result.returncode != 0:
            return None
        sha = result.stdout.strip()
        if _HEX40_RE.match(sha):
            return sha
        return None
    except Exception:
        return None


def format_notice(
    repo: dict,
    old_sha: Optional[str],
    new_sha: str,
    plugin_root: str,
) -> str:
    """Format a one-line update notice for display on stderr."""
    label = repo["label"]
    new_short = new_sha[:7]

    if repo["is_self"]:
        has_git = (Path(plugin_root) / ".git").exists()
        if has_git and old_sha:
            old_short = old_sha[:7]
            return (
                f"vault-bridge: update available — {label} "
                f"({old_short} → {new_short}). "
                f"Run `git pull` in {plugin_root} to update."
            )
        else:
            return (
                f"vault-bridge: update available — {label} "
                f"({new_short}). "
                f"Check the plugin marketplace for updates."
            )
    else:
        return f"vault-bridge: upstream changed — {label} (new: {new_short})"


def check_repo(
    repo: dict,
    cache: dict,
    plugin_root: str,
    now: datetime,
) -> Tuple[Optional[str], Optional[str]]:
    """Check a single repo for updates.

    Returns (notice, new_remote_sha).
    - notice is None if no update to report.
    - new_remote_sha is None if the remote could not be reached.
    """
    remote_sha = fetch_remote_sha(repo["url"])
    if remote_sha is None:
        return (None, None)

    if repo["is_self"]:
        local = get_local_sha(plugin_root)
        if local is not None and local != remote_sha:
            return (format_notice(repo, local, remote_sha, plugin_root), remote_sha)
        return (None, remote_sha)
    else:
        entry = update_cache.get_entry(cache, repo["url"])
        prev = entry.get("remote_sha") if entry else None
        if prev is None:
            # First time seeing this repo — establish baseline, no notice
            return (None, remote_sha)
        elif prev != remote_sha:
            return (format_notice(repo, prev, remote_sha, plugin_root), remote_sha)
        else:
            return (None, remote_sha)


def run(
    plugin_root: str,
    prompt: Optional[str],
    force: bool,
    now: Optional[datetime] = None,
    cache_path_override: Optional[Path] = None,
) -> List[str]:
    """Run the update check and return a list of notice strings.

    Fail-silent: any exception is caught and returns []. A failure to
    persist the cache never drops collected notices — the cache write
    is isolated in its own try/except so the return value always
    reflects the checks actually performed.

    cache_path_override lets tests inject the cache path directly instead
    of relying on the VAULT_BRIDGE_CACHE_DIR env var. Production callers
    leave it None.
    """
    try:
        if is_disabled():
            return []

        now = now or datetime.now(timezone.utc)
        repos = get_repos()
        cache_p = cache_path_override if cache_path_override is not None else update_cache.cache_path()
        cache = update_cache.load_cache(cache_p)
        ttl = update_cache.get_ttl_hours()

        relevant = is_relevant_prompt(prompt) or force
        any_stale = any(
            update_cache.is_stale(update_cache.get_entry(cache, r["url"]), ttl, now)
            for r in repos
        )

        if not relevant and not any_stale:
            return []

        notices: List[str] = []

        def _check_one(repo: dict) -> Tuple[Optional[str], Optional[str]]:
            try:
                return check_repo(repo, cache, plugin_root, now)
            except Exception:
                return (None, None)

        with ThreadPoolExecutor(max_workers=3) as executor:
            futures = {executor.submit(_check_one, repo): repo for repo in repos}
            for future, repo in futures.items():
                try:
                    notice, new_sha = future.result()
                except Exception:
                    notice, new_sha = None, None
                if notice:
                    notices.append(notice)
                if new_sha is not None:
                    update_cache.put_entry(cache, repo["url"], new_sha, now)

        # Cache persistence must not swallow collected notices if the
        # disk write fails (e.g. read-only home dir).
        try:
            update_cache.save_cache(cache_p, cache)
        except Exception:
            pass

        return notices

    except Exception:
        return []


def main() -> None:
    """CLI entry point for update_check.py."""
    parser = argparse.ArgumentParser(description="Check for vault-bridge updates.")
    parser.add_argument("--plugin-root", required=True, help="Path to vault-bridge plugin root")
    parser.add_argument("--force", action="store_true", help="Force check regardless of cache")
    args = parser.parse_args()

    # Try to parse prompt from stdin (may be UserPromptSubmit JSON)
    prompt: Optional[str] = None
    try:
        if not sys.stdin.isatty():
            raw = sys.stdin.read()
            if raw.strip():
                parsed = json.loads(raw)
                prompt = parsed.get("prompt")
    except Exception:
        pass

    notices = run(plugin_root=args.plugin_root, prompt=prompt, force=args.force)
    for notice in notices:
        print(notice, file=sys.stderr)

    sys.exit(0)


if __name__ == "__main__":
    main()
