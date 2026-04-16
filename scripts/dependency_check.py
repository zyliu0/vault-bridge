#!/usr/bin/env python3
"""Dependency checks for vault-bridge.

Detects what's installed and reports what's missing. vault-bridge cannot
install other Claude Code plugins or skills — it can only check and guide.

Hard requirements:
  - obsidian CLI (vault writes won't work without it)
  - Python packages from requirements.txt

Recommended Claude Code skills (optional but improve hand-editing):
  - obsidian-cli (for manual obsidian CLI guidance)
  - obsidian-markdown (for manual Obsidian-flavored markdown guidance)
  - obsidian-bases (for manual Bases file guidance)
"""
import importlib
import shutil
import subprocess
import sys


REQUIRED_CLIS = [
    {
        "name": "defuddle",
        "purpose": "clean HTML extraction for /vault-bridge:research",
        "install": "npm install -g defuddle",
    },
]

REQUIRED_PYTHON_PACKAGES = [
    ("yaml", "PyYAML"),
    ("PIL", "Pillow"),
    ("PyPDF2", "PyPDF2"),
    ("docx", "python-docx"),
    ("pptx", "python-pptx"),
]

RECOMMENDED_SKILLS = [
    {
        "name": "obsidian-cli",
        "purpose": "Reference for obsidian CLI commands when hand-editing notes",
        "source": "obsidian-skills marketplace",
    },
    {
        "name": "obsidian-markdown",
        "purpose": "Obsidian-flavored markdown syntax for hand-edited notes",
        "source": "obsidian-skills marketplace",
    },
    {
        "name": "obsidian-bases",
        "purpose": "Obsidian Bases (.base) file authoring guidance",
        "source": "obsidian-skills marketplace",
    },
    {
        "name": "obsidian-visual-skills:obsidian-canvas-creator",
        "purpose": "Generate Obsidian JSON Canvas files for viz command",
        "source": "obsidian-visual-skills marketplace",
    },
    {
        "name": "obsidian-visual-skills:excalidraw-diagram",
        "purpose": "Generate Excalidraw diagrams as Obsidian markdown for viz command",
        "source": "obsidian-visual-skills marketplace",
    },
    {
        "name": "marp-slide",
        "purpose": "Generate Marp presentation decks for viz command",
        "source": "marp-slide marketplace",
    },
    {
        "name": "obsidian-skills:defuddle",
        "purpose": "Documents defuddle CLI usage for /vault-bridge:research",
        "source": "obsidian-skills marketplace",
    },
]


def check_required_clis() -> dict:
    """Check that required external CLI tools are on PATH."""
    results = []
    missing = []
    for cli in REQUIRED_CLIS:
        available = shutil.which(cli["name"]) is not None
        results.append({
            "name": cli["name"],
            "purpose": cli["purpose"],
            "install": cli["install"],
            "available": available,
        })
        if not available:
            missing.append(cli["name"])
    return {
        "name": "Required CLIs",
        "required": True,
        "clis": results,
        "missing": missing,
        "install_hint": (
            "; ".join(
                f"Run: {c['install']}"
                for c in REQUIRED_CLIS
                if c["name"] in missing
            )
        ) if missing else None,
    }


def _run_command(cmd: list, timeout: int = 5):
    """Run a command and return (returncode, stdout, stderr)."""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return (result.returncode, result.stdout, result.stderr)
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return (1, "", str(e))


def check_obsidian_cli() -> dict:
    """Check if the obsidian CLI is available."""
    code, stdout, stderr = _run_command(["obsidian", "help"])
    available = code == 0
    return {
        "name": "Obsidian CLI",
        "available": available,
        "required": True,
        "install_hint": (
            "Install the Obsidian CLI from https://help.obsidian.md/cli "
            "and ensure Obsidian is running."
        ) if not available else None,
    }


def check_python_packages() -> dict:
    """Check that required Python packages are importable."""
    packages = []
    missing = []
    for module_name, package_name in REQUIRED_PYTHON_PACKAGES:
        try:
            importlib.import_module(module_name)
            packages.append({"package": package_name, "available": True})
        except ImportError:
            packages.append({"package": package_name, "available": False})
            missing.append(package_name)

    return {
        "name": "Python packages",
        "required": True,
        "packages": packages,
        "missing": missing,
        "install_hint": (
            f"Run: pip install {' '.join(missing)}"
        ) if missing else None,
    }


def check_recommended_skills() -> dict:
    """List recommended Claude Code skills.

    We cannot programmatically detect installed skills from outside Claude
    Code, so we just list what's recommended and let the user verify.
    """
    return {
        "name": "Recommended Claude Code skills",
        "required": False,
        "skills": RECOMMENDED_SKILLS,
        "install_hint": (
            "These skills are optional. They help when manually editing notes "
            "in Obsidian. Install via:\n"
            "  claude plugin marketplace add github.com/obsidian-skills/obsidian-skills\n"
            "  claude plugin install obsidian-skills@obsidian-skills"
        ),
    }


def check_all() -> dict:
    """Run all dependency checks and return a combined result."""
    obsidian = check_obsidian_cli()
    pkgs = check_python_packages()
    clis = check_required_clis()
    skills = check_recommended_skills()

    # Overall OK if all required deps are present
    ok = obsidian["available"] and not pkgs["missing"] and not clis["missing"]

    return {
        "ok": ok,
        "obsidian_cli": obsidian,
        "python_packages": pkgs,
        "required_clis": clis,
        "recommended_skills": skills,
    }


def format_report(result: dict) -> str:
    """Format a check_all() result as a human-readable report."""
    lines = []
    lines.append("vault-bridge dependency check")
    lines.append("=" * 30)
    lines.append("")

    # Obsidian CLI
    obs = result["obsidian_cli"]
    status = "OK" if obs["available"] else "MISSING"
    lines.append(f"[{status}] Obsidian CLI (required)")
    if not obs["available"] and obs.get("install_hint"):
        lines.append(f"    -> {obs['install_hint']}")

    # Python packages
    pkgs = result["python_packages"]
    if pkgs.get("missing"):
        lines.append(f"[MISSING] Python packages: {', '.join(pkgs['missing'])}")
        if pkgs.get("install_hint"):
            lines.append(f"    -> {pkgs['install_hint']}")
    else:
        lines.append("[OK] Python packages")

    # Required CLIs
    clis = result.get("required_clis", {})
    if clis.get("missing"):
        lines.append(f"[MISSING] Required CLIs: {', '.join(clis['missing'])}")
        if clis.get("install_hint"):
            lines.append(f"    -> {clis['install_hint']}")
    else:
        lines.append("[OK] Required CLIs")

    # Recommended skills
    lines.append("")
    lines.append("Recommended Claude Code skills (optional):")
    rec = result.get("recommended_skills", {})
    for s in rec.get("skills", []):
        lines.append(f"  - {s['name']} — {s['purpose']}")
    if rec.get("install_hint"):
        lines.append(f"  Install hint: {rec['install_hint']}")

    lines.append("")
    if result["ok"]:
        lines.append("All required dependencies present.")
    else:
        lines.append("Some required dependencies are missing. See above.")

    return "\n".join(lines)


if __name__ == "__main__":
    result = check_all()
    print(format_report(result))
    sys.exit(0 if result["ok"] else 2)
