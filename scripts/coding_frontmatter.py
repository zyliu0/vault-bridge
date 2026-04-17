"""Coding-domain frontmatter helpers for vault-bridge.

Layered onto schema v2 for coding-domain events: language, repo_url,
framework, branch, commit, pr_url, runtime, package_manager,
architecture, build_system, test_framework, linter.
"""

CODING_FRONTMATTER_KEYS = [
    "language",
    "languages",
    "repo_url",
    "framework",
    "branch",
    "commit",
    "pr_url",
    "runtime",
    "package_manager",
    "architecture",
    "build_system",
    "test_framework",
    "linter",
]

_SCHEMA_V2_KEYS = [
    "schema_version",
    "plugin",
    "domain",
    "project",
    "source_path",
    "file_type",
    "captured_date",
    "event_date",
    "event_date_source",
    "scan_type",
    "sources_read",
    "read_bytes",
    "content_confidence",
    "tags",
    "cssclasses",
]


def build_coding_frontmatter(event_data: dict) -> dict:
    """Merge coding-specific fields onto a schema v2 frontmatter base."""
    base = {k: event_data[k] for k in _SCHEMA_V2_KEYS if k in event_data}
    for key in CODING_FRONTMATTER_KEYS:
        if key in event_data:
            base[key] = event_data[key]
    return base


def validate_coding_frontmatter(frontmatter: dict) -> tuple[bool, list[str]]:
    """Returns (ok, error_list). Errors are human-readable strings."""
    errors = []
    if "language" in frontmatter and not isinstance(frontmatter["language"], str):
        errors.append("language must be a string")
    if "languages" in frontmatter and not isinstance(frontmatter["languages"], list):
        errors.append("languages must be a list")
    if "framework" in frontmatter and not isinstance(frontmatter["framework"], (str, type(None))):
        errors.append("framework must be a string or null")
    if "repo_url" in frontmatter and not isinstance(frontmatter["repo_url"], (str, type(None))):
        errors.append("repo_url must be a string or null")
    if "branch" in frontmatter and not isinstance(frontmatter["branch"], (str, type(None))):
        errors.append("branch must be a string or null")
    if "commit" in frontmatter and not isinstance(frontmatter["commit"], (str, type(None))):
        errors.append("commit must be a string or null")
    if "pr_url" in frontmatter and not isinstance(frontmatter["pr_url"], (str, type(None))):
        errors.append("pr_url must be a string or null")
    if "runtime" in frontmatter and not isinstance(frontmatter["runtime"], (str, type(None))):
        errors.append("runtime must be a string or null")
    if "package_manager" in frontmatter and not isinstance(frontmatter["package_manager"], (str, type(None))):
        errors.append("package_manager must be a string or null")
    if "architecture" in frontmatter and not isinstance(frontmatter["architecture"], (str, type(None))):
        errors.append("architecture must be a string or null")
    if "build_system" in frontmatter and not isinstance(frontmatter["build_system"], (str, type(None))):
        errors.append("build_system must be a string or null")
    if "test_framework" in frontmatter and not isinstance(frontmatter["test_framework"], (str, type(None))):
        errors.append("test_framework must be a string or null")
    if "linter" in frontmatter and not isinstance(frontmatter["linter"], (str, type(None))):
        errors.append("linter must be a string or null")
    return (len(errors) == 0, errors)
