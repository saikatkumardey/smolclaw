#!/usr/bin/env bash
set -e

REPO="https://github.com/saikatkumardey/smolclaw"

echo ""
echo "  SmolClaw installer"
echo ""

# Python 3.12+
if ! command -v python3 &>/dev/null; then
  echo "Error: python3 not found. Install Python 3.12+ and try again."
  exit 1
fi

PY_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
PY_MAJOR=$(echo "$PY_VERSION" | cut -d. -f1)
PY_MINOR=$(echo "$PY_VERSION" | cut -d. -f2)

if [ "$PY_MAJOR" -lt 3 ] || { [ "$PY_MAJOR" -eq 3 ] && [ "$PY_MINOR" -lt 12 ]; }; then
  echo "Error: Python 3.12+ required (found $PY_VERSION)."
  exit 1
fi

# Install uv if missing
if ! command -v uv &>/dev/null; then
  echo "→ Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

# Install smolclaw
echo "→ Installing smolclaw..."
uv tool install "git+$REPO" --force

# Ensure uv tool bin is on PATH
export PATH="$(uv tool dir)/../bin:$PATH"

echo ""
echo "→ Running setup..."
echo ""
smolclaw setup
