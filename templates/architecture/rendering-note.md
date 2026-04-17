---
schema_version: 2
plugin: vault-bridge
domain: <% tp.file.cursor(1) %>
project: <% tp.file.cursor(2) %>
source_path: <% tp.file.cursor(3) %>
file_type: <% tp.file.cursor(4) %>
captured_date: <% tp.date.now("YYYY-MM-DD") %>
event_date: <% tp.file.cursor(5) %>
event_date_source: filename-prefix
scan_type: manual
sources_read: []
read_bytes: 0
content_confidence: high
render_type: <% tp.file.cursor(6) %>
software: <% tp.file.cursor(7) %>
tags: [architecture, rendering]
cssclasses: []
---

> [!quote] Brief
> <% tp.file.cursor(8) %>

## Render Details

- **Type:** {{render_type}}
- **Software:** {{software}}
- **Date:** {{event_date}}

## Revisions

| Revision | Date | Changes |
|----------|------|---------|
| v1       |      |         |
| v2       |      |         |

## Notes

<% tp.file.cursor(9) %>
