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
content_confidence: metadata-only
ci_system: <% tp.file.cursor(5) %>
pipeline: <% tp.file.cursor(6) %>
branch: <% tp.file.cursor(7) %>
commit: <% tp.file.cursor(8) %>
duration_sec: <% tp.file.cursor(9) %>
status: <% tp.file.cursor(10) %>
tags: [coding, ci-cd]
cssclasses: []
---

# CI Run: {{pipeline}}

**System:** {{ci_system}}
**Branch:** {{branch}}
**Commit:** `{{commit}}`
**Duration:** {{duration_sec}}s
**Status:** {{status}}
**Date:** <% tp.date.now("YYYY-MM-DD") %>

## Pipeline Stages

| Stage | Status | Duration |
|-------|--------|----------|
|       |        |          |

## Failures

<% tp.file.cursor(11) %>

## Artifacts

<% tp.file.cursor(12) %>

## Logs

<% tp.file.cursor(13) %>
