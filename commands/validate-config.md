---
description: Validate your vault-bridge configuration
allowed-tools: Bash, Read
---

Validate the user's vault-bridge configuration and report the result.

## Step 0 — check for plugin updates

Run a non-blocking update check:

```bash
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from plugin_version import format_update_notice
notice = format_update_notice()
if notice:
    print(f'NOTE: {notice}', file=sys.stderr)
"
```

## Step 1 — load config

```python
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from config import load_config, effective_for, SetupNeeded

try:
    cfg = load_config(Path.cwd())
except SetupNeeded as e:
    print(f'No valid config found: {e}')
    print('Run /vault-bridge:setup to configure.')
    import sys; sys.exit(1)
```

If this fails → print the error verbatim plus:
"No valid config found. Run /vault-bridge:setup to configure."

## Step 2 — report the result

Show a per-domain summary:

```
vault-bridge config is valid (schema v4).

Vault:       {cfg.vault_name}
Domains:     {len(cfg.domains)}
Active:      {cfg.active_domain or "(none — multi-domain, resolved per scan)"}

  [1] {domain.name} ({domain.label})
    archive:   {domain.archive_root}
    transport: {domain.transport or "(not configured — run /vault-bridge:build-transport)"}
    patterns:  {len(domain.routing_patterns)} path-based rules
    overrides: {len(domain.content_overrides)} content overrides
    fallback:  {domain.fallback}
    tags:      {domain.default_tags}

  [2] ...
```

Then: "Your vault-bridge configuration is ready (v4). Run
`/vault-bridge:retro-scan <folder-path>` to scan a project folder."

For any domain missing a transport, tell the user:
"Domain '{domain.name}' has no transport configured. Run
`/vault-bridge:build-transport --domain {domain.name}` to set one up."
