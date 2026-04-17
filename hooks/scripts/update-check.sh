#!/bin/bash
PLUGIN_ROOT="${CLAUDE_PLUGIN_ROOT:-$(cd "$(dirname "$0")/../.." && pwd)}"
python3 "${PLUGIN_ROOT}/scripts/update_check.py" --plugin-root "${PLUGIN_ROOT}"
exit 0
