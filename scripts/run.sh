#!/usr/bin/env bash
# Wrapper: finds the right Python and runs the scraper

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SCRIPT="$PLUGIN_DIR/impressum_scraper.py"
REQ="$PLUGIN_DIR/requirements.txt"

# Find Python with gspread available
PYTHON=$(which python3 2>/dev/null)
if ! "$PYTHON" -c "import gspread" 2>/dev/null; then
  PYTHON=$(find ~/.pyenv/versions -name "python3" -path "*/bin/python3" 2>/dev/null | sort -V | tail -1)
fi

if [[ -z "$PYTHON" ]]; then
  echo "ERROR: Python 3 not found. Run: /impressum setup"
  exit 1
fi

SUBCOMMAND="${1:-}"

case "$SUBCOMMAND" in
  setup)
    echo "Installing dependencies..."
    "$PYTHON" -m pip install -r "$REQ"
    echo ""
    echo "Checking env vars..."
    COUNT=$(grep -c "CLOUDFLARE_ACCOUNT_ID\|CLOUDFLARE_API_TOKEN" ~/.env 2>/dev/null || echo 0)
    if [[ "$COUNT" -lt 2 ]]; then
      echo "WARNING: Add CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_API_TOKEN to ~/.env"
    else
      echo "OK: Cloudflare credentials found"
    fi
    "$PYTHON" -c "from claude_agent_sdk import query; print('OK: Claude SDK available')" 2>/dev/null || echo "INFO: Claude SDK not available (ICP scoring disabled)"
    echo "Setup complete."
    ;;
  status)
    echo "=== Impressum LeadGen Status ==="
    echo "Python: $("$PYTHON" --version)"
    echo "Script: $SCRIPT"
    echo ""
    echo "Dependencies:"
    "$PYTHON" -m pip show gspread requests beautifulsoup4 python-dotenv 2>&1 | grep -E "^(Name|Version)"
    echo ""
    echo "Cloudflare env vars: $(grep -c "CLOUDFLARE_ACCOUNT_ID\|CLOUDFLARE_API_TOKEN" ~/.env 2>/dev/null || echo 0)/2 found"
    "$PYTHON" -c "from claude_agent_sdk import query; print('Claude SDK: OK')" 2>/dev/null || echo "Claude SDK: not available"
    ;;
  scan)
    shift
    if [[ -z "$1" ]]; then
      echo "Usage: /impressum scan <csv-path-or-google-sheets-url> [--limit N] [--workers N] [--no-claude]"
      exit 1
    fi
    "$PYTHON" "$SCRIPT" "$@"
    ;;
  *)
    echo "Usage: /impressum <scan|setup|status>"
    echo ""
    echo "  scan <source>   Scrape URLs from CSV or Google Sheet"
    echo "  setup           Install Python dependencies"
    echo "  status          Check environment"
    ;;
esac
