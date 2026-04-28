---
name: impressum
description: "Impressum LeadGen: ICP-scored scraper for German B2B industrial companies. Subcommands: scan, setup, status."
---

The user invoked `/impressum`. Parse their subcommand and args, then immediately run:

```bash
RUN=$(find ~/.claude/plugins/cache -path "*/impressum-leadgen/*/scripts/run.sh" 2>/dev/null | head -1)
bash "$RUN" <subcommand> [args]
```

## Subcommands

**`/impressum scan <source> [options]`**
```bash
bash "$RUN" scan <source> [--limit N] [--workers N] [--no-claude] [--cloudflare] [--output-csv out.csv] [--credentials path/to/credentials.json]
```
- `source` = path to CSV file or Google Sheets URL
- After completion: show summary (total processed, High/Medium Fit count, top results)

**`/impressum setup`**
```bash
bash "$RUN" setup
```

**`/impressum status`**
```bash
bash "$RUN" status
```

If no subcommand given, ask the user: scan, setup, or status?
