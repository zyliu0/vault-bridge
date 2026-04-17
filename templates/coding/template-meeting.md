---
schema_version: 2
plugin: vault-bridge
domain: <% tp.file.cursor(1) %>
project: <% tp.file.cursor(2) %>
source_path: null
file_type: md
captured_date: <% tp.date.now("YYYY-MM-DD") %>
event_date: <% tp.date.now("YYYY-MM-DD") %>
event_date_source: captured-date
scan_type: manual
sources_read: []
read_bytes: 0
content_confidence: metadata-only
tags: [coding, meeting]
cssclasses: []
---

# Meeting: <% tp.file.cursor(3) %>

**Date:** <% tp.date.now("YYYY-MM-DD") %>
**Project:** {{project}}

## Attendees

-
-
-

## Agenda

1.
2.
3.

## Discussion

### <% tp.file.cursor(4) %>

<% tp.file.cursor(5) %>

## Decisions

| Decision | Rationale | Owner |
|----------|-----------|-------|
|          |           |       |

## Action Items

| Action | Owner | Status |
|--------|-------|--------|
|        |       | Open   |

## Notes

<% tp.file.cursor(6) %>
