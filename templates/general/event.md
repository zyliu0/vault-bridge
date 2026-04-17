---
schema_version: 2
plugin: vault-bridge
domain: <% tp.file.cursor(1) %>
project: <% tp.file.cursor(2) %>
source_path: <% tp.file.cursor(3) %>
file_type: <% tp.file.cursor(4) %>
captured_date: <% tp.date.now("YYYY-MM-DD") %>
event_date: <% tp.date.now("YYYY-MM-DD") %>
event_date_source: captured-date
scan_type: manual
sources_read: []
read_bytes: 0
content_confidence: high
event_type: <% tp.file.cursor(5) %>
tags: []
cssclasses: []
---

> [!abstract] Summary
> <% tp.file.cursor(6) %>

## Details

<% tp.file.cursor(7) %>

## Outcomes

<% tp.file.cursor(8) %>
