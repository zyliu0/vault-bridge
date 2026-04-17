---
schema_version: 2
plugin: vault-bridge
domain: <% tp.file.cursor(1) %>
project: <% tp.file.cursor(2) %>
source_path: <% tp.file.cursor(3) %>
file_type: md
captured_date: <% tp.date.now("YYYY-MM-DD") %>
event_date: <% tp.file.cursor(4) %>
event_date_source: filename-prefix
scan_type: manual
sources_read: []
read_bytes: 0
content_confidence: high
repo_url: <% tp.file.cursor(5) %>
pr_url: <% tp.file.cursor(6) %>
branch: <% tp.file.cursor(7) %>
language: <% tp.file.cursor(8) %>
tags: [coding, code-review]
cssclasses: []
---

> [!abstract] Summary
> <% tp.file.cursor(9) %>

## PR Overview

- **PR:** <% tp.file.cursor(10) %>
- **Branch:** <% tp.file.cursor(11) %>
- **Author:** <% tp.file.cursor(12) %>
- **Reviewers:** <% tp.file.cursor(13) %>

## Changes

<% tp.file.cursor(14) %>

## Discussion

<% tp.file.cursor(15) %>

## Decisions

<% tp.file.cursor(16) %>

## Action Items

<% tp.file.cursor(17) %>
