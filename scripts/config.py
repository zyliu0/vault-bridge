#!/usr/bin/env python3
"""vault-bridge v4 config module.

Single source of truth for configuration in vault-bridge v6.0.0.

Config file location: <workdir>/.vault-bridge/config.json
Schema version: 4

Breaking changes from v3:
- Domain.file_system_type removed → Domain.transport (Optional[str])
- EffectiveConfig.file_system_type removed → EffectiveConfig.transport_name
- load_config rejects anything with schema_version != 4

Public API
----------
    load_config(workdir: Path) -> Config
    save_config(workdir: Path, config: Config) -> Path
    effective_for(config: Config, domain_name: Optional[str]) -> EffectiveConfig
    config_bind_transport(workdir: Path, domain_name: str, slug: str) -> None
    reports_dir(workdir: Path) -> Path

    Config           — top-level config dataclass
    Domain           — per-domain config dataclass
    ProjectOverrides — project-level routing overrides dataclass
    EffectiveConfig  — merged ready-to-use config
    SetupNeeded      — exception: config missing or schema mismatch

    BUILTIN_FABRICATION_STOPWORDS  — canonical stop-word list

Python 3.9 compatible — uses typing.Optional, List, Dict, etc.
"""
import json
import sys
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Make sibling scripts importable when run standalone
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from domain_templates import DOMAIN_TEMPLATES, get_domain_template  # noqa: E402


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_SUBDIR = ".vault-bridge"
CONFIG_FILENAME = "config.json"
REPORTS_DIRNAME = "reports"
SCHEMA_VERSION = 4


# ---------------------------------------------------------------------------
# Built-in fabrication stop-word list (single source of truth)
# ---------------------------------------------------------------------------

BUILTIN_FABRICATION_STOPWORDS: List[str] = [
    "pulled the back wall in",
    "the team",
    "[person] said",
    "the review came back",
    "half a storey",
    "40cm",
]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class SetupNeeded(Exception):
    """Raised when required configuration is missing, corrupt, or schema mismatch.

    Always points the user at /vault-bridge:setup.
    """


# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class Domain:
    name: str
    label: str
    template_seed: str
    archive_root: str
    transport: Optional[str] = None
    default_tags: List[str] = field(default_factory=list)
    fallback: str = "Inbox"
    style: Dict[str, Any] = field(default_factory=dict)
    routing_patterns: List[Dict[str, str]] = field(default_factory=list)
    content_overrides: List[Dict[str, str]] = field(default_factory=list)
    skip_patterns: List[str] = field(default_factory=list)
    calendar_sync: bool = False
    throughput_bps: Optional[float] = None

    @classmethod
    def from_dict(cls, d: dict) -> "Domain":
        raw_tput = d.get("throughput_bps")
        throughput_bps: Optional[float] = float(raw_tput) if raw_tput is not None else None
        return cls(
            name=d["name"],
            label=d.get("label", d["name"]),
            template_seed=d.get("template_seed", "general"),
            archive_root=d.get("archive_root", ""),
            transport=d.get("transport", None),
            default_tags=list(d.get("default_tags", [])),
            fallback=d.get("fallback", "Inbox"),
            style=dict(d.get("style", {})),
            routing_patterns=list(d.get("routing_patterns", [])),
            content_overrides=list(d.get("content_overrides", [])),
            skip_patterns=list(d.get("skip_patterns", [])),
            calendar_sync=bool(d.get("calendar_sync", False)),
            throughput_bps=throughput_bps,
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "template_seed": self.template_seed,
            "archive_root": self.archive_root,
            "transport": self.transport,
            "default_tags": list(self.default_tags),
            "fallback": self.fallback,
            "style": dict(self.style),
            "routing_patterns": list(self.routing_patterns),
            "content_overrides": list(self.content_overrides),
            "skip_patterns": list(self.skip_patterns),
            "calendar_sync": self.calendar_sync,
            "throughput_bps": self.throughput_bps,
        }

    def has_external_archive(self) -> bool:
        """True when this domain is backed by an external filesystem.

        Vault-only domains (no archive to scan — notes are authored directly
        via /vault-bridge:visualization, /vault-bridge:research, or manual
        entry) set `archive_root = ""` and skip transport binding entirely.
        Scan commands (retro-scan, heartbeat-scan) must skip these domains.
        """
        return bool(self.archive_root and self.archive_root.strip())


@dataclass
class ProjectOverrides:
    routing_patterns: List[Dict[str, str]] = field(default_factory=list)
    content_overrides: List[Dict[str, str]] = field(default_factory=list)
    skip_patterns: List[str] = field(default_factory=list)
    fallback: Optional[str] = None
    project_style: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "ProjectOverrides":
        return cls(
            routing_patterns=list(d.get("routing_patterns", [])),
            content_overrides=list(d.get("content_overrides", [])),
            skip_patterns=list(d.get("skip_patterns", [])),
            fallback=d.get("fallback", None),
            project_style=dict(d.get("project_style", {})),
        )

    def to_dict(self) -> dict:
        return {
            "routing_patterns": list(self.routing_patterns),
            "content_overrides": list(self.content_overrides),
            "skip_patterns": list(self.skip_patterns),
            "fallback": self.fallback,
            "project_style": dict(self.project_style),
        }


@dataclass
class Config:
    schema_version: int
    vault_name: str
    vault_path: Optional[str]
    created_at: Optional[str]
    fabrication_stopwords: List[str]
    global_style: Dict[str, Any]
    active_domain: Optional[str]
    domains: List[Domain]
    project_overrides: ProjectOverrides
    discovered_structure: Dict[str, Any]
    file_type_config: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, d: dict) -> "Config":
        domains = [Domain.from_dict(x) for x in d.get("domains", [])]
        po_raw = d.get("project_overrides", {})
        project_overrides = ProjectOverrides.from_dict(po_raw) if po_raw else ProjectOverrides()
        ds = d.get("discovered_structure", {"last_walked_at": None, "observed_subfolders": []})
        # file_type_config: default to {} when missing or null (backwards-compatible)
        raw_ftc = d.get("file_type_config")
        file_type_config = dict(raw_ftc) if raw_ftc else {}
        return cls(
            schema_version=d.get("schema_version", 0),
            vault_name=d.get("vault_name", ""),
            vault_path=d.get("vault_path", None),
            created_at=d.get("created_at", None),
            fabrication_stopwords=list(d.get("fabrication_stopwords", [])),
            global_style=dict(d.get("global_style", {})),
            active_domain=d.get("active_domain", None),
            domains=domains,
            project_overrides=project_overrides,
            discovered_structure=dict(ds),
            file_type_config=file_type_config,
        )

    def to_dict(self) -> dict:
        return {
            "schema_version": self.schema_version,
            "vault_name": self.vault_name,
            "vault_path": self.vault_path,
            "created_at": self.created_at,
            "fabrication_stopwords": list(self.fabrication_stopwords),
            "global_style": dict(self.global_style),
            "active_domain": self.active_domain,
            "domains": [d.to_dict() for d in self.domains],
            "project_overrides": self.project_overrides.to_dict(),
            "discovered_structure": dict(self.discovered_structure),
            "file_type_config": dict(self.file_type_config),
        }

    def transport_for(self, archive_path: str) -> Optional[str]:
        """Return the transport slug for the domain whose archive_root best matches.

        Uses longest-prefix matching against archive_path.
        Returns None if no domain matches.

        Args:
            archive_path: The archive-side file path.
        """
        best_root = ""
        best_transport: Optional[str] = None

        for domain in self.domains:
            root = domain.archive_root
            if not root:
                continue
            # Normalize: ensure root doesn't end with /
            norm_root = root.rstrip("/")
            if archive_path == norm_root or archive_path.startswith(norm_root + "/") or archive_path.startswith(norm_root):
                if len(norm_root) > len(best_root):
                    best_root = norm_root
                    best_transport = domain.transport

        return best_transport


@dataclass
class EffectiveConfig:
    """Merged, ready-to-use configuration for a single domain in a project.

    v4: transport_name replaces file_system_type.
    """
    vault_name: str
    domain_name: str
    archive_root: str
    transport_name: Optional[str]
    routing_patterns: List[Dict[str, str]] = field(default_factory=list)
    content_overrides: List[Dict[str, str]] = field(default_factory=list)
    skip_patterns: List[str] = field(default_factory=list)
    fallback: str = "Inbox"
    default_tags: List[str] = field(default_factory=list)
    style: Dict[str, Any] = field(default_factory=dict)
    fabrication_stopwords: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        """Return a flat dict that domain_router.route_event() can consume."""
        return {
            "name": self.domain_name,
            "vault_name": self.vault_name,
            "archive_root": self.archive_root,
            "transport_name": self.transport_name,
            "routing_patterns": list(self.routing_patterns),
            "content_overrides": list(self.content_overrides),
            "skip_patterns": list(self.skip_patterns),
            "fallback": self.fallback,
            "default_tags": list(self.default_tags),
            "style": dict(self.style),
            "fabrication_stopwords": list(self.fabrication_stopwords),
        }


# ---------------------------------------------------------------------------
# Internal merge helpers
# ---------------------------------------------------------------------------

def _merge_lists(base: list, override: list) -> list:
    """Concatenate; override (project) entries come FIRST."""
    return list(override) + list(base)


def _merge_scalars(base: Any, override: Any) -> Any:
    """Return override if not None, else base."""
    return override if override is not None else base


def _merge_dicts(base: dict, override: dict) -> dict:
    """Shallow-merge; override keys win."""
    merged = dict(base)
    merged.update(override)
    return merged


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------

def _config_dir(workdir: Path) -> Path:
    return Path(workdir) / CONFIG_SUBDIR


def _config_path(workdir: Path) -> Path:
    return _config_dir(workdir) / CONFIG_FILENAME


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reports_dir(workdir: Path) -> Path:
    """Return the project's reports directory. Creates it if missing."""
    path = _config_dir(workdir) / REPORTS_DIRNAME
    path.mkdir(parents=True, exist_ok=True)
    return path


def load_config(workdir: Path) -> Config:
    """Load and validate the v4 config from <workdir>/.vault-bridge/config.json.

    Raises SetupNeeded if:
    - The file is missing.
    - The JSON is corrupt.
    - The schema_version is not 4.
    """
    path = _config_path(workdir)
    if not path.exists():
        raise SetupNeeded(
            "vault-bridge is not configured in this working directory. "
            "Run /vault-bridge:setup to create a config.json."
        )

    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SetupNeeded(
            f"vault-bridge config at {path} has a JSON parse error: {exc}. "
            "Run /vault-bridge:setup to recreate it."
        ) from exc
    except OSError as exc:
        raise SetupNeeded(
            f"Cannot read vault-bridge config at {path}: {exc}. "
            "Run /vault-bridge:setup."
        ) from exc

    if not isinstance(data, dict):
        raise SetupNeeded(
            f"vault-bridge config at {path} is not a JSON object. "
            "Run /vault-bridge:setup."
        )

    schema_version = data.get("schema_version") or data.get("config_version")
    if schema_version != SCHEMA_VERSION:
        if schema_version == 3:
            raise SetupNeeded(
                f"schema v3 detected; run /vault-bridge:setup to migrate to v4. "
                "The config schema changed in v6.0.0 (transport replaces file_system_type)."
            )
        raise SetupNeeded(
            f"vault-bridge config has schema_version={schema_version!r}, "
            f"but only version {SCHEMA_VERSION} is accepted. "
            "Run /vault-bridge:setup to migrate and recreate your config."
        )

    return Config.from_dict(data)


def save_config(workdir: Path, config: Config) -> Path:
    """Write config to <workdir>/.vault-bridge/config.json.

    Auto-fills active_domain when there is exactly one domain and
    active_domain is None.

    Returns the path written.
    """
    active = config.active_domain
    if active is None and len(config.domains) == 1:
        active = config.domains[0].name

    d = config.to_dict()
    d["active_domain"] = active

    path = _config_path(workdir)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def effective_for(config: Config, domain_name: Optional[str]) -> EffectiveConfig:
    """Merge three tiers (template + domain + project_overrides) for domain_name.

    If domain_name is None:
    - Uses config.active_domain when set.
    - Raises ValueError when active_domain is also None and len(domains) > 1.
    - For single-domain with active_domain=None, uses the only domain.

    Raises:
        ValueError — if domain_name doesn't match any configured domain.
        ValueError — if domain_name is None and active_domain is None and >1 domains.
    """
    # Resolve domain_name
    resolved: Optional[str] = domain_name
    if resolved is None:
        if config.active_domain is not None:
            resolved = config.active_domain
        elif len(config.domains) == 1:
            resolved = config.domains[0].name
        else:
            raise ValueError(
                "active_domain is not set and there are multiple domains. "
                "Resolve the domain via domain_router.resolve_domain() before "
                "calling effective_for()."
            )

    # Find the domain
    domain: Optional[Domain] = None
    for d in config.domains:
        if d.name == resolved:
            domain = d
            break
    if domain is None:
        raise ValueError(
            f"Domain '{resolved}' not found in config. "
            f"Available: {[d.name for d in config.domains]}"
        )

    # Get template (may be empty dict if template_seed unknown)
    template: dict = {}
    if domain.template_seed and domain.template_seed in DOMAIN_TEMPLATES:
        template = get_domain_template(domain.template_seed)

    po = config.project_overrides

    # Lists: project first, then domain, then template
    merged_routing = _merge_lists(
        _merge_lists(
            template.get("routing_patterns", []),
            list(domain.routing_patterns),
        ),
        list(po.routing_patterns),
    )

    merged_content_overrides = _merge_lists(
        _merge_lists(
            template.get("content_overrides", []),
            list(domain.content_overrides),
        ),
        list(po.content_overrides),
    )

    merged_skip = _merge_lists(
        _merge_lists(
            template.get("skip_patterns", []),
            list(domain.skip_patterns),
        ),
        list(po.skip_patterns),
    )

    merged_tags = _merge_lists(
        template.get("default_tags", []),
        list(domain.default_tags),
    )

    # Scalars: project > domain > template
    template_fallback = template.get("fallback")
    merged_fallback = _merge_scalars(
        _merge_scalars(template_fallback, domain.fallback or None),
        po.fallback,
    ) or "Inbox"

    # Dicts (style): template < global_style < domain < project
    template_style = template.get("style", {})
    merged_style = _merge_dicts(
        _merge_dicts(
            _merge_dicts(
                _merge_dicts({}, template_style),
                dict(config.global_style),
            ),
            dict(domain.style),
        ),
        dict(po.project_style),
    )

    # fabrication_stopwords: builtins first, then user-added
    merged_stopwords = list(BUILTIN_FABRICATION_STOPWORDS) + list(config.fabrication_stopwords)

    return EffectiveConfig(
        vault_name=config.vault_name,
        domain_name=resolved,
        archive_root=domain.archive_root,
        transport_name=domain.transport,
        routing_patterns=merged_routing,
        content_overrides=merged_content_overrides,
        skip_patterns=merged_skip,
        fallback=merged_fallback,
        default_tags=merged_tags,
        style=merged_style,
        fabrication_stopwords=merged_stopwords,
    )


def config_bind_transport(workdir: Path, domain_name: str, slug: str) -> None:
    """Bind a transport slug to a domain and persist the config.

    Finds the domain by name, sets domain.transport = slug, and saves.

    Raises:
        SetupNeeded — if config.json is missing or invalid.
        ValueError  — if domain_name is not found.
    """
    config = load_config(workdir)

    # Find the domain
    target: Optional[Domain] = None
    for d in config.domains:
        if d.name == domain_name:
            target = d
            break

    if target is None:
        raise ValueError(
            f"Domain '{domain_name}' not found in config. "
            f"Available: {[d.name for d in config.domains]}"
        )

    target.transport = slug
    save_config(workdir, config)


# ---------------------------------------------------------------------------
# Module-level convenience
# ---------------------------------------------------------------------------

def config_path(workdir: Path) -> Path:
    """Return the config.json path for workdir."""
    return _config_path(workdir)


def local_dir(workdir: Path) -> Path:
    """Return the .vault-bridge/ dir for workdir."""
    return _config_dir(workdir)
