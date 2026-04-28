# impressum-leadgen

Claude Code plugin zum Scrapen deutscher Impressum-Seiten mit automatischem ICP-Scoring für B2B-Industrieunternehmen.

## Was es macht

1. **ICP-Filter** — Analysiert jede Firmen-Homepage mit Claude und bewertet sie auf einer Skala 0–100, ob sie zum Ziel-ICP passt (Maschinenbau, Messtechnik, Anlagenbau, Präzisionstechnik)
2. **Impressum-Scraping** — Extrahiert Kontaktdaten (Geschäftsführer, Telefon, E-Mail) nur für Unternehmen mit ICP Score ≥ 40
3. **Export** — Schreibt Ergebnisse live in Google Sheets oder CSV

## Installation

```bash
/plugin marketplace add Tomadm21/impressum-leadgen
/plugin install impressum-leadgen@Tomadm21-impressum-leadgen
```

Danach Claude Code neu starten.

## Setup

```bash
/impressum setup
```

Prüft und installiert Python-Dependencies (`gspread`, `beautifulsoup4`, `requests`, `python-dotenv`).

**Benötigte Env-Variablen in `~/.env`:**
```
CLOUDFLARE_ACCOUNT_ID=...
CLOUDFLARE_API_TOKEN=...
```

Für Google Sheets zusätzlich eine Service Account `credentials.json` — siehe [Google Sheets API Setup](https://docs.gspread.org/en/latest/oauth2.html).

## Verwendung

### CSV scannen
```
/impressum scan ~/meine-urls.csv
```

### Google Sheet scannen
```
/impressum scan https://docs.google.com/spreadsheets/d/... --credentials ~/credentials.json
```

### Nur erste 50 URLs testen
```
/impressum scan ~/urls.csv --limit 50 --workers 3
```

### Status prüfen
```
/impressum status
```

## ICP-Scoring

| Score | Fit | Bedeutung |
|-------|-----|-----------|
| ≥ 70 | High Fit | Hersteller mit klarem Industrie-/Technikbezug, B2B |
| 40–69 | Medium Fit | Potenziell relevant — Kontaktdaten werden extrahiert |
| < 40 | Low Fit | Ausgeschlossen — Impressum wird nicht gescrapt |

**Sofort-Ausschluss (Score = 0):** Agenturen, reine IT-Firmen, Handwerk, Online-Shops, Coaching

## Output-Spalten (Google Sheets ab Spalte H)

`icp_score` · `icp_fit` · `is_manufacturer` · `industry` · `target_group` · `icp_reason` · `impressum_url` · `geschaeftsfuehrer` · `telefon` · `email` · `strasse` · `plz` · `ort` · `handelsregister` · `ust_idnr` · `status`

## Optionen

| Flag | Beschreibung | Default |
|------|-------------|---------|
| `--limit N` | Nur erste N URLs | alle |
| `--offset N` | Erste N URLs überspringen | 0 |
| `--workers N` | Parallele Worker | 5 |
| `--no-claude` | ICP-Analyse deaktivieren | — |
| `--cloudflare` | Cloudflare Browser Rendering für alle Requests | — |
| `--credentials PATH` | Google Service Account JSON | credentials.json |
| `--sheet-url-col N` | URL-Spalte im Sheet (1-indexed) | 6 |

## Anforderungen

- Python 3.11+
- Claude Code mit aktivem Account (für ICP-Analyse via Claude Agent SDK)
- Cloudflare Account mit Browser Rendering API
- Optional: Google Service Account für Sheets-Integration
