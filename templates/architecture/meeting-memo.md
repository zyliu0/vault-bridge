---
schema_version: 2
plugin: vault-bridge
domain: <% tp.file.cursor(1) %>
project: <% tp.file.cursor(2) %>
source_path: <% tp.file.cursor(3) %>
file_type: md
captured_date: <% tp.date.now("YYYY-MM-DD") %>
event_date: <% tp.date.now("YYYY-MM-DD") %>
event_date_source: captured-date
scan_type: manual
sources_read: []
read_bytes: 0
content_confidence: metadata-only
meeting_type: <% tp.file.cursor(4) %>
attendees: <% tp.file.cursor(5) %>
location: <% tp.file.cursor(6) %>
tags: [architecture, meeting]
cssclasses: []
---

# Meeting: <% tp.file.cursor(7) %>

**Date:** <% tp.date.now("YYYY-MM-DD") %>
**Type:** {{meeting_type}}
**Attendees:** {{attendees}}
**Location:** {{location}}

## Agenda

1.
2.
3.

## Discussion

<% tp.file.cursor(8) %>

## Decisions

| Decision | Rationale | Owner |
|----------|-----------|-------|
|          |           |       |

## Action Items

| Action | Owner | Due | Status |
|--------|-------|-----|--------|
|        |       |     | Open   |
