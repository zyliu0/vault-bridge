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
tags: [coding, adr]
cssclasses: []
---

# ADR: <% tp.file.cursor(4) %>

**Status:** <% tp.file.cursor(5) %>

**Date:** <% tp.date.now("YYYY-MM-DD") %>

**Context**

<% tp.file.cursor(6) %>

**Decision**

<% tp.file.cursor(7) %>

**Consequences**

<% tp.file.cursor(8) %>

<!-- Links -->
<!-- Related ADRs: -->
<!-- Superseded by: -->
