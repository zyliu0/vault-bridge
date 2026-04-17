---
schema_version: 2
plugin: vault-bridge
domain: <% tp.file.cursor(1) %>
project: <% tp.file.cursor(2) %>
source_path: <% tp.file.cursor(3) %>
file_type: md
captured_date: <% tp.date.now("YYYY-MM-DD") %>
event_date: <% tp.date.now("YYYY-MM-DD") %>
event_date_source: captured-date
scan_type: manual
sources_read: []
read_bytes: 0
content_confidence: high
source_title: <% tp.file.cursor(4) %>
page_range: <% tp.file.cursor(5) %>
tags: [research, annotation]
cssclasses: []
---

# Annotation: {{source_title}}

**Source:** {{source_title}}
**Pages:** {{page_range}}
**Date:** <% tp.date.now("YYYY-MM-DD") %>

## Chapter / Section

<% tp.file.cursor(6) %>

## Highlights

| Page | Highlight | Note |
|------|-----------|------|
|      |           |      |

## Marginalia

<% tp.file.cursor(7) %>

## Questions

<% tp.file.cursor(8) %>

## Connections

<% tp.file.cursor(9) %>
