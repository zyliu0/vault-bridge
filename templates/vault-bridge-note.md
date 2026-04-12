---
schema_version: 1
plugin: vault-bridge
project: <% tp.file.cursor(1) %>
source_path: <% tp.file.cursor(2) %>
file_type: <% tp.file.cursor(3) %>
captured_date: <% tp.date.now("YYYY-MM-DD") %>
event_date: <% tp.file.cursor(4) %>
event_date_source: filename-prefix
scan_type: manual
sources_read: []
read_bytes: 0
content_confidence: metadata-only
cssclasses: []
---

<% tp.file.cursor(5) %>

NAS: `<% tp.file.cursor(6) %>`
