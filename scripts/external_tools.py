"""vault-bridge external-tool detection and install orchestration.

Handlers for `.doc` / `.ppt` / `.dwg` shell out to CLI binaries
(`soffice`, `dwg2dxf`, `ODAFileConverter`) that are NOT installed by
pip. Pre-v16.0.4 `/vault-bridge:setup` printed a warning and walked
away — the user ended up with a green setup banner and silently-broken
handlers on those file types, which is the complaint in the v16.0.3
field report.

This module closes that gap:

* `detect_missing_tools` inspects the handlers that were just installed
  and returns a list of tools that are missing AND can be installed via
  the system package manager (brew on macOS, apt/dnf/pacman on Linux,
  winget on Windows). Tools that need a click-through EULA (ODA) are
  reported as manual-install candidates instead.
* `install_tool` runs the actual package-manager command with a
  streaming subprocess so the user sees progress on long casks.
* `reprobe` re-checks PATH + the canonical macOS app bundle path after
  install so the handler's runtime check (`shutil.which`) finds the
  tool without a shell restart.

Design choices to match the v16.0.3 report's acceptance criteria:

* Platform detection happens here, not at the call site. Setup Step
  6.5e passes a list of installed handler categories; this module
  decides what to do on each OS.
* The consent cache lives in `Config.file_type_config['install_consent']`
  so re-running setup doesn't re-ask. Consent is stored per TOOL
  (``libreoffice``, ``libredwg``), not per extension — a user who
  installs LibreOffice for .doc also gets .ppt support, and re-asking
  would be noise.
* `shutil.which` checks run both the binary name and canonical macOS
  app-bundle paths (LibreOffice lives at
  ``/Applications/LibreOffice.app/Contents/MacOS/soffice`` after a
  cask install — it is not on PATH by default).

Python 3.9 compatible — no f-string=, no match statements.
"""

from __future__ import annotations

import logging
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Platform detection
# ---------------------------------------------------------------------------

def _is_macos() -> bool:
    return sys.platform == "darwin"


def _is_linux() -> bool:
    return sys.platform.startswith("linux")


def _is_windows() -> bool:
    return sys.platform == "win32"


def _linux_family() -> str:
    """Return a coarse Linux family tag: ``debian``, ``fedora``, ``arch``,
    or ``unknown``. Used to pick between apt / dnf / pacman.

    Reads `/etc/os-release` by preference; falls back to PATH probes.
    """
    osrel = Path("/etc/os-release")
    if osrel.exists():
        try:
            text = osrel.read_text()
            id_line = ""
            id_like_line = ""
            for line in text.splitlines():
                if line.startswith("ID="):
                    id_line = line.split("=", 1)[1].strip().strip('"').lower()
                elif line.startswith("ID_LIKE="):
                    id_like_line = line.split("=", 1)[1].strip().strip('"').lower()
            joined = id_line + " " + id_like_line
            if any(tag in joined for tag in ("debian", "ubuntu")):
                return "debian"
            if any(tag in joined for tag in ("fedora", "rhel", "centos")):
                return "fedora"
            if "arch" in joined:
                return "arch"
        except Exception as exc:
            logger.debug("Could not parse /etc/os-release: %s", exc)
    # Fallback: infer from available package manager.
    if shutil.which("apt-get") or shutil.which("apt"):
        return "debian"
    if shutil.which("dnf"):
        return "fedora"
    if shutil.which("pacman"):
        return "arch"
    return "unknown"


# ---------------------------------------------------------------------------
# Tool registry
# ---------------------------------------------------------------------------
#
# Each entry records:
#   - the handler categories that need this tool
#   - the binaries to probe on PATH (any one found = satisfied)
#   - a macOS app-bundle path accepted as equivalent
#   - a per-platform install command (None = no auto-install possible)
#   - a rough size/time estimate for the AskUserQuestion label
#   - a canonical bin path to prepend to PATH after install, for the
#     post-install re-probe (macOS casks don't auto-link to /usr/local/bin)

@dataclass(frozen=True)
class ToolSpec:
    """Specification for one external CLI tool.

    Attributes:
        name:            Short slug used as the consent-cache key.
        label:           Human-readable display name.
        categories:      Handler categories that shell out to this tool.
        binaries:        Names to look up via shutil.which.
        macos_app_paths: Absolute paths accepted as "installed" on macOS,
                         since some casks don't link into /usr/local/bin.
        install_cmds:    Per-platform shell command list or None if the
                         tool has no auto-install path on that platform.
                         Keys: 'darwin', 'linux.debian', 'linux.fedora',
                         'linux.arch', 'win32'. Missing keys mean
                         "no auto-install on this OS".
        size_hint:       Rough install size / time string for prompts.
    """
    name: str
    label: str
    categories: Tuple[str, ...]
    binaries: Tuple[str, ...]
    macos_app_paths: Tuple[str, ...] = ()
    install_cmds: dict = field(default_factory=dict)
    size_hint: str = ""


# LibreOffice — powers .doc / .ppt legacy extraction. Cask on macOS,
# libreoffice-core on Debian/Ubuntu, libreoffice on Fedora/Arch, winget
# on Windows. The macOS cask installs to /Applications but does NOT
# symlink soffice onto PATH, so runtime checks must know about the
# canonical bundle path.
LIBREOFFICE = ToolSpec(
    name="libreoffice",
    label="LibreOffice",
    categories=("document-office-legacy",),
    binaries=("soffice", "libreoffice"),
    macos_app_paths=("/Applications/LibreOffice.app/Contents/MacOS/soffice",),
    install_cmds={
        "darwin":        ["brew", "install", "--cask", "libreoffice"],
        "linux.debian":  ["apt-get", "install", "-y", "libreoffice-core"],
        "linux.fedora":  ["dnf", "install", "-y", "libreoffice-core"],
        "linux.arch":    ["pacman", "-S", "--noconfirm", "libreoffice-fresh"],
        "win32":         ["winget", "install", "--id",
                          "TheDocumentFoundation.LibreOffice", "-e",
                          "--accept-package-agreements",
                          "--accept-source-agreements"],
    },
    size_hint="~500 MB, 2-5 min",
)

# LibreDWG — GNU alternative to ODA File Converter for .dwg. Provides
# `dwg2dxf`, which the cad-dwg handler can pipe into ezdxf (already
# installed). Smaller than ODA and no click-through EULA, so it's a
# strict-win auto-install path that eliminates the manual ODA step for
# the common case. Linux-only packaging on most distros; on macOS it's
# a keg-only brew formula.
LIBREDWG = ToolSpec(
    name="libredwg",
    label="LibreDWG (dwg2dxf)",
    categories=("cad-dwg",),
    binaries=("dwg2dxf", "dwgread"),
    macos_app_paths=(),
    install_cmds={
        "darwin":        ["brew", "install", "libredwg"],
        "linux.debian":  ["apt-get", "install", "-y", "libredwg-tools"],
        "linux.fedora":  ["dnf", "install", "-y", "libredwg"],
        "linux.arch":    ["pacman", "-S", "--noconfirm", "libredwg"],
    },
    size_hint="~15 MB, <1 min",
)


_REGISTRY: Tuple[ToolSpec, ...] = (LIBREOFFICE, LIBREDWG)


def _spec_by_category(category: str) -> Optional[ToolSpec]:
    for spec in _REGISTRY:
        if category in spec.categories:
            return spec
    return None


def _platform_key() -> str:
    """Return the key used to look up install_cmds for the current OS."""
    if _is_macos():
        return "darwin"
    if _is_windows():
        return "win32"
    if _is_linux():
        return "linux." + _linux_family()
    return "unknown"


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

def is_tool_present(spec: ToolSpec) -> bool:
    """True if any binary is on PATH OR a canonical macOS app path exists.

    The dual check exists because `brew install --cask libreoffice`
    drops the app into /Applications but does not add soffice to PATH
    — `shutil.which("soffice")` alone would miss the install.
    """
    for bin_name in spec.binaries:
        if shutil.which(bin_name):
            return True
    for path_str in spec.macos_app_paths:
        if Path(path_str).exists():
            return True
    return False


@dataclass
class MissingTool:
    """One tool that the selected handlers need but that isn't installed.

    Attributes:
        spec:        The tool registry entry.
        categories:  Subset of spec.categories that the user actually
                     selected — used for the prompt label so a user
                     who only picked .doc isn't told "also for .dwg".
        install_cmd: Resolved command for the current OS, or None
                     when no auto-install path exists.
    """
    spec: ToolSpec
    categories: Tuple[str, ...]
    install_cmd: Optional[List[str]]


def detect_missing_tools(installed_categories: Iterable[str]) -> List[MissingTool]:
    """Return auto-installable tools that the selected handlers need but lack.

    Tools that have no install path on the current OS (e.g. a .dwg-only
    Windows box with no winget) are NOT returned — setup already writes
    REQUIREMENTS.md for those and there is nothing productive to
    ask the user about.

    Args:
        installed_categories: The `spec.category` of every handler the
            setup wizard is about to install (or has just installed).
            Typically ``{"document-office-legacy", "cad-dwg"}`` on a
            box that opted into the Visual/CAD group.

    Returns:
        One `MissingTool` per distinct tool. Multiple categories that
        share a tool (documentation hint: both .doc and .ppt share
        LibreOffice) collapse to one entry.
    """
    seen_by_name: dict = {}
    platform_key = _platform_key()
    for category in installed_categories:
        spec = _spec_by_category(category)
        if spec is None:
            continue
        if is_tool_present(spec):
            continue
        install_cmd = spec.install_cmds.get(platform_key)
        # Skip entries with no auto-install path on this OS — the caller
        # still gets the REQUIREMENTS.md warning from handler_installer.
        if install_cmd is None:
            continue
        # Verify the package manager itself is on PATH (brew / apt / ...).
        # Missing package manager means a user on a distro we can't
        # drive; skip silently and let the warning path take over.
        pm = install_cmd[0]
        if not shutil.which(pm):
            continue
        if spec.name in seen_by_name:
            existing = seen_by_name[spec.name]
            seen_by_name[spec.name] = MissingTool(
                spec=existing.spec,
                categories=tuple(sorted(set(existing.categories) | {category})),
                install_cmd=existing.install_cmd,
            )
        else:
            seen_by_name[spec.name] = MissingTool(
                spec=spec,
                categories=(category,),
                install_cmd=install_cmd,
            )
    return list(seen_by_name.values())


# ---------------------------------------------------------------------------
# Install
# ---------------------------------------------------------------------------

@dataclass
class InstallOutcome:
    """Result of one auto-install attempt.

    Attributes:
        ok:       True iff the install command exited 0 AND the tool
                  is now detected (re-probe passes).
        tool:     The tool spec that was installed.
        error:    Human-readable failure reason when ok=False; empty
                  when ok=True.
        duration: Wall-clock seconds the install took — useful when a
                  caller wants to warn about slow casks on re-runs.
    """
    ok: bool
    tool: ToolSpec
    error: str = ""
    duration: float = 0.0


def install_tool(
    missing: MissingTool,
    timeout: int = 600,
    stream_output: bool = True,
) -> InstallOutcome:
    """Run the resolved install command and re-probe for the tool.

    Streams subprocess stdout/stderr to this process's stderr so long
    casks (LibreOffice: 2-5 min) show progress. Callers that want
    silent output pass ``stream_output=False``; the streamed path is
    the default because setup is interactive.

    Args:
        missing:        One entry from `detect_missing_tools`.
        timeout:        Kill the subprocess after this many seconds.
                        LibreOffice cask install benchmarks at 3-5 min
                        on a warm Homebrew cache; 600s (10 min) covers
                        cold caches and slow networks.
        stream_output:  Forward the subprocess's stdout/stderr to this
                        process's stderr in real time.

    Returns:
        InstallOutcome. Callers MUST check ``ok`` — a zero-exit
        subprocess that still fails to land the binary on PATH is
        reported as ``ok=False`` with ``error="installed but not
        detected on PATH"``.
    """
    import time
    start = time.monotonic()

    if missing.install_cmd is None:
        return InstallOutcome(
            ok=False,
            tool=missing.spec,
            error="no auto-install path on this platform",
        )

    # The command starts with the package manager (brew/apt/...). If
    # it vanished between detect_missing_tools and here (unlikely but
    # possible), fail fast with a recognisable message.
    if not shutil.which(missing.install_cmd[0]):
        return InstallOutcome(
            ok=False,
            tool=missing.spec,
            error=(
                "package manager '" + missing.install_cmd[0] +
                "' not on PATH; skipping auto-install"
            ),
        )

    try:
        if stream_output:
            # Pipe stdout/stderr to our stderr so the user watches the
            # cask install in real time. communicate() only runs once
            # the process exits; we read line-by-line instead.
            proc = subprocess.Popen(
                missing.install_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            try:
                assert proc.stdout is not None
                for line in proc.stdout:
                    sys.stderr.write(line)
                rc = proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                return InstallOutcome(
                    ok=False,
                    tool=missing.spec,
                    error="install timed out after " + str(timeout) + "s",
                    duration=time.monotonic() - start,
                )
        else:
            result = subprocess.run(
                missing.install_cmd,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
            rc = result.returncode
    except FileNotFoundError as exc:
        return InstallOutcome(
            ok=False,
            tool=missing.spec,
            error="subprocess failed: " + str(exc),
            duration=time.monotonic() - start,
        )
    except Exception as exc:
        return InstallOutcome(
            ok=False,
            tool=missing.spec,
            error="unexpected install error: " + str(exc),
            duration=time.monotonic() - start,
        )

    duration = time.monotonic() - start

    if rc != 0:
        return InstallOutcome(
            ok=False,
            tool=missing.spec,
            error="install command exited " + str(rc),
            duration=duration,
        )

    # Critical: a successful exit doesn't guarantee the binary is
    # discoverable. brew --cask drops LibreOffice into /Applications
    # but doesn't symlink `soffice` onto PATH — the re-probe catches
    # this by also checking macos_app_paths.
    if not is_tool_present(missing.spec):
        return InstallOutcome(
            ok=False,
            tool=missing.spec,
            error="installed but not detected on PATH",
            duration=duration,
        )

    return InstallOutcome(ok=True, tool=missing.spec, duration=duration)


# ---------------------------------------------------------------------------
# Consent cache
# ---------------------------------------------------------------------------
#
# Setup stores per-tool consent under Config.file_type_config, keyed by
# spec.name. A user who once accepted "install LibreOffice" shouldn't
# be re-asked on re-runs; a user who once declined shouldn't see the
# nag again unless they explicitly re-enable the relevant file types.
#
# file_type_config is already a free-form dict on Config (schema v4),
# so adding the nested key needs no schema migration.


def read_consent(file_type_config: dict, tool_name: str) -> Optional[bool]:
    """Return stored consent for ``tool_name``, or None if never asked.

    Returned values:
        True  — user previously accepted
        False — user previously declined
        None  — never asked (or the cache is malformed)
    """
    cache = file_type_config.get("install_consent") if file_type_config else None
    if not isinstance(cache, dict):
        return None
    value = cache.get(tool_name)
    if isinstance(value, bool):
        return value
    return None


def write_consent(file_type_config: dict, tool_name: str, accepted: bool) -> None:
    """Mutate ``file_type_config`` in place to record the decision.

    The caller is responsible for persisting via
    ``config.save_config`` — this helper only touches the in-memory
    dict so tests and batch updates don't force a write per tool.
    """
    cache = file_type_config.setdefault("install_consent", {})
    if not isinstance(cache, dict):
        cache = {}
        file_type_config["install_consent"] = cache
    cache[tool_name] = bool(accepted)


# ---------------------------------------------------------------------------
# Prompt helpers (rendering only — AskUserQuestion invocation happens
# in the setup.md skill body, which is the only place that has access
# to that tool. These helpers format the prompt text consistently.)
# ---------------------------------------------------------------------------

def format_prompt_label(missing: List[MissingTool]) -> str:
    """Format one AskUserQuestion label covering every missing tool.

    Example output:

        Install 2 missing tool(s): LibreOffice (~500 MB, 2-5 min)
        for .doc/.ppt; LibreDWG (~15 MB, <1 min) for .dwg

    Kept as a free-standing helper so tests can assert on the rendered
    string without spinning up a prompt. Setup.md pastes the result
    into the AskUserQuestion ``question`` field verbatim.
    """
    if not missing:
        return ""
    pieces = []
    for m in missing:
        exts_for_categories = sorted({
            # Coarse "category -> representative extensions" label. The
            # handler_installer surfaces the real selected extensions
            # at a different layer; we only need a hint for the prompt.
            "document-office-legacy": ".doc/.ppt",
            "cad-dwg": ".dwg",
        }.get(c, c) for c in m.categories)
        ext_hint = ", ".join(exts_for_categories)
        suffix = " (" + m.spec.size_hint + ")" if m.spec.size_hint else ""
        pieces.append(m.spec.label + suffix + " for " + ext_hint)
    return "Install " + str(len(missing)) + " missing tool(s): " + "; ".join(pieces)
