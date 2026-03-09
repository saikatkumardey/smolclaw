#!/bin/bash
# Deploy smolclaw: reinstall and restart service.
set -e
cd "$(dirname "$0")"
uv tool uninstall smolclaw 2>/dev/null || true
uv tool install .
if systemctl is-active --quiet smolclaw 2>/dev/null; then
    systemctl restart smolclaw && echo "Service restarted."
elif systemctl cat smolclaw &>/dev/null 2>&1; then
    systemctl start smolclaw && echo "Service started."
else
    echo "No systemd service found. Run: smolclaw setup  (to generate the service file), then: smolclaw start"
fi
