#!/usr/bin/env python3
"""
Impressum Scraper — Extracts contact data from German website Impressum pages.

Uses Cloudflare Browser Rendering API for crawling and Google Sheets for I/O.
"""

import argparse
import asyncio
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urljoin, urlparse

import gspread
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from google.oauth2.service_account import Credentials

# Claude Agent SDK (uses local Claude Code CLI with user subscription)
try:
    from claude_agent_sdk import query, ClaudeAgentOptions
    CLAUDE_SDK_AVAILABLE = True
except ImportError:
    CLAUDE_SDK_AVAILABLE = False

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

IMPRESSUM_PATHS = [
    "/impressum",
    "/impressum/",
    "/impressum.html",
    "/imprint",
    "/imprint/",
    "/imprint.html",
    "/legal/impressum",
    "/de/impressum",
    "/kontakt/impressum",
    "/ueber-uns/impressum",
    "/about/impressum",
    "/info/impressum",
]

IMPRESSUM_LINK_PATTERNS = re.compile(
    r"impressum|imprint|legal\s*notice|rechtliche[s]?\s*hinweise",
    re.IGNORECASE,
)

RATE_LIMIT_SECONDS = 2.0

# Fields to extract
FIELDS = [
    "firma",
    "vorname",
    "nachname",
    "geschaeftsfuehrer",
    "telefon",
    "email",
    "strasse",
    "plz",
    "ort",
    "land",
    "handelsregister",
    "ust_idnr",
    "fax",
]

# ICP analysis fields (added before contact fields in output)
ICP_FIELDS = [
    "icp_score",
    "icp_fit",
    "is_manufacturer",
    "industry",
    "target_group",
    "icp_reason",
]

HEADER_ROW = [
    "Webseite (Input)",
    "Impressum URL",
    "ICP Score",
    "ICP Fit",
    "Hersteller",
    "Branche",
    "Zielgruppe",
    "ICP Begruendung",
    "Firma",
    "Vorname",
    "Nachname",
    "Geschaeftsfuehrer / Inhaber",
    "Telefon",
    "E-Mail",
    "Strasse",
    "PLZ",
    "Ort",
    "Land",
    "Handelsregister",
    "USt-IdNr",
    "Fax",
    "Status",
]


# ---------------------------------------------------------------------------
# Cloudflare Browser Rendering
# ---------------------------------------------------------------------------


HTTP_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "de-DE,de;q=0.9,en;q=0.5",
}


def simple_fetch_html(url: str) -> str | None:
    """Fetch HTML via simple HTTP GET (fast, no rate limits)."""
    try:
        resp = requests.get(url, headers=HTTP_HEADERS, timeout=15, allow_redirects=True)
        resp.raise_for_status()
        return resp.text
    except requests.RequestException:
        return None


def cf_fetch_html(url: str, account_id: str, api_token: str, max_retries: int = 1) -> str | None:
    """Fetch rendered HTML from a URL via Cloudflare Browser Rendering with retry on 429."""
    endpoint = (
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}"
        f"/browser-rendering/content"
    )
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    payload = {"url": url}

    for attempt in range(max_retries):
        try:
            resp = requests.post(endpoint, headers=headers, json=payload, timeout=60)
            if resp.status_code == 429:
                wait = 5 * (attempt + 1)
                print(f"  [RATE LIMIT] Warte {wait}s vor erneutem Versuch...")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            if data.get("success"):
                return data["result"]
        except requests.RequestException as e:
            if "429" not in str(e):
                print(f"  [WARN] Cloudflare Fehler fuer {url}: {e}")
                return None
            wait = 5 * (attempt + 1)
            print(f"  [RATE LIMIT] Warte {wait}s vor erneutem Versuch...")
            time.sleep(wait)
    print(f"  [WARN] Alle Versuche fehlgeschlagen fuer {url}")
    return None


def fetch_html(url: str, account_id: str, api_token: str, use_cloudflare: bool = False) -> str | None:
    """Fetch HTML — try simple HTTP first, fall back to Cloudflare if needed."""
    if use_cloudflare:
        return cf_fetch_html(url, account_id, api_token)
    # Fast path: simple HTTP
    html = simple_fetch_html(url)
    if html:
        return html
    # Fallback: Cloudflare for JS-rendered pages
    print(f"  [FALLBACK] Cloudflare fuer: {url}")
    return cf_fetch_html(url, account_id, api_token)


# ---------------------------------------------------------------------------
# Impressum Discovery
# ---------------------------------------------------------------------------


def find_impressum_url(base_url: str, account_id: str, api_token: str, use_cf: bool = False) -> tuple[str | None, str | None]:
    """Find Impressum URL by scanning homepage first, then trying direct paths.

    Returns (impressum_url, homepage_html) — homepage_html is returned so we
    don't need to re-fetch if Impressum is on the same domain.
    """
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"

    # 1) Scan homepage for Impressum link (most efficient — 1 request)
    print(f"  Scanne Homepage nach Impressum-Link: {base_url}")
    html = fetch_html(base_url, account_id, api_token, use_cf)
    if html:
        soup = BeautifulSoup(html, "html.parser")
        candidates = []
        for a_tag in soup.find_all("a", href=True):
            link_text = a_tag.get_text(strip=True).lower()
            href = a_tag["href"]
            href_lower = href.lower()
            if IMPRESSUM_LINK_PATTERNS.search(link_text) or IMPRESSUM_LINK_PATTERNS.search(href_lower):
                full_url = urljoin(base_url, href)
                candidates.append(full_url)
        if candidates:
            # Prefer German-language links
            de_links = [c for c in candidates if "/de" in c.lower() or "impressum" in c.lower()]
            chosen = de_links[0] if de_links else candidates[0]
            print(f"  Impressum-Link gefunden: {chosen}")
            return chosen, html

    # 2) Fallback: try ONE direct path (simple HTTP only, no Cloudflare retries)
    candidate = base + "/impressum"
    page_html = simple_fetch_html(candidate)
    if page_html and not _is_error_page(page_html):
        return candidate, page_html

    return None, None


async def _claude_extract(impressum_text: str) -> dict:
    """Use Claude (via local Claude Code subscription) to extract structured Impressum data."""
    prompt = f"""Extrahiere die Kontaktdaten aus diesem deutschen Impressum-Text und antworte AUSSCHLIESSLICH mit JSON.

Felder (leerer String wenn nicht gefunden):
- firma: Firmenname inkl. Rechtsform (z.B. "Beckhoff Automation GmbH & Co. KG")
- vorname: Vorname des Geschaeftsfuehrers/Inhabers (nur eine Person, die erste genannte)
- nachname: Nachname (ohne akademische Titel wie Dr., Dipl.-Ing.)
- geschaeftsfuehrer: vollstaendiger Name mit Titel (z.B. "Dr. Hans Mueller")
- telefon: Telefonnummer im Originalformat
- email: E-Mail-Adresse
- strasse: Strasse und Hausnummer
- plz: 5-stellige Postleitzahl
- ort: Stadt/Ort
- land: "Deutschland", "Oesterreich" oder "Schweiz"
- handelsregister: z.B. "Amtsgericht Hamburg HRB 12345"
- ust_idnr: USt-IdNr im Format "DE123456789"
- fax: Faxnummer

Impressum-Text:
{impressum_text[:8000]}

Antworte nur mit validem JSON, kein Markdown, kein Code-Block, keine Erklaerungen."""

    options = ClaudeAgentOptions(
        system_prompt="Du extrahierst strukturierte Daten aus deutschen Impressum-Texten. Antworte ausschliesslich mit validem JSON.",
        max_turns=1,
        allowed_tools=[],
    )

    response_text = ""
    async for msg in query(prompt=prompt, options=options):
        if hasattr(msg, "content"):
            for block in msg.content:
                if hasattr(block, "text"):
                    response_text += block.text

    # Extract JSON from response
    match = re.search(r"\{[\s\S]*\}", response_text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


# Limit concurrent Claude SDK subprocesses (they are heavy)
_claude_semaphore = threading.BoundedSemaphore(3)


def claude_extract_sync(impressum_text: str) -> dict:
    """Sync wrapper for Claude extraction with concurrency limit."""
    if not CLAUDE_SDK_AVAILABLE:
        return {}
    with _claude_semaphore:
        # Allow nested Claude Code invocations (needed when running inside a session)
        claudecode_env = os.environ.pop("CLAUDECODE", None)
        try:
            return asyncio.run(_claude_extract(impressum_text))
        except Exception as e:
            print(f"  [CLAUDE FALLBACK FAILED] {e}")
            return {}
        finally:
            if claudecode_env is not None:
                os.environ["CLAUDECODE"] = claudecode_env


def merge_extractions(regex_result: dict, claude_result: dict) -> dict:
    """Merge regex and Claude results — Claude takes precedence when it has a value."""
    merged = dict(regex_result)
    for key, value in claude_result.items():
        if key in merged and value and str(value).strip():
            merged[key] = value
    return merged


async def _claude_icp_analyze(homepage_text: str, company_name: str = "") -> dict:
    """Use Claude to score a company website against the B2B industrial ICP."""
    prompt = f"""Du analysierst eine Unternehmenswebsite, um zu entscheiden, ob sie zu einer B2B-Agentur passt, die auf Industrieunternehmen spezialisiert ist.

### Ziel-ICP:
Hersteller und Maschinenbauunternehmen mit erklaerungsbeduерftigen technischen Produkten (z. B. Pumpen, Messtechnik, Praezisionsbauteile, Anlagenbau).

### Schritt 1: Klassifikation
Bestimme:
- Ist das Unternehmen ein Hersteller? (ja/nein)
- Branche (z. B. Maschinenbau, Messtechnik, Handel, Dienstleistung)
- Zielgruppe: B2B, B2C oder gemischt

### Schritt 2: ICP-Scoring (0-100)
+35 → Hersteller / produziert physische Produkte
+25 → klarer Bezug zu Maschinenbau / Industrie / Technik
+15 → B2B-Fokus
+10 → komplexe technische Produkte (erklaerungsbeduerftig)
+10 → internationale Taetigkeit oder Industriekunden
+5 → Hinweise auf Groesse (Historie, Mitarbeiter etc.)

### Schritt 3: Ausschluss (sofort Score = 0 wenn zutreffend)
- Agentur / Marketing / Webdesign
- IT- oder Softwarefirma ohne Industriebezug
- Handwerksbetrieb
- reiner Online-Shop
- Coaching / Beratung

### Ausgabeformat (nur JSON, kein Markdown):
{{
  "company_name": "{company_name}",
  "is_manufacturer": "",
  "industry": "",
  "target_group": "",
  "icp_score": 0,
  "icp_fit": "",
  "reason": ""
}}

ICP Fit-Regeln: High Fit (>=70), Medium Fit (40-69), Low Fit (<40)

Website-Text:
{homepage_text[:6000]}

Antworte AUSSCHLIESSLICH mit validem JSON. Kein Markdown, kein Code-Block, keine Erklaerungen."""

    options = ClaudeAgentOptions(
        system_prompt="Du klassifizierst Unternehmenswebseiten fuer ICP-Scoring. Antworte ausschliesslich mit validem JSON.",
        max_turns=1,
        allowed_tools=[],
    )

    response_text = ""
    async for msg in query(prompt=prompt, options=options):
        if hasattr(msg, "content"):
            for block in msg.content:
                if hasattr(block, "text"):
                    response_text += block.text

    match = re.search(r"\{[\s\S]*\}", response_text)
    if match:
        try:
            return json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return {}


def claude_icp_analyze_sync(homepage_text: str, company_name: str = "") -> dict:
    """Sync wrapper for ICP analysis with concurrency limit."""
    if not CLAUDE_SDK_AVAILABLE:
        return {}
    with _claude_semaphore:
        claudecode_env = os.environ.pop("CLAUDECODE", None)
        try:
            return asyncio.run(_claude_icp_analyze(homepage_text, company_name))
        except Exception as e:
            print(f"  [ICP FALLBACK FAILED] {e}")
            return {}
        finally:
            if claudecode_env is not None:
                os.environ["CLAUDECODE"] = claudecode_env


def needs_claude_fallback(result: dict) -> bool:
    """Smart trigger: only call Claude when regex results look suspicious."""
    firma = result.get("firma", "").lower()
    plz = result.get("plz", "")
    email = result.get("email", "")

    # Garbage detection in firma field
    garbage_markers = [
        "keine personen", "datenschutz", "cookie", "diese webseite",
        "impressum - ", "tmg", "pam-co", "impressum",
    ]
    firma_looks_bad = (
        len(firma) < 4
        or len(firma) > 120
        or any(marker in firma for marker in garbage_markers)
    )
    # Does firma have a legal form suffix?
    has_legal_form = bool(re.search(r"(?:GmbH|AG|KG|KGaA|e\.K\.|UG|mbH|SE|eG|GbR)", result.get("firma", ""), re.IGNORECASE))

    # Structural checks
    valid_plz = bool(re.match(r"^\d{5}$", plz))
    valid_email = "@" in email and "." in email

    # Count high-confidence signals
    good_signals = sum([has_legal_form, valid_plz, valid_email,
                        bool(result.get("telefon")), bool(result.get("geschaeftsfuehrer"))])

    # Call Claude if: firma looks bad OR fewer than 4 good signals
    return firma_looks_bad or good_signals < 4


def _is_error_page(html: str) -> bool:
    """Heuristic check if the page is a 404 or error page."""
    lower = html.lower()
    # Check title tag anywhere in the document
    title_match = re.search(r"<title[^>]*>(.*?)</title>", lower)
    if title_match:
        title = title_match.group(1)
        title_errors = ["404", "nicht gefunden", "not found", "fehler", "error"]
        if any(err in title for err in title_errors):
            return True
    # Also check prominent text in first 10000 chars
    check_area = lower[:10000]
    body_errors = [
        "fehler 404",
        "error 404",
        "seite nicht gefunden",
        "seite wurde nicht gefunden",
        "page not found",
        "page unavailable",
    ]
    return any(indicator in check_area for indicator in body_errors)


# ---------------------------------------------------------------------------
# Contact Extraction
# ---------------------------------------------------------------------------


def extract_contacts(html: str) -> dict:
    """Extract structured contact information from Impressum HTML."""
    soup = BeautifulSoup(html, "html.parser")

    # Remove script and style elements
    for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
        tag.decompose()

    text = soup.get_text(separator="\n")
    # Normalize whitespace per line
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    full_text = "\n".join(lines)

    result = {f: "" for f in FIELDS}

    # --- E-Mail ---
    emails = re.findall(
        r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
        full_text,
    )
    if emails:
        result["email"] = emails[0]

    # --- Telefon ---
    tel_match = re.search(
        r"(?:Tel(?:efon)?|Phone|Fon|Tel\.)\s*[:\.]?\s*([\+\d\s\-\/\(\)]{8,})",
        full_text,
        re.IGNORECASE,
    )
    if tel_match:
        result["telefon"] = tel_match.group(1).strip()

    # --- Fax ---
    fax_match = re.search(
        r"(?:Fax)\s*[:\.]?\s*([\+\d\s\-\/\(\)]{8,})",
        full_text,
        re.IGNORECASE,
    )
    if fax_match:
        result["fax"] = fax_match.group(1).strip()

    # --- Geschaeftsfuehrer / Inhaber ---
    gf_match = re.search(
        r"(?:Gesch[aä]ftsf[uü]hr(?:er|ung|er/?in)|Inhaber(?:/?in)?|Vertretungsberechtig(?:t|er)|"
        r"Managing\s*Director|CEO|Vorstand)\s*[:/\.]?\s*(.+)",
        full_text,
        re.IGNORECASE,
    )
    if gf_match:
        gf_value = gf_match.group(1).strip()
        # Clean up: take until next label or newline
        gf_value = re.split(r"\n|Registergericht|Handelsregister|USt|Amtsgericht", gf_value)[0].strip()
        gf_value = gf_value.rstrip(",;.")
        # Remove leading artifacts like "in:" from "Geschäftsführer/in:"
        gf_value = re.sub(r"^(?:in|ung)\s*[:\.]?\s*", "", gf_value, flags=re.IGNORECASE).strip()
        result["geschaeftsfuehrer"] = gf_value

    # --- Firma (company name) ---
    # Match company name ending with legal form suffix
    firma_match = re.search(
        r"([\wäöüÄÖÜß][\w\säöüÄÖÜß\-&\.\,]+?(?:GmbH|AG|KGaA|KG|OHG|e\.?\s?K\.|UG|mbH|SE|Ltd\.?|Inc\.?|Co\.?\s*(?:KG|KGaA|OHG)?|GbR|eG)(?:\s*&\s*Co\.?\s*(?:KG|KGaA|OHG))?)",
        full_text,
    )
    if firma_match:
        firma = firma_match.group(1).strip()
        # Clean: take only the line containing the match
        firma = firma.split("\n")[0].strip()
        result["firma"] = firma

    # --- USt-IdNr ---
    ust_match = re.search(
        r"(?:USt[\-\.]?\s*(?:Id[\-\.]?\s*Nr|Identifikationsnummer)|"
        r"Umsatzsteuer[\-\s]*Identifikationsnummer|VAT[\-\s]*ID)\s*[:\.]?\s*"
        r"(DE\s*\d{9}|\w{2}\s*\d{7,})",
        full_text,
        re.IGNORECASE,
    )
    if ust_match:
        result["ust_idnr"] = ust_match.group(1).strip()

    # --- Handelsregister ---
    hr_match = re.search(
        r"(?:Handelsregister|Registergericht|Amtsgericht)\s*[:\.]?\s*([^\n]+)",
        full_text,
        re.IGNORECASE,
    )
    if hr_match:
        hr_value = hr_match.group(1).strip().rstrip(",;.")
        # Clean: stop at common following sections
        hr_value = hr_value.split("\n")[0].strip()
        hr_value = re.split(r"USt|Steuer|Tel|Chairman|Vorstand|Geschäftsf", hr_value)[0].strip().rstrip(",;.")
        # Remove redundant label prefixes
        hr_value = re.sub(r"^(?:Registergericht|Amtsgericht)\s*[:\.]?\s*", "", hr_value, flags=re.IGNORECASE).strip()
        result["handelsregister"] = hr_value

    # --- HRB number (supplement to Handelsregister) ---
    if not result["handelsregister"]:
        hrb_match = re.search(r"(HR[AB]\s*\d+)", full_text)
        if hrb_match:
            result["handelsregister"] = hrb_match.group(1).strip()

    # --- Adresse (PLZ + Ort + Strasse) ---
    # German PLZ pattern: 5 digits
    plz_match = re.search(
        r"(\d{5})\s+([A-Za-zäöüÄÖÜß\s\-]+)",
        full_text,
    )
    if plz_match:
        result["plz"] = plz_match.group(1)
        result["ort"] = plz_match.group(2).strip().split("\n")[0].strip().rstrip(",;.")

    # Street: line before PLZ usually
    if plz_match:
        plz_pos = plz_match.start()
        text_before_plz = full_text[:plz_pos]
        preceding_lines = [l.strip() for l in text_before_plz.split("\n") if l.strip()]
        if preceding_lines:
            street_candidate = preceding_lines[-1]
            # Street usually contains a number
            if re.search(r"\d", street_candidate) and len(street_candidate) < 80:
                result["strasse"] = street_candidate

    # --- Name extraction from Geschaeftsfuehrer ---
    if result["geschaeftsfuehrer"]:
        # Take only the first person if multiple are listed
        first_person = re.split(r"[,;]|\bund\b", result["geschaeftsfuehrer"])[0].strip()
        # Remove role descriptions in parentheses
        first_person = re.sub(r"\s*\(.*?\)", "", first_person).strip()
        name_parts = first_person.split()
        if len(name_parts) >= 2:
            title_pattern = re.compile(
                r"^(?:dr|prof|dipl|ing|mr|mrs|herr|frau|mag|rer|nat|pol|jur|med|phil|oec|phys)\.?$",
                re.IGNORECASE,
            )
            # Also filter compound titles like "Dipl.-Phys." "Dipl.-Ing."
            filtered = [p for p in name_parts if not title_pattern.match(p.rstrip(".-"))]
            # Remove parts that are dash-joined titles like "Dipl.-Phys."
            filtered = [p for p in filtered if not re.match(r"^(?:Dipl|Prof|Dr)\.-", p, re.IGNORECASE)]
            if len(filtered) >= 2:
                result["vorname"] = filtered[0]
                result["nachname"] = " ".join(filtered[1:])
            elif len(filtered) == 1:
                result["nachname"] = filtered[0]

    # --- Land (default Deutschland for .de domains) ---
    land_match = re.search(
        r"(?:Deutschland|Germany|Bundesrepublik|Schweiz|Switzerland|[OÖö]sterreich|Austria)",
        full_text,
        re.IGNORECASE,
    )
    if land_match:
        mapping = {
            "deutschland": "Deutschland",
            "germany": "Deutschland",
            "bundesrepublik": "Deutschland",
            "schweiz": "Schweiz",
            "switzerland": "Schweiz",
            "österreich": "Oesterreich",
            "austria": "Oesterreich",
        }
        result["land"] = mapping.get(land_match.group(0).lower(), land_match.group(0))
    else:
        result["land"] = "Deutschland"  # default for .de sites

    return result


# ---------------------------------------------------------------------------
# Google Sheets I/O
# ---------------------------------------------------------------------------

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def get_gspread_client(credentials_path: str) -> gspread.Client:
    """Authenticate with Google Sheets using a service account."""
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return gspread.authorize(creds)


def read_urls_from_sheet(
    client: gspread.Client,
    spreadsheet_url: str,
    sheet_name: str = "Sheet1",
    url_column: int = 1,
) -> list[str]:
    """Read URLs from the specified column in a Google Sheet."""
    spreadsheet = client.open_by_url(spreadsheet_url)
    worksheet = spreadsheet.worksheet(sheet_name)
    all_values = worksheet.col_values(url_column)

    # Skip header row, filter empty
    urls = [v.strip() for v in all_values[1:] if v.strip()]
    return urls


def update_sheet_inline(
    client: gspread.Client,
    spreadsheet_url: str,
    results: list[dict],
    start_row: int,
    header_row: int = 2,
) -> None:
    """Update the existing sheet by adding new columns with extracted data.

    Adds columns H-P (Impressum URL, Geschaeftsfuehrer, Telefon_Impressum,
    Email_Impressum, Strasse_Impressum, PLZ_Impressum, Ort_Impressum,
    Handelsregister, USt-IdNr, Status) to the existing sheet.
    """
    spreadsheet = client.open_by_url(spreadsheet_url)
    worksheet = spreadsheet.sheet1

    # Write new column headers (H-...) on the header row
    new_headers = [
        "icp_score",
        "icp_fit",
        "is_manufacturer",
        "industry",
        "target_group",
        "icp_reason",
        "impressum_url",
        "geschaeftsfuehrer",
        "telefon_impressum",
        "email_impressum",
        "strasse_impressum",
        "plz_impressum",
        "ort_impressum",
        "handelsregister",
        "ust_idnr",
        "scrape_status",
    ]
    worksheet.update(range_name=f"H{header_row}", values=[new_headers])

    # Write data rows starting at start_row
    rows = []
    for r in results:
        rows.append([
            r.get("icp_score", ""),
            r.get("icp_fit", ""),
            r.get("is_manufacturer", ""),
            r.get("industry", ""),
            r.get("target_group", ""),
            r.get("icp_reason", ""),
            r.get("impressum_url", ""),
            r.get("geschaeftsfuehrer", ""),
            r.get("telefon", ""),
            r.get("email", ""),
            r.get("strasse", ""),
            r.get("plz", ""),
            r.get("ort", ""),
            r.get("handelsregister", ""),
            r.get("ust_idnr", ""),
            r.get("status", ""),
        ])

    if rows:
        batch_size = 500
        for i in range(0, len(rows), batch_size):
            batch = rows[i:i + batch_size]
            cell_start = start_row + i
            worksheet.update(range_name=f"H{cell_start}", values=batch)
            print(f"  Sheet aktualisiert: Zeile {cell_start}-{cell_start + len(batch) - 1}")

    print(f"\n  {len(rows)} Zeilen ins Sheet geschrieben (Spalten H-W)")


def write_results_to_sheet(
    client: gspread.Client,
    spreadsheet_url: str,
    results: list[dict],
    output_sheet_name: str = "Impressum Ergebnisse",
) -> None:
    """Write extraction results to a new sheet in the spreadsheet."""
    spreadsheet = client.open_by_url(spreadsheet_url)

    # Create or get output sheet
    try:
        worksheet = spreadsheet.worksheet(output_sheet_name)
        worksheet.clear()
    except gspread.exceptions.WorksheetNotFound:
        worksheet = spreadsheet.add_worksheet(
            title=output_sheet_name,
            rows=len(results) + 1,
            cols=len(HEADER_ROW),
        )

    # Write header
    worksheet.update(range_name="A1", values=[HEADER_ROW])

    # Write data rows
    rows = []
    for r in results:
        rows.append([
            r.get("webseite", ""),
            r.get("impressum_url", ""),
            r.get("icp_score", ""),
            r.get("icp_fit", ""),
            r.get("is_manufacturer", ""),
            r.get("industry", ""),
            r.get("target_group", ""),
            r.get("icp_reason", ""),
            r.get("firma", ""),
            r.get("vorname", ""),
            r.get("nachname", ""),
            r.get("geschaeftsfuehrer", ""),
            r.get("telefon", ""),
            r.get("email", ""),
            r.get("strasse", ""),
            r.get("plz", ""),
            r.get("ort", ""),
            r.get("land", ""),
            r.get("handelsregister", ""),
            r.get("ust_idnr", ""),
            r.get("fax", ""),
            r.get("status", ""),
        ])

    if rows:
        worksheet.update(range_name="A2", values=rows)

    print(f"\n  Ergebnisse in Sheet '{output_sheet_name}' geschrieben ({len(rows)} Zeilen)")


# ---------------------------------------------------------------------------
# Main Pipeline
# ---------------------------------------------------------------------------


def scrape_single_url(
    url: str, account_id: str, api_token: str, use_claude: bool = True
) -> dict:
    """Scrape Impressum data from a single website.

    Runs ICP analysis on the homepage first. Only proceeds to full Impressum
    extraction when icp_score >= 40.
    """
    result = {"webseite": url, "impressum_url": "", "status": ""}
    for f in FIELDS:
        result[f] = ""
    for f in ICP_FIELDS:
        result[f] = ""

    # Normalize URL
    if not url.startswith("http"):
        url = "https://" + url

    # Find Impressum page — also returns homepage HTML (cached_html)
    impressum_url, cached_html = find_impressum_url(url, account_id, api_token)

    # --- ICP Analysis on homepage ---
    if use_claude and CLAUDE_SDK_AVAILABLE and cached_html:
        print(f"  [ICP] Analysiere Homepage...")
        soup = BeautifulSoup(cached_html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()
        homepage_text = "\n".join(
            l.strip() for l in soup.get_text(separator="\n").splitlines() if l.strip()
        )
        icp_data = claude_icp_analyze_sync(homepage_text)
        if icp_data:
            result["icp_score"] = icp_data.get("icp_score", 0)
            result["icp_fit"] = icp_data.get("icp_fit", "")
            result["is_manufacturer"] = icp_data.get("is_manufacturer", "")
            result["industry"] = icp_data.get("industry", "")
            result["target_group"] = icp_data.get("target_group", "")
            result["icp_reason"] = icp_data.get("reason", "")
            score = int(icp_data.get("icp_score", 0))
            fit = icp_data.get("icp_fit", "")
            print(f"  [ICP] Score={score} | {fit} | {icp_data.get('industry', '')} | {icp_data.get('reason', '')[:60]}")

            if score < 40:
                result["status"] = f"ICP Low Fit ({score})"
                return result

    if not impressum_url:
        result["status"] = "Kein Impressum gefunden"
        return result

    result["impressum_url"] = impressum_url

    # Fetch Impressum page
    html = fetch_html(impressum_url, account_id, api_token)

    if not html:
        result["status"] = "Impressum-Seite nicht ladbar"
        return result

    # Extract contacts via regex
    contacts = extract_contacts(html)

    # Claude fallback if regex extraction was too sparse
    if use_claude and CLAUDE_SDK_AVAILABLE and needs_claude_fallback(contacts):
        print(f"  [CLAUDE FALLBACK] Regex-Ergebnis zu duenn, frage Claude...")
        soup = BeautifulSoup(html, "html.parser")
        for tag in soup(["script", "style", "nav", "header", "footer", "noscript"]):
            tag.decompose()
        text = soup.get_text(separator="\n")
        text = "\n".join(l.strip() for l in text.splitlines() if l.strip())
        claude_contacts = claude_extract_sync(text)
        if claude_contacts:
            contacts = merge_extractions(contacts, claude_contacts)
            print(f"  [CLAUDE OK] Felder ergaenzt")

    # Preserve webseite, impressum_url and ICP fields from earlier steps
    saved = {k: result[k] for k in ["webseite", "impressum_url"] + ICP_FIELDS}
    result.update(contacts)
    result.update(saved)
    result["status"] = "OK"

    return result


def read_urls_from_csv(csv_path: str, url_column: str = "www") -> list[str]:
    """Read URLs from a CSV file."""
    import csv
    urls = []
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            url = row.get(url_column, "").strip()
            if url:
                urls.append(url)
    return urls


def write_results_to_csv(results: list[dict], output_path: str) -> None:
    """Write extraction results to a CSV file."""
    import csv
    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HEADER_ROW)
        writer.writeheader()
        for r in results:
            writer.writerow({
                "Webseite (Input)": r.get("webseite", ""),
                "Impressum URL": r.get("impressum_url", ""),
                "ICP Score": r.get("icp_score", ""),
                "ICP Fit": r.get("icp_fit", ""),
                "Hersteller": r.get("is_manufacturer", ""),
                "Branche": r.get("industry", ""),
                "Zielgruppe": r.get("target_group", ""),
                "ICP Begruendung": r.get("icp_reason", ""),
                "Firma": r.get("firma", ""),
                "Vorname": r.get("vorname", ""),
                "Nachname": r.get("nachname", ""),
                "Geschaeftsfuehrer / Inhaber": r.get("geschaeftsfuehrer", ""),
                "Telefon": r.get("telefon", ""),
                "E-Mail": r.get("email", ""),
                "Strasse": r.get("strasse", ""),
                "PLZ": r.get("plz", ""),
                "Ort": r.get("ort", ""),
                "Land": r.get("land", ""),
                "Handelsregister": r.get("handelsregister", ""),
                "USt-IdNr": r.get("ust_idnr", ""),
                "Fax": r.get("fax", ""),
                "Status": r.get("status", ""),
            })
    print(f"\n  Ergebnisse gespeichert: {output_path} ({len(results)} Zeilen)")


def main():
    parser = argparse.ArgumentParser(
        description="Scrape German Impressum pages and save results to Google Sheets or CSV"
    )
    parser.add_argument(
        "source",
        help="Google Sheets URL or path to CSV file with website URLs",
    )
    parser.add_argument(
        "--credentials",
        default="credentials.json",
        help="Path to Google Service Account credentials JSON (default: credentials.json)",
    )
    parser.add_argument(
        "--input-sheet",
        default="Sheet1",
        help="Name of the input sheet (default: Sheet1)",
    )
    parser.add_argument(
        "--url-column",
        default="1",
        help="Column number (Google Sheets, 1-indexed) or column name (CSV). Default: 1 / 'www'",
    )
    parser.add_argument(
        "--output-sheet",
        default="Impressum Ergebnisse",
        help="Name of the output sheet (default: 'Impressum Ergebnisse')",
    )
    parser.add_argument(
        "--output-csv",
        default=None,
        help="Output CSV file path (default: ergebnisse.csv for CSV mode)",
    )
    parser.add_argument(
        "--env-file",
        default=os.path.expanduser("~/.env"),
        help="Path to .env file with Cloudflare credentials (default: ~/.env)",
    )
    parser.add_argument(
        "--delay",
        type=float,
        default=RATE_LIMIT_SECONDS,
        help=f"Seconds to wait between websites (default: {RATE_LIMIT_SECONDS})",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Limit number of URLs to scrape (0 = all, default: 0)",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=0,
        help="Skip first N URLs (default: 0)",
    )
    parser.add_argument(
        "--cloudflare",
        action="store_true",
        help="Force Cloudflare Browser Rendering for all requests (slower, for JS-heavy sites)",
    )
    parser.add_argument(
        "--sheet-tab",
        default=None,
        help="Tab name in the Google Sheet (default: first tab)",
    )
    parser.add_argument(
        "--sheet-url-col",
        type=int,
        default=6,
        help="Column number (1-indexed) containing URLs in the Google Sheet (default: 6 = F)",
    )
    parser.add_argument(
        "--sheet-data-start",
        type=int,
        default=3,
        help="Row number where data starts in Google Sheet (default: 3)",
    )
    parser.add_argument(
        "--sheet-header-row",
        type=int,
        default=2,
        help="Row number of header in Google Sheet (default: 2)",
    )
    parser.add_argument(
        "--sheet-out-col",
        default="H",
        help="Starting column letter for output data (default: H)",
    )
    parser.add_argument(
        "--no-claude",
        action="store_true",
        help="Disable Claude fallback even if SDK is available",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=5,
        help="Number of parallel workers (default: 5)",
    )

    args = parser.parse_args()

    # Load environment
    load_dotenv(args.env_file)
    account_id = os.getenv("CLOUDFLARE_ACCOUNT_ID")
    api_token = os.getenv("CLOUDFLARE_API_TOKEN")

    if not account_id or not api_token:
        print("FEHLER: CLOUDFLARE_ACCOUNT_ID und CLOUDFLARE_API_TOKEN muessen in der .env Datei gesetzt sein.")
        sys.exit(1)

    # Determine mode: CSV or Google Sheets
    is_csv = os.path.isfile(args.source) or args.source.endswith(".csv")
    is_gsheet = args.source.startswith("https://docs.google.com/")

    print("=" * 60)
    print("  IMPRESSUM SCRAPER")
    print("=" * 60)

    if is_csv:
        print(f"\n1. Lese URLs aus CSV: {args.source}")
        col_name = args.url_column if not args.url_column.isdigit() else "www"
        urls = read_urls_from_csv(args.source, col_name)
    elif is_gsheet:
        creds_path = args.credentials
        if not os.path.exists(creds_path):
            print(f"FEHLER: Google Service Account Credentials nicht gefunden: {creds_path}")
            print("Siehe SETUP.md fuer Anweisungen zur Einrichtung.")
            sys.exit(1)
        print(f"\n1. Verbinde mit Google Sheets...")
        client = get_gspread_client(creds_path)
        print(f"2. Lese URLs aus Sheet...")
        spreadsheet = client.open_by_url(args.source)
        # Select worksheet by name if specified, else first
        if args.sheet_tab:
            worksheet = spreadsheet.worksheet(args.sheet_tab)
        else:
            worksheet = spreadsheet.sheet1
        print(f"   Tab: '{worksheet.title}'")
        all_values = worksheet.col_values(args.sheet_url_col)
        # Build list of (sheet_row_number, url) tuples — preserve row mapping
        data_start = args.sheet_data_start
        url_rows = []
        for idx, val in enumerate(all_values[data_start - 1:], start=data_start):
            if val.strip():
                url_rows.append((idx, val.strip()))
        urls = [u for _, u in url_rows]
    else:
        print(f"FEHLER: '{args.source}' ist weder eine CSV-Datei noch eine Google Sheets URL.")
        sys.exit(1)

    # Apply offset and limit
    if args.offset > 0:
        urls = urls[args.offset:]
    if args.limit > 0:
        urls = urls[:args.limit]

    print(f"   {len(urls)} URLs zu verarbeiten.")

    if not urls:
        print("Keine URLs gefunden. Abbruch.")
        sys.exit(0)

    # Prepare Google Sheet for live updates
    gsheet_live = is_gsheet and not args.output_csv
    out_col = args.sheet_out_col.upper()
    header_row = args.sheet_header_row
    if gsheet_live:
        ws = worksheet  # already loaded above
        # Write new column headers once
        new_headers = [
            "icp_score", "icp_fit", "is_manufacturer", "industry",
            "target_group", "icp_reason", "impressum_url", "geschaeftsfuehrer",
            "telefon_impressum", "email_impressum", "strasse_impressum",
            "plz_impressum", "ort_impressum", "handelsregister", "ust_idnr",
            "scrape_status",
        ]
        ws.update(range_name=f"{out_col}{header_row}", values=[new_headers])

    # Build row mapping for Google Sheets mode
    if is_gsheet:
        # url_rows has (sheet_row, url) tuples — apply offset/limit
        url_rows_sliced = url_rows[args.offset:]
        if args.limit > 0:
            url_rows_sliced = url_rows_sliced[:args.limit]
        sheet_row_map = [row_num for row_num, _ in url_rows_sliced]

    # Scrape URLs in parallel
    print(f"\n3. Starte Scraping ({len(urls)} Webseiten, {args.workers} parallele Worker)...\n")
    results = [None] * len(urls)
    sheet_lock = threading.Lock()
    counter = {"done": 0, "ok": 0}
    counter_lock = threading.Lock()

    def worker(idx: int, url: str) -> dict:
        try:
            result = scrape_single_url(url, account_id, api_token, use_claude=not args.no_claude)
        except Exception as e:
            result = {
                "webseite": url,
                "impressum_url": "",
                "status": f"Fehler: {e}",
                **{f: "" for f in FIELDS},
            }

        # Write to Google Sheet immediately
        if gsheet_live:
            row_num = sheet_row_map[idx]
            row_data = [[
                result.get("icp_score", ""),
                result.get("icp_fit", ""),
                result.get("is_manufacturer", ""),
                result.get("industry", ""),
                result.get("target_group", ""),
                result.get("icp_reason", ""),
                result.get("impressum_url", ""),
                result.get("geschaeftsfuehrer", ""),
                result.get("telefon", ""),
                result.get("email", ""),
                result.get("strasse", ""),
                result.get("plz", ""),
                result.get("ort", ""),
                result.get("handelsregister", ""),
                result.get("ust_idnr", ""),
                result.get("status", ""),
            ]]
            with sheet_lock:
                try:
                    ws.update(range_name=f"{out_col}{row_num}", values=row_data)
                except Exception as e:
                    print(f"  [WARN] Sheet-Update Zeile {row_num} fehlgeschlagen: {e}")

        # Progress counter
        with counter_lock:
            counter["done"] += 1
            if result["status"] == "OK":
                counter["ok"] += 1
            done = counter["done"]
            firma = result.get("firma", "-")[:40]
            status_short = "OK" if result["status"] == "OK" else "SKIP"
            print(f"[{done:4d}/{len(urls)}] {status_short} | {firma:40s} | {url[:40]}")

        return result

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(worker, i, url): i for i, url in enumerate(urls)}
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()

    # Write results to CSV if requested
    if args.output_csv:
        print(f"\n4. Schreibe Ergebnisse in CSV...")
        write_results_to_csv(results, args.output_csv)
    elif not gsheet_live:
        print(f"\n4. Schreibe Ergebnisse in CSV...")
        write_results_to_csv(results, "ergebnisse.csv")

    # Summary
    ok_count = sum(1 for r in results if r["status"] == "OK")
    print(f"\n{'=' * 60}")
    print(f"  FERTIG: {ok_count}/{len(results)} erfolgreich extrahiert")
    print(f"{'=' * 60}")


if __name__ == "__main__":
    main()
