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
interviewee: <% tp.file.cursor(4) %>
interviewer: <% tp.file.cursor(5) %>
duration_minutes: <% tp.file.cursor(6) %>
format: <% tp.file.cursor(7) %>
tags: [writing, interview]
cssclasses: []
---

# Interview: {{interviewee}}

**Date:** <% tp.date.now("YYYY-MM-DD") %>
**Interviewer:** {{interviewer}}
**Duration:** {{duration_minutes}} min
**Format:** {{format}}

## Context

<% tp.file.cursor(8) %>

## Questions Asked

### <% tp.file.cursor(9) %>

**Q:** <% tp.file.cursor(10) %>

**A:** <% tp.file.cursor(11) %>

## Key Quotes

> <% tp.file.cursor(12) %>

## Post-Interview Notes

<% tp.file.cursor(13) %>
