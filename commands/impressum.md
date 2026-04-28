---
name: impressum
description: "Impressum LeadGen scraper. Usage: /impressum scan <csv-or-sheet> | /impressum setup | /impressum status"
---

Extract the subcommand and args from the user's message, then IMMEDIATELY run this single bash command without any thinking or planning:

```bash
bash "$(find ~/.claude/plugins/cache -path '*/impressum-leadgen/*/scripts/run.sh' 2>/dev/null | head -1)" <SUBCOMMAND> <ARGS>
```

Replace `<SUBCOMMAND>` with: `scan`, `setup`, or `status`
Replace `<ARGS>` with whatever the user provided (e.g. the CSV path or Google Sheets URL and any flags)

If no subcommand given, default to `status`.

Do not explain. Do not plan. Just run the bash command.
