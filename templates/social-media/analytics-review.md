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
platform: <% tp.file.cursor(4) %>
period: <% tp.file.cursor(5) %>
followers_before: <% tp.file.cursor(6) %>
followers_after: <% tp.file.cursor(7) %>
tags: [content-creation, analytics]
cssclasses: []
---

# Analytics Review: {{platform}}

**Period:** {{period}}
**Followers:** {{followers_before}} → {{followers_after}}
**Date:** <% tp.date.now("YYYY-MM-DD") %>

## Metrics

| Metric | Value | Change |
|---------|-------|--------|
| Impressions |       |        |
| Engagement |       |        |
| Clicks |            |        |
| Shares |            |        |

## Top Content

| Content | Metric | Value |
|---------|--------|-------|
|         |        |       |

## Insights

<% tp.file.cursor(8) %>

## Recommendations

<% tp.file.cursor(9) %>
