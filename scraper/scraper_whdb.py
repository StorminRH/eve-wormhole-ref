"""
WHDB Signatures Scraper
Scrapes wormhole site data from whdb.scan-stakan.space and saves as JSON.

This site is a modern, server-rendered static HTML site. Unlike the wiki-style
eve-survival.org scraper, here we:
  1. Parse the index page to collect IDs from clean /signatures/N/ URLs
  2. Fetch each detail page and extract stats using regex on plain text
     (the site uses icon images next to values, not labeled text fields)

Output: data/sites_whdb.json

Run with:
    python scraper_whdb.py
"""

import json
import time
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://whdb.scan-stakan.space"
INDEX_URL = f"{BASE_URL}/signatures/"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "sites_whdb.json"
DELAY_SECONDS = 1.0


def fetch_page(url: str, session: requests.Session) -> BeautifulSoup:
    print(f"  Fetching: {url}")
    response = session.get(url, timeout=15)
    response.raise_for_status()
    return BeautifulSoup(response.text, "html.parser")


def scrape_index(session: requests.Session) -> list[dict]:
    """
    Parse the index page to collect all site IDs, names, and class assignments.
    Sites are grouped under heading elements by WH class.
    """
    print("Phase 1: Scraping index page...")
    soup = fetch_page(INDEX_URL, session)

    sites = []
    seen_ids = set()
    current_class = "Unknown"

    # Walk the page in order so we can track which class heading we're under
    for tag in soup.descendants:
        if not hasattr(tag, "name"):
            continue

        # Class headings — whdb uses text like "Signatues and anomalies 1th class:"
        # The digit appears BEFORE the word "class", so match both orderings.
        # These headings may be in any block element, not just h2/h3.
        if tag.name in ("h1", "h2", "h3", "h4", "p", "div", "span", "li"):
            # Avoid descending into child tags we'll visit separately
            direct_text = tag.get_text(separator=" ", strip=True)
            # "1th class", "2nd class", "3rd class" etc.
            class_match = re.search(r"(\d)\s*(?:st|nd|rd|th)?\s*class", direct_text, re.IGNORECASE)
            if not class_match:
                # Also handle "class 1" ordering as fallback
                class_match = re.search(r"class\s*(\d)", direct_text, re.IGNORECASE)
            if class_match:
                current_class = f"C{class_match.group(1)}"
            elif re.search(r"\bore\b.*site", direct_text, re.IGNORECASE):
                current_class = "Ore"
            elif re.search(r"\bgas\b.*site", direct_text, re.IGNORECASE):
                current_class = "Gas"
            elif re.search(r"thera", direct_text, re.IGNORECASE):
                current_class = "Thera"

        # Site links follow the pattern /signatures/N/
        if tag.name == "a":
            href = tag.get("href", "")
            id_match = re.search(r"^/signatures/(\d+)/$", href)
            if not id_match:
                continue

            site_id = int(id_match.group(1))
            if site_id in seen_ids:
                continue
            seen_ids.add(site_id)

            name = tag.get_text(strip=True)
            if not name:
                continue

            sites.append({
                "id": site_id,
                "display_name": name,
                "wh_class": current_class,
                "url": BASE_URL + href,
            })

    sites.sort(key=lambda x: x["id"])
    print(f"  Found {len(sites)} sites.")
    return sites


def parse_detail_page(soup: BeautifulSoup) -> dict:
    """
    Extract stats from an individual site detail page.

    The page uses image icons next to plain text values rather than labeled
    form fields, so we use regex on the full page text to pull out known patterns.
    """
    data = {}
    text = soup.get_text(separator="\n")

    # Total loot value in ISK
    loot_match = re.search(r"([\d,]+)\s*ISK", text)
    if loot_match:
        data["total_loot_isk"] = int(loot_match.group(1).replace(",", ""))

    # Total EHP
    ehp_match = re.search(r"([\d,]+)\s*EHP", text, re.IGNORECASE)
    if ehp_match:
        data["total_ehp"] = int(ehp_match.group(1).replace(",", ""))

    # DPS rating
    dps_match = re.search(r"([\d,]+)\s*dps", text, re.IGNORECASE)
    if dps_match:
        data["dps"] = int(dps_match.group(1).replace(",", ""))

    # ISK/EHP efficiency ratio
    ratio_match = re.search(r"([\d,]+)\s*ISK/EHP", text, re.IGNORECASE)
    if ratio_match:
        data["isk_per_ehp"] = int(ratio_match.group(1).replace(",", ""))

    # Estimated income at 100 and 500 DPS
    inc_100 = re.search(r"([\d,]+)\s*ISK/hour\s*\(100", text, re.IGNORECASE)
    if inc_100:
        data["income_100dps_isk_hr"] = int(inc_100.group(1).replace(",", ""))

    inc_500 = re.search(r"([\d,]+)\s*ISK/hour\s*\(500", text, re.IGNORECASE)
    if inc_500:
        data["income_500dps_isk_hr"] = int(inc_500.group(1).replace(",", ""))

    # Wave count — count list items that look like wave entries
    wave_headers = re.findall(r"^(Wave\s+\d+|Initial\s+Group|Reinforcement)", text, re.MULTILINE | re.IGNORECASE)
    if wave_headers:
        data["waves"] = [w.strip() for w in wave_headers]

    # NPC ship names — links to individual sleeper ship pages
    npc_names = []
    for link in soup.find_all("a", href=re.compile(r"/sleepers/")):
        name = link.get_text(strip=True)
        if name:
            npc_names.append(name)
    if npc_names:
        data["npcs"] = list(dict.fromkeys(npc_names))  # deduplicate, preserve order

    return data


def scrape_details(sites: list[dict], session: requests.Session) -> list[dict]:
    print(f"\nPhase 2: Scraping {len(sites)} detail pages...")
    total = len(sites)

    for i, site in enumerate(sites, 1):
        print(f"  [{i}/{total}] {site['display_name']}")
        try:
            soup = fetch_page(site["url"], session)
            detail = parse_detail_page(soup)
            site.update(detail)
        except requests.RequestException as e:
            print(f"    ERROR: {e}")
            site["fetch_error"] = str(e)

        if i < total:
            time.sleep(DELAY_SECONDS)

    return sites


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
