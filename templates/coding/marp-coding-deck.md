---
marp: true
paginate: true
theme: base
style: |
  section {
    --accent: #4a7c7c;
    --accent-light: #e8f4f4;
    background-color: #fafaf8;
    color: #2c3e50;
    font-family: 'Inter', system-ui, sans-serif;
  }
  h1 { color: #1a2f38; }
  h2 { color: #4a7c7c; }
  h3 { color: #3d6b6b; }
  code { background: #f0f0f0; border-radius: 4px; padding: 2px 6px; }
  pre { background: #1e2d38; color: #e8e8e8; border-radius: 8px; padding: 1rem 1.25rem; }
  pre code { background: transparent; padding: 0; color: inherit; }
  blockquote { border-left: 4px solid #4a7c7c; background: #e8f4f4; padding: 0.5rem 1rem; margin: 0; }
  table { border-collapse: collapse; width: 100%; }
  th { background: #4a7c7c; color: white; padding: 0.5rem 1rem; text-align: left; }
  td { padding: 0.5rem 1rem; border-bottom: 1px solid #e0e0e0; }
  tr:nth-child(even) { background: #f5f5f4; }
  footer { color: #888; font-size: 0.6em; }
  section.title { text-align: center; }
  section.title h1 { font-size: 2.5em; margin-bottom: 0.5rem; }
  section.title h2 { color: #888; font-weight: normal; font-size: 1.2em; }
---

<!-- _class: lead -->

# <!-- fit --> Project Overview

<!-- Insert title and subtitle here -->

---

## Agenda

<!-- columns 1/3 2/3 -->
<!-- left -->

1. Topic one
2. Topic two
3. Topic three

<!-- right -->

- Sub-point A
- Sub-point B
- Sub-point C

---

## <% tp.file.cursor(1) %>

<!-- Use columns for 1/3-2/3 or 2/3-1/3 split -->

<!-- columns 2/3 -->
<!-- left -->

### Context

<% tp.file.cursor(2) %>

Code context:

```python
# Representative code
def example():
    pass
```

<!-- right -->

Notes or supplementary detail here

---

## Decision: <% tp.file.cursor(3) %>

> [!quote]
> The key reason for this choice was...

<!-- columns 1/3 2/3 -->
<!-- left -->

**Trade-offs**

- Pro: ...
- Con: ...

<!-- right -->

```
$ command output
result
```

---

## Architecture

<!-- columns 1/3 2/3 -->
<!-- left -->

Component overview

- Service A
- Service B
- Service C

<!-- right -->

```text
┌─────────┐     ┌─────────┐
│   A     │────▶│   B     │
└─────────┘     └─────────┘
      │              │
      ▼              ▼
┌─────────┐     ┌─────────┐
│   C     │     │   D     │
└─────────┘     └─────────┘
```

---

## Action Items

| Action | Owner | Status |
|--------|-------|--------|
|        |       | Open   |
|        |       | Open   |

---

## Summary

<!-- 2/3 width, centered -->

- Key point one
- Key point two
- Next step

---

<!-- _class: lead -->

# Thank you

Questions?

<!-- Insert contact / repo info -->
