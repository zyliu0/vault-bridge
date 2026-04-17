"""Google Calendar sync helpers for vault-bridge heartbeat-scan.

Pure utility helpers — no MCP tool calls. MCP invocation happens directly
in heartbeat-scan.md Step 5b via the mcp__claude_ai_Google_Calendar__create_event
tool. This module provides payload formatting and config helpers only.

Python 3.9 compatible.
"""
from typing import Optional, Tuple

from config import Config  # noqa: E402


def format_all_day_event(event_date: str) -> Tuple[str, str]:
    """Return (start, end) datetime strings for an all-day Google Calendar event.

    Args:
        event_date: ISO date string in YYYY-MM-DD format.

    Returns:
        A tuple (start_datetime, end_datetime) using Google's all-day event
        format: YYYY-MM-DDT00:00:00 and YYYY-MM-DDT23:59:59.
        If event_date is not a valid ISO date, returns it unchanged.
    """
    if len(event_date) == 10 and event_date[4] == "-" and event_date[7] == "-":
        return (f"{event_date}T00:00:00", f"{event_date}T23:59:59")
    # Fallback: return as-is (not a valid ISO date)
    return (event_date, event_date)


def should_sync(config: Config, domain_name: str) -> bool:
    """Return True if calendar_sync is enabled for the named domain.

    Checks domain.calendar_sync. Returns False if the domain is not found
    or if the calendar_sync field is absent / False.
    """
    for domain in config.domains:
        if domain.name == domain_name:
            return bool(domain.calendar_sync)
    return False


def build_event_description(note_path: str, source_path: str) -> str:
    """Build the description field for a calendar event.

    Args:
        note_path:   Vault path of the note, e.g. "2408 Project/SD/2024-09-09 review.md"
        source_path: Archive source path, e.g. "/archive/2408 Project/Meetings/review.pdf"

    Returns:
        A description string with note_path on the first line and source_path on the second.
    """
    return f"Note: {note_path}\nSource: {source_path}"
