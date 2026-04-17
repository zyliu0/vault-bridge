#!/usr/bin/env python3
"""Structured prompt builder for vault-bridge user interactions.

Builds structured prompt specs that commands consume via AskUserQuestion.
These functions return dicts — they do NOT call AskUserQuestion themselves,
keeping the Python scripts testable without mocking Claude Code tools.

Python 3.9 compatible.
"""

# ---------------------------------------------------------------------------
# Template update prompts
# ---------------------------------------------------------------------------

def build_template_update_prompt(
    added_count: int,
    modified_count: int,
    deleted_count: int,
) -> dict:
    """Build an AskUserQuestion prompt for template update selection."""
    total = added_count + modified_count + deleted_count
    if total == 0:
        return None
    body = (
        f"vault-bridge detected {total} template change(s):\n"
        f"  • {added_count} new template(s)\n"
        f"  • {modified_count} updated template(s)\n"
        f"  • {deleted_count} removed template(s)\n\n"
        "Would you like to update your installed templates?"
    )
    return {
        "question": body,
        "header": "Template Update",
        "options": [
            {"label": "Update all templates", "description": "Install all new and updated templates"},
            {"label": "Review individually", "description": "Choose which templates to update one by one"},
            {"label": "Skip templates", "description": "Keep installed templates as-is for now"},
        ],
        "multi_select": False,
    }


def build_individual_template_prompt(templates: list[dict]) -> dict:
    """Build a multi-select prompt for individual template choices."""
    options = [
        {"label": t["relative_path"], "description": f"{t['status']}: {t.get('description', '')}"}
        for t in templates
    ]
    return {
        "question": "Select templates to update:",
        "header": "Select Templates",
        "options": options,
        "multi_select": True,
    }


# ---------------------------------------------------------------------------
# Domain resolution prompts
# ---------------------------------------------------------------------------

def build_domain_resolution_prompt(domains: list[str], source_path: str) -> dict:
    """Build a prompt for disambiguating which domain a source file belongs to."""
    options = [{"label": d, "description": f"Archive root for {d}"} for d in domains]
    return {
        "question": (
            f"Which domain should '{source_path}' belong to?\n"
            "The file matches multiple archive roots."
        ),
        "header": "Domain Selection",
        "options": options,
        "multi_select": False,
    }


# ---------------------------------------------------------------------------
# General confirmation prompts
# ---------------------------------------------------------------------------

def build_confirm_prompt(
    question: str,
    header: str = "Confirm",
    confirm_label: str = "Yes, proceed",
    cancel_label: str = "No, cancel",
) -> dict:
    """Build a simple yes/no confirmation prompt."""
    return {
        "question": question,
        "header": header,
        "options": [
            {"label": confirm_label, "description": ""},
            {"label": cancel_label, "description": ""},
        ],
        "multi_select": False,
    }


# ---------------------------------------------------------------------------
# Domain / project selection prompts (existing)
# ---------------------------------------------------------------------------

def build_domain_selection_prompt(
    candidates: list,
    source_path: str,
    default: str = None,
) -> dict:
    """Build a domain selection prompt for ambiguous source paths."""
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
    """Build a project selection prompt within a domain."""
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
    """Build a subfolder confirmation prompt when routing is uncertain."""
    all_folders = [suggested] + [a for a in alternatives if a != suggested]
    options = [{"label": f, "value": f} for f in all_folders]
    return {
        "question": f"Route this event to which subfolder? (suggested: {suggested})",
        "options": options,
        "default": suggested,
    }
