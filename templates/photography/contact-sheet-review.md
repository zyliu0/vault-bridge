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
content_confidence: metadata-only
film_stock: <% tp.file.cursor(6) %>
developer: <% tp.file.cursor(7) %>
scanner: <% tp.file.cursor(8) %>
tags: [photography, contact-sheet]
cssclasses: []
---

# Contact Sheet Review

**Film Stock:** {{film_stock}}
**Developer:** {{developer}}
**Scanner:** {{scanner}}
**Date:** {{event_date}}

## Picks

| Frame | Pick | Reason |
|-------|------|--------|
|       | ✅   |        |

## Rejects

| Frame | Reason |
|-------|--------|
|       |        |

## Notes

<% tp.file.cursor(9) %>
