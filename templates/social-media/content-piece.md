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
platform: <% tp.file.cursor(5) %>
content_type: <% tp.file.cursor(6) %>
word_count: <% tp.file.cursor(7) %>
publish_date: <% tp.file.cursor(8) %>
tags: [content-creation, social-media]
cssclasses: []
---

> [!abstract] Summary
> <% tp.file.cursor(9) %>

## Concept

<% tp.file.cursor(10) %>

## Script / Content

<% tp.file.cursor(11) %>

## Hook

<% tp.file.cursor(12) %>

## Call to Action

<% tp.file.cursor(13) %>

## Tags / Hashtags

<% tp.file.cursor(14) %>

---

*Platform: {{platform}} | Type: {{content_type}} | Status: Draft*
