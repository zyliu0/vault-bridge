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
software: <% tp.file.cursor(5) %>
session_goals: <% tp.file.cursor(6) %>
tags: [photography, edit]
cssclasses: []
---

> [!abstract] Summary
> <% tp.file.cursor(7) %>

## Session Goals

{{session_goals}}

## Edits Made

<% tp.file.cursor(8) %>

## Export Specs

| Setting | Value |
|---------|-------|
| Format | JPEG |
| Quality |       |
| Size |        |

## Before / After

<% tp.file.cursor(9) %>
