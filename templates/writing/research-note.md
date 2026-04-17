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
source_type: <% tp.file.cursor(6) %>
credibility: <% tp.file.cursor(7) %>
tags: [writing, research]
cssclasses: []
---

> [!abstract] Summary
> <% tp.file.cursor(8) %>

## Key Findings

<% tp.file.cursor(9) %>

## Quotes

> <% tp.file.cursor(10) %>

## How This Changes the Draft

<% tp.file.cursor(11) %>

## Follow-up Questions

<% tp.file.cursor(12) %>

---

*Source type: {{source_type}} | Credibility: {{credibility}}*
