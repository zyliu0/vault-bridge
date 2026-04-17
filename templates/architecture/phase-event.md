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
phase: <% tp.file.cursor(6) %>
tags: [architecture, phase]
cssclasses: []
---

> [!abstract] Summary
> <% tp.file.cursor(7) %>

## Phase Deliverables

| Deliverable | Status | Notes |
|-------------|--------|-------|
| Document | ⬜ | |
| Drawing | ⬜ | |
| Model | ⬜ | |
| Approval | ⬜ | |

## Key Decisions

<% tp.file.cursor(8) %>

## Open Issues

<% tp.file.cursor(9) %>
