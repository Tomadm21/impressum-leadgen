---
name: impressum
description: "Impressum LeadGen: scrape company Impressum pages, ICP-score against industrial B2B target, export to Google Sheets or CSV. Subcommands: scan, setup, status."
---

The user has invoked the Impressum LeadGen plugin.

**First, locate the plugin scripts directory (works on any machine):**
```bash
SCRIPT=$(find ~/.claude/plugins/cache -path "*/impressum-leadgen/*/scripts/impressum_scraper.py" 2>/dev/null | head -1)
REQ=$(find ~/.claude/plugins/cache -path "*/impressum-leadgen/*/scripts/requirements.txt" 2>/dev/null | head -1)
```

Parse their subcommand:
- `/impressum scan <source> [options]` — Run the full pipeline (ICP filter + Impressum scrape)
- `/impressum setup` — Install Python dependencies
- `/impressum status` — Check environment (env vars, Python deps)

If no subcommand is given, ask: scan, setup, or status?

---

## /impressum scan

**Arguments:**
- `<source>` — Google Sheets URL (`https://docs.google.com/...`) or path to CSV file
- `--limit N` — Only process the first N URLs (default: all)
- `--workers N` — Parallel workers (default: 5)
- `--no-claude` — Disable Claude ICP analysis (faster, no filtering)
- `--cloudflare` — Force Cloudflare Browser Rendering for all requests
- `--offset N` — Skip first N URLs

**What to do:**

1. Confirm the source with the user if not provided.
2. Locate the script:
   ```bash
   SCRIPT=$(find ~/.claude/plugins/cache -path "*/impressum-leadgen/*/scripts/impressum_scraper.py" 2>/dev/null | head -1)
   ```
3. Build the command:
   ```bash
   python3 "$SCRIPT" <source> [flags]
   ```
   For CSV: add `--output-csv ergebnisse.csv` unless user specifies otherwise.
   For Google Sheets: requires `--credentials <path>` — ask user for credentials.json path if not provided.

4. Run the command using Bash. Stream output so the user sees progress.

5. When complete, summarize:
   - Total URLs processed
   - How many passed ICP filter (score ≥ 40)
   - How many had status "OK" (full extraction success)
   - Top 3 High Fit companies (if any)

---

## /impressum setup

1. Locate requirements:
   ```bash
   REQ=$(find ~/.claude/plugins/cache -path "*/impressum-leadgen/*/scripts/requirements.txt" 2>/dev/null | head -1)
   ```
2. Run: `pip install -r "$REQ"`
3. Check that `~/.env` contains `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN`:
   ```bash
   grep -c "CLOUDFLARE_ACCOUNT_ID\|CLOUDFLARE_API_TOKEN" ~/.env
   ```
   If count < 2: tell user to add them to `~/.env`.
4. Check Claude SDK: `python3 -c "from claude_agent_sdk import query; print('OK')" 2>&1`
5. Report setup status in a clean summary.

---

## /impressum status

1. Check Python version: `python3 --version`
2. Check key deps: `pip show gspread requests beautifulsoup4 python-dotenv 2>&1 | grep -E "^(Name|Version|not found)"`
3. Check env vars: `grep -c "CLOUDFLARE_ACCOUNT_ID\|CLOUDFLARE_API_TOKEN" ~/.env 2>/dev/null || echo "0"`
4. Check Claude SDK: `python3 -c "from claude_agent_sdk import query; print('OK')" 2>&1`
5. Show script location:
   ```bash
   find ~/.claude/plugins/cache -path "*/impressum-leadgen/*/scripts/impressum_scraper.py" 2>/dev/null | head -1
   ```
6. Report all findings in a clean table.
