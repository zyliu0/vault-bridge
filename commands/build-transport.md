---
description: Build or rebuild a transport (connection) module for the current workdir
allowed-tools:
  - Read
  - Bash
  - Write
  - Edit
  - AskUserQuestion
argument-hint: "[--domain DOMAIN_NAME] [--slug SLUG]"
---

# /vault-bridge:build-transport

Build a new transport module for the current working directory, or rebuild an
existing one. Invokes the `transport-builder` skill interactively.

---

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

## Parse arguments

Extract `--domain` and `--slug` from the argument string, if present.

```python
import sys
args = "$ARGUMENTS".split()
domain_arg = None
slug_arg = None
for i, a in enumerate(args):
    if a == "--domain" and i + 1 < len(args):
        domain_arg = args[i + 1]
    if a == "--slug" and i + 1 < len(args):
        slug_arg = args[i + 1]
```

---

## Check existing transports

```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from transport_registry import list_transports
transports = list_transports(Path('.'))
print('existing:', [t['name'] for t in transports])
"
```

If existing transports exist and `--slug` is not provided, AskUserQuestion:
> "You already have transport(s): {names}. What do you want to do?"
Options:
1. "Build a brand-new transport"
2. "Rebuild an existing transport — replace it"
3. "Cancel"

For option 2, ask which slug to replace.

---

## Invoke the transport-builder skill

Pass `slug_arg` as the initial slug suggestion (Step 4 of the skill).

The skill handles the full interview, code generation, validation, registration,
and testing.

---

## Bind to domain after skill completes

After the skill returns a registered `slug`:

If `domain_arg` is provided:
```bash
python3 -c "
import sys; sys.path.insert(0, 'scripts')
from pathlib import Path
from config import config_bind_transport
config_bind_transport(Path('.'), '$domain_arg', '$slug')
print('Bound $slug to domain $domain_arg')
"
```

Otherwise, the skill's Step 10 handles domain binding interactively.

---

## Offer capability probe

AskUserQuestion:
> "Would you like to run a capability probe for this transport now?"

Options:
1. "Yes — test connectivity and list a sample of the archive"
2. "No — I'll test later with /vault-bridge:setup"

If "Yes", run `setup_probe.run_probe()` for the domain bound to this transport.

---

## Done

Report:
- Transport slug registered
- Domain(s) bound
- Probe result (or skipped)
