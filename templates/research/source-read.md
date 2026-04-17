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
author: <% tp.file.cursor(7) %>
publication: <% tp.file.cursor(8) %>
year: <% tp.file.cursor(9) %>
doi: <% tp.file.cursor(10) %>
tags: [research, source]
cssclasses: []
---

> [!abstract] Summary
> <% tp.file.cursor(11) %>

## Abstract

<% tp.file.cursor(12) %>

## Key Claims

<% tp.file.cursor(13) %>

## ==Important Findings==

<% tp.file.cursor(14) %>

## Methodology

<% tp.file.cursor(15) %>

## Limitations

<% tp.file.cursor(16) %>

## How This Relates

<% tp.file.cursor(17) %>

---

*Source: {{author}} ({{year}}) — {{publication}}*
