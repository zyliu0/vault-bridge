#!/usr/bin/env python3
"""Structured prompt builder for vault-bridge user interactions.

Builds structured prompt specs that commands consume via AskUserQuestion.
These functions return dicts — they do NOT call AskUserQuestion themselves,
keeping the Python scripts testable without mocking Claude Code tools.
"""


def build_domain_selection_prompt(
    candidates: list,
    source_path: str,
    default: str = None,
) -> dict:
    """Build a domain selection prompt for ambiguous source paths.

    Args:
        candidates: List of domain dicts (must have 'name' and 'label').
        source_path: The source file path being processed.
        default: Optional pre-selected domain name.

    Returns a dict with question, options, and default.
    """
    options = []
    for c in candidates:
        label = c.get("label", c["name"])
        options.append({"label": f"{label} ({c['name']}/)", "value": c["name"]})

    options.append({"label": "Create new domain...", "value": "__new__"})

    result = {
        "question": f"Which domain does this file belong to?\n  {source_path}",
        "options": options,
    }
    if default:
        result["default"] = default

    return result


def build_project_selection_prompt(
    domain_name: str,
    existing_projects: list,
    suggested_name: str,
) -> dict:
    """Build a project selection prompt within a domain.

    Args:
        domain_name: The domain the project belongs to.
        existing_projects: List of existing project names in this domain.
        suggested_name: A suggested name for a new project.

    Returns a dict with question and options.
    """
    options = []
    for p in existing_projects:
        options.append({"label": p, "value": p})

    if suggested_name and suggested_name not in existing_projects:
        options.append({"label": f"{suggested_name} (new)", "value": suggested_name})

    result = {
        "question": f"Which project in '{domain_name}' does this belong to?",
        "options": options,
    }
    if suggested_name:
        result["default"] = suggested_name

    return result


def build_subfolder_confirmation_prompt(
    suggested: str,
    alternatives: list,
) -> dict:
    """Build a subfolder confirmation prompt when routing is uncertain.

    Args:
        suggested: The auto-detected subfolder.
        alternatives: Other possible subfolders.

    Returns a dict with question, options, and default.
    """
    all_folders = [suggested] + [a for a in alternatives if a != suggested]
    options = [{"label": f, "value": f} for f in all_folders]

    return {
        "question": f"Route this event to which subfolder? (suggested: {suggested})",
        "options": options,
        "default": suggested,
    }
