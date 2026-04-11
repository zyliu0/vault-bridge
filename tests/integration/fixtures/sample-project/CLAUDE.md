# Test fixture vault CLAUDE.md

This fixture is used by tests/integration/test_pipeline.py to exercise the
full vault-bridge Python pipeline end-to-end. It is NOT a production config.

## vault-bridge: configuration

```yaml
version: 1

file_system:
  type: local-path
  root_path: /tmp/vault-bridge-fixture-source
  access_pattern: "Use Read and Glob tools for all file reads."

routing:
  patterns:
    - match: "CD"
      subfolder: CD
    - match: "SD"
      subfolder: SD
    - match: "Meeting"
      subfolder: Meetings
  content_overrides:
    - when: "filename contains one of ['meeting', 'memo']"
      subfolder: Meetings
  fallback: Admin

skip_patterns:
  - ".DS_Store"
  - "Thumbs.db"
  - "*.tmp"

style:
  note_filename_pattern: "YYYY-MM-DD topic.md"
  writing_voice: first-person-diary
  summary_word_count: [100, 200]
```
