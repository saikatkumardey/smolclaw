#!/bin/bash
# Deploy smolclaw: reinstall and restart service.
set -e
cd "$(dirname "$0")"
uv tool uninstall smolclaw 2>/dev/null || true
uv tool install .
systemctl restart smolclaw 2>/dev/null && echo "Service restarted." || echo "No systemd service found. Run: smolclaw start"
