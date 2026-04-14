#!/bin/bash
# vault-bridge local config health check hook.
# Runs before Bash tool invocations to catch config issues early.
# Auto-repairs what it can, reports what it can't.

PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"

python3 -c "
import sys, os
sys.path.insert(0, os.path.join('${PLUGIN_ROOT}', 'scripts'))
import local_config

workdir = os.getcwd()
remaining = local_config.health_check_and_repair(workdir)
if remaining:
    for e in remaining:
        print(f'vault-bridge config warning: {e}', file=sys.stderr)
" 2>&1

# Always exit 0 — warnings only, never block the command
exit 0
