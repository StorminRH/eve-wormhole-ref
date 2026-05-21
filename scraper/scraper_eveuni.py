"""
EVE University Wiki Wormhole Sites Scraper
Scrapes wormhole site data from wiki.eveuniversity.org and saves as JSON.

This is a MediaWiki site, which means:
  - Clean HTML structure with standard heading hierarchy (H2 = class, H3 = type)
  - Site lists are <ul> with <a> links pointing to individual wiki articles
  - Detail pages use HTML <table> elements with icon-image column headers

Compared to the eve-survival.org scraper (regex on raw text), this scraper
demonstrates parsing structured HTML tables — a more reliable approach when
the source uses tabular data.

Output: data/sites_eveuni.json

Run with:
    python scraper_eveuni.py
"""

import json
import time
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://wiki.eveuniversity.org"
INDEX_PATH = "/Wormhole_sites"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "sites_eveuni.json"
DELAY_SECONDS = 1.5

# MediaWiki wraps all article content in this div
CONTENT_ID = "mw-content-text"

# Pages we want to skip when following links from the index
SKIP_PATHS = {
    "Wormhole_sites", "Wormhole", "EVE_University", "Category:",
    "Template:", "Special:", "Help:", "File:", "Talk:", "User:",
}


def fetch_page(path: str, session: requests.Session) -> BeautifulSoup:
    url = BASE_URL + path if path.startswith("/") else path
    print(f"  Fetching: {url}")
    response = session.get(url, timeout=15)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def get_content(soup: BeautifulSoup) -> BeautifulSoup:
    """Return the main article content div, falling back to body."""
    return (
        soup.find(id=CONTENT_ID)
        or soup.find("div", class_="mw-parser-output")
        or soup.find("body")
    )


# ---------------------------------------------------------------------------
# Phase 1: Index page
# ---------------------------------------------------------------------------

def scrape_index(session: requests.Session) -> list[dict]:
    """
    Walk the Wormhole_sites index page.
    H2 headings denote WH class; H3 headings denote site type.
    <a> links inside <li> elements are the actual site entries.
    """
    print("Phase 1: Scraping index page...")
    soup = fetch_page(INDEX_PATH, session)
    content = get_content(soup)

    sites = []
    seen_paths = set()
    current_class = "Unknown"
    current_type = "Unknown"
    found_first_class = False  # ignore nav links that appear before any class heading

    type_keywords = {
        "anomal": "Cosmic Anomaly",
        "combat": "Cosmic Anomaly",
        "data": "Data",
        "relic": "Relic",
        "gas": "Gas",
        "ore": "Ore",
        "ice": "Ice",
    }

    for tag in content.descendants:
        if not hasattr(tag, "name"):
            continue

        # H2 → WH class. EVE Uni uses "Class 1", "Class 2", "Gas sites", "Ore sites"
        if tag.name == "h2":
            text = tag.get_text(strip=True)
            m = re.search(r"class\s*(\d)", text, re.IGNORECASE)
            if m:
                current_class = f"C{m.group(1)}"
                current_type = "Unknown"
                found_first_class = True
            elif re.search(r"gas", text, re.IGNORECASE):
                current_class = "Gas"
                current_type = "Gas"
                found_first_class = True
            elif re.search(r"ore", text, re.IGNORECASE):
                current_class = "Ore"
                current_type = "Ore"
                found_first_class = True
            elif re.search(r"ice", text, re.IGNORECASE):
                current_class = "Ice"
                current_type = "Ice"
                found_first_class = True

        # H3 → site type within the class
        if tag.name == "h3":
            text = tag.get_text(strip=True).lower()
            for keyword, site_type in type_keywords.items():
                if keyword in text:
                    current_type = site_type
                    break

        # Only collect links once we've seen a class heading
        if not found_first_class:
            continue

        # <a> inside a <li> = site entry
        if tag.name == "a" and tag.parent and tag.parent.name == "li":
            href = tag.get("href", "")

            if not href.startswith("/"):
                continue
            if any(skip in href for skip in SKIP_PATHS):
                continue
            if "#" in href or "?" in href:
                continue
            if href in seen_paths:
                continue

            display_name = tag.get_text(strip=True)
            if not display_name or len(display_name) < 3:
                continue

            seen_paths.add(href)
            sites.append({
                "path": href,
                "display_name": display_name,
                "wh_class": current_class,
                "site_type": current_type,
            })

    print(f"  Found {len(sites)} sites.")
    return sites


# ---------------------------------------------------------------------------
# Phase 2: Detail pages
# ---------------------------------------------------------------------------

def parse_npc_table(table: BeautifulSoup) -> list[dict]:
    """
    Parse one NPC encounter table from a site detail page.

    MediaWiki tables use <th> for headers and <td> for data cells.
    The EVE Uni wiki uses image icons as column headers; we use the
    image alt text (or title) to identify column meaning.
    """
    rows = table.find_all("tr")
    if not rows:
        return []

    # Build column name list from the header row
    header_row = rows[0]
    headers = []
    for cell in header_row.find_all(["th", "td"]):
        # Column headers are images — grab alt or title text
        img = cell.find("img")
        if img:
            label = img.get("alt") or img.get("title") or ""
        else:
            label = cell.get_text(strip=True)
        headers.append(label.lower().replace(" ", "_"))

    npcs = []
    for row in rows[1:]:
        cells = row.find_all(["td", "th"])
        if not cells:
            continue

        npc = {}
        for i, cell in enumerate(cells):
            col = headers[i] if i < len(headers) else f"col_{i}"
            # Get text, stripping footnote markers
            val = cell.get_text(strip=True)
            # Many cells are numeric — convert where possible
            numeric = re.sub(r"[,\s]", "", val)
            try:
                npc[col] = int(numeric)
            except ValueError:
                try:
                    npc[col] = float(numeric)
                except ValueError:
                    npc[col] = val

        if npc:
            npcs.append(npc)

    return npcs


def parse_detail_page(soup: BeautifulSoup) -> dict:
    """
    Extract metadata and wave/NPC data from a site detail page.

    Strategy:
      1. Pull key-value metadata via regex on plain text (faction, damage, etc.)
      2. Parse each wikitable as a wave/NPC roster
    """
    data = {}
    content = get_content(soup)
    if not content:
        return data

    text = content.get_text(separator="\n")

    # Key-value metadata fields (same patterns as the eve-survival scraper)
    metadata_fields = [
        ("faction", r"Faction\s*[:\-]\s*(.+)"),
        ("damage_dealt", r"Damage\s+dealt\s*[:\-]\s*(.+)"),
        ("webbers", r"Webbers?\s*[:\-]\s*(.+)"),
        ("scramblers", r"Scramblers?\s*[:\-]\s*(.+)"),
        ("recommended_ships", r"Recommended\s+ship[^:]*[:\-]\s*(.+)"),
        # Format on EVE Uni is "X,XXX,XXX ISK in Blue Loot" — value precedes the label
        ("blue_loot", r"([\d,]+)\s*ISK\s+in\s+Blue\s+Loot"),
    ]
    for field, pattern in metadata_fields:
        m = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if m:
            data[field] = m.group(1).strip()

    # Parse all wikitables as waves — each table is one wave/encounter
    tables = content.find_all("table", class_=re.compile(r"wikitable"))
    waves = []
    for i, table in enumerate(tables):
        npcs = parse_npc_table(table)
        if npcs:
            waves.append({"wave": i + 1, "npcs": npcs})
    if waves:
        data["waves"] = waves

    return data


def scrape_details(sites: list[dict], session: requests.Session) -> list[dict]:
    print(f"\nPhase 2: Scraping {len(sites)} detail pages...")
    total = len(sites)

    for i, site in enumerate(sites, 1):
        print(f"  [{i}/{total}] {site['display_name']}")
        try:
            soup = fetch_page(site["path"], session)
            detail = parse_detail_page(soup)
            site.update(detail)
        except requests.HTTPError as e:
            if e.response.status_code == 404:
                print(f"    Page not found — skipping")
                site["fetch_error"] = "404"
            else:
                print(f"    ERROR: {e}")
                site["fetch_error"] = str(e)
        except requests.RequestException as e:
            print(f"    ERROR: {e}")
            site["fetch_error"] = str(e)

        if i < total:
            time.sleep(DELAY_SECONDS)

    return sites


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    session = requests.Session()
    session.headers["User-Agent"] = "EVE-WH-Ref-Scraper/1.0 (personal learning project)"

    try:
        sites = scrape_index(session)
        if not sites:
            print("No sites found. The page structure may have changed.")
            sys.exit(1)

        sites = scrape_details(sites, session)
    except requests.RequestException as e:
        print(f"Network error: {e}")
        sys.exit(1)

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(sites, f, indent=2, ensure_ascii=False)

    print(f"\nDone! Saved {len(sites)} sites to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
