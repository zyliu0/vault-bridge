---
description: Check for vault-bridge updates and install new or changed templates.
allowed-tools: Read, Bash, AskUserQuestion
---

You are running the vault-bridge self-update command. Your job is to check
for plugin updates and, if any are found, offer to install new or changed
templates from the plugin template bank into the vault's
`_Templates/vault-bridge/` folder.

## Step 0 — check plugin version

Run the version check:

```bash
python3 -c "
import sys
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from plugin_version import (
    get_installed_version, get_git_sha,
    get_templates_installed, is_first_run, check_for_updates,
    format_update_notice
)
from template_bank import list_templates, get_template_diff, format_diff_summary

plugin_root = Path('${CLAUDE_PLUGIN_ROOT}')
installed_version = get_installed_version()
current_sha = get_git_sha(plugin_root)
templates_installed = get_templates_installed()
first_run = is_first_run()

print(f'installed_version={installed_version}')
print(f'current_sha={current_sha}')
print(f'first_run={first_run}')
print(f'templates_installed_count={len(templates_installed)}')

diff = get_template_diff(templates_installed)
print(f'added={len(diff.added)}')
print(f'modified={len(diff.modified)}')
print(f'deleted={len(diff.deleted)}')
print('---')
print(format_diff_summary(diff))
"
```

Read the output to determine:
- If `first_run=true` → this is a first installation, offer to install all templates
- If `added + modified + deleted > 0` → template changes detected
- If all zero → no template changes, but version may still differ

## Step 1 — ask whether to update templates

Present an AskUserQuestion to the user:

> "vault-bridge has detected template changes. What would you like to do?"
>
> Options:
> - "Update all templates" — install all new and updated templates
> - "Review individually" — choose which templates to update one by one
> - "Skip templates" — keep installed templates as-is

## Step 2 — if "Update all"

Run the installer for all changed templates:

```bash
python3 -c "
import sys, json
from pathlib import Path
sys.path.insert(0, '${CLAUDE_PLUGIN_ROOT}/scripts')
from template_bank import list_templates, get_template_diff
from template_installer import install_templates
from plugin_version import save_version, get_git_sha

plugin_root = Path('${CLAUDE_PLUGIN_ROOT}')

# Start from whatever was already recorded — we merge rather than
# overwrite so templates left untouched keep their existing marker.
from plugin_version import get_templates_installed
templates_installed = dict(get_templates_installed())

# Install all added + modified
diff = get_template_diff(templates_installed)
templates_to_install = [t.relative_path for t in diff.added + diff.modified]

result = None
if templates_to_install:
    result = install_templates(templates_to_install, plugin_root, dry_run=False)
    # v16.0.2: persist the real source-file hash for each just-installed
    # template so get_template_diff on the next run can self-verify.
    # Previously we stored the literal string "installed", which never
    # matched a SHA256 prefix — every template then reappeared as
    # "modified" forever.
    for p in result.installed:
        templates_installed[p] = result.hashes.get(p, '')
    for e in result.errors:
        print(f'ERROR: {e}', file=sys.stderr)

# Prune entries for templates no longer in the bank.
for p in diff.deleted:
    templates_installed.pop(p, None)

# Save new version
new_version = get_git_sha(plugin_root)
save_version(new_version, templates_installed)
installed_count = len(result.installed) if result else 0
error_count = len(result.errors) if result else 0
print(f'Installed {installed_count} template(s), {error_count} error(s)')
"
```

## Step 3 — if "Review individually"

Present a multi-select AskUserQuestion listing all changed templates:

> "Select which templates to install:"
>
> Options (one per changed template):
> - "+ {relative_path}" (for added templates)
> - "~ {relative_path}" (for modified templates)
> - "- {relative_path}" (for deleted templates)

Install only the selected templates using the same installer script above,
filtering to only the user-selected paths.

## Step 4 — report result

Print a summary:

```
vault-bridge self-update complete.

  Templates installed: {N}
  Templates skipped:    {M}
  Errors:               {E}

  Plugin version: {sha}
```

If there were errors, list them individually.
