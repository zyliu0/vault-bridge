---
description: Validate your vault-bridge configuration
allowed-tools: Bash, Read
---

Validate the user's vault-bridge configuration and report the result.

## Step 1 — try loading the v2 config

```
python3 -c "
import sys, json
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
import setup_config
config = setup_config.load_config()
print(json.dumps(config))
"
```

If exit 0 → parse the JSON output and go to Step 2.

If exit 2 (SetupNeeded) → try the advanced config path:
```
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/parse_config.py CLAUDE.md
```

If THAT also fails → print the stderr verbatim plus:
"No valid config found. Run /vault-bridge:setup to configure."

## Step 2 — report the result

Show a per-domain summary:

```
✓ vault-bridge config is valid.

Vault: {vault_name}
Domains: {N}

  [1] {domain.name} ({domain.label})
    archive:   {domain.archive_root}
    fs_type:   {domain.file_system_type}
    patterns:  {len(routing_patterns)} path-based rules
    overrides: {len(content_overrides)} content overrides
    fallback:  {domain.fallback}
    tags:      {domain.default_tags}

  [2] ...
```

Then: "Your vault-bridge configuration is ready. Run
`/vault-bridge:retro-scan <folder-path>` to scan a project folder."

If any domain has `file_system_type: nas-mcp`, verify the NAS is reachable:
```bash
mcp__nas__list_files path="{domain.archive_root}" limit=1
```
Report if the NAS is unreachable.
