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
content_confidence: metadata-only
version: <% tp.file.cursor(4) %>
release_date: <% tp.date.now("YYYY-MM-DD") %>
prereqs: <% tp.file.cursor(5) %>
breaking_changes: <% tp.file.cursor(6) %>
tags: [coding, release]
cssclasses: []
---

# Release: v{{version}}

**Date:** {{release_date}}
**Status:** Released

## What's New

<% tp.file.cursor(7) %>

## Breaking Changes

<% tp.file.cursor(8) %>

## Bug Fixes

<% tp.file.cursor(9) %>

## Under the Hood

<% tp.file.cursor(10) %>

## Upgrade Notes

{{prereqs}}

## Contributors

<% tp.file.cursor(11) %>
