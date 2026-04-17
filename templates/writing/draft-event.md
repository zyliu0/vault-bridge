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
word_count: <% tp.file.cursor(5) %>
draft_number: <% tp.file.cursor(6) %>
genre: <% tp.file.cursor(7) %>
tags: [writing, draft]
cssclasses: []
---

> [!abstract] Summary
> <% tp.file.cursor(8) %>

## Plot / Argument

<% tp.file.cursor(9) %>

## Characters / Points

<% tp.file.cursor(10) %>

## Scenes / Sections

<% tp.file.cursor(11) %>

## Feedback Needed

<% tp.file.cursor(12) %>

---

**Word count:** {{word_count}} | **Draft:** {{draft_number}}
