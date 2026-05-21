"""
EVE Online Wormhole Site Scraper
Scrapes site data from eve-survival.org and saves it as JSON.

Run with:
    python scraper.py
"""

import json
import time
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# --- Configuration ---

BASE_URL = "https://eve-survival.org/wikka.php?wakka="
INDEX_PAGE = "WormholeSpace"
OUTPUT_FILE = Path(__file__).parent.parent / "data" / "sites.json"

# Be polite — wait between requests so we don't hammer the server
DELAY_SECONDS = 1.5


# --- Helper: fetch a page ---

def fetch_page(wakka_name: str, session: requests.Session) -> BeautifulSoup:
    """Fetch a wiki page and return a BeautifulSoup object for parsing."""
    url = BASE_URL + wakka_name
    print(f"  Fetching: {url}")

    response = session.get(url, timeout=15)
    response.raise_for_status()  # raises an error if the request failed (e.g. 404)

    # BeautifulSoup parses the raw HTML into a tree we can navigate
    return BeautifulSoup(response.text, "html.parser")


# --- Phase 1: Scrape the index page ---

def scrape_index(session: requests.Session) -> list[dict]:
    """
    Parse the WormholeSpace index page.
    Returns a list of dicts like:
      { "name": "PerimeterAmbushPoint", "display_name": "Perimeter Ambush Point",
        "wh_class": "C1", "site_type": "Cosmic Anomaly" }
    """
    print("Phase 1: Scraping index page...")
    soup = fetch_index(session)

    # The main wiki content lives inside a div with id="wikicontent"
    content = soup.find(id="wikicontent") or soup.find("div", class_="page")
    if not content:
        # Fallback: just use the whole body
        content = soup.find("body")

    sites = []
    current_class = "Classless"
    current_type = "Unknown"

    # Walk through every element in the content area in order
    for tag in content.descendants:
        # Skip non-Tag nodes (plain text, etc.)
        if not hasattr(tag, "name"):
            continue

        # Headings tell us which WH class we're in (e.g. "Class 1", "Class 2")
        if tag.name in ("h1", "h2", "h3", "h4"):
            heading_text = tag.get_text(strip=True)
            class_match = re.search(r"Class\s*(\d)", heading_text, re.IGNORECASE)
            if class_match:
                current_class = f"C{class_match.group(1)}"
                current_type = "Unknown"
            elif "classless" in heading_text.lower():
                current_class = "Classless"

            # Sub-headings tell us the site type within a class
            if tag.name in ("h3", "h4"):
                type_keywords = {
                    "anomal": "Cosmic Anomaly",
                    "magnetometric": "Magnetometric",
                    "radar": "Radar",
                    "gravimetric": "Gravimetric",
                    "ladar": "Ladar",
                    "combat": "Cosmic Anomaly",
                }
                for keyword, site_type in type_keywords.items():
                    if keyword in heading_text.lower():
                        current_type = site_type
                        break

        # Links are the actual site entries
        if tag.name == "a":
            href = tag.get("href", "")
            # Site links contain "wakka=" in the URL
            if "wakka=" not in href:
                continue
            # Skip navigation links (they point to meta pages)
            skip_words = ["WormholeSpace", "MissionReports", "PageIndex",
                          "RecentChanges", "RecentlyCommented", "HomePage",
                          "Login", "Register", "UserSettings", "CategoryWiki",
                          "EVE", "Jove", "DOTLAN", "Metrics"]
            wakka = re.search(r"wakka=([^&]+)", href)
            if not wakka:
                continue
            wakka_name = wakka.group(1)
            # Skip wiki admin/edit URLs (e.g. "SomePage/edit")
            if "/" in wakka_name:
                continue
            if any(s.lower() in wakka_name.lower() for s in skip_words):
                continue

            display_name = tag.get_text(strip=True)
            if not display_name:
                continue

            # Avoid duplicates (same site listed under multiple classes)
            if any(s["name"] == wakka_name for s in sites):
                continue

            sites.append({
                "name": wakka_name,
                "display_name": display_name,
                "wh_class": current_class,
                "site_type": current_type,
            })

    print(f"  Found {len(sites)} sites.")
    return sites


def fetch_index(session: requests.Session) -> BeautifulSoup:
    return fetch_page(INDEX_PAGE, session)


# --- Phase 2: Scrape individual site pages ---

def parse_site_page(soup: BeautifulSoup) -> dict:
    """
    Extract metadata and wave info from an individual site page.
    Returns a dict of whatever fields we can find.
    """
    data = {}

    # The page content
    content = soup.find(id="wikicontent") or soup.find("div", class_="page") or soup.find("body")
    if not content:
        return data

    full_text = content.get_text(separator="\n")

    # These are the key-value fields present on most site pages.
    # The pattern is: "Label: Value" on its own line (or close to it).
    metadata_fields = [
        ("faction", r"Faction\s*:\s*(.+)"),
        ("signature_type", r"Signature type\s*:\s*(.+)"),
        ("space_type", r"Space type\s*:\s*(.+)"),
        ("damage_dealt", r"Damage dealt\s*:\s*(.+)"),
        ("webbers", r"Webbers?\s*:\s*(.+)"),
        ("scramblers", r"Scramblers?\s*:\s*(.+)"),
        ("extras", r"Extras?\s*:\s*(.+)"),
        ("recommended_ships", r"Recommended ship\s*[^:]*:\s*(.+)"),
        ("recommended_setup", r"Recommended setup\s*:\s*(.+)"),
        ("blue_loot_value", r"Blue [Ll]oot\s*[^:]*:\s*([\d.,]+ ?[MmBb]ill?)"),
    ]

    for field_name, pattern in metadata_fields:
        match = re.search(pattern, full_text, re.IGNORECASE | re.MULTILINE)
        if match:
            data[field_name] = match.group(1).strip()

    # Extract any WARNING text
    warning_match = re.search(r"(?:Warning|Note)\s*:?\s*(.{10,200})", full_text, re.IGNORECASE)
    if warning_match:
        data["warning"] = warning_match.group(1).strip()

    # Extract pocket/wave names as a simple list (lines starting with "Pocket" or "Wave")
    pockets = re.findall(r"^((?:Pocket|Wave|Initial|Reinforcement)\s*\d*[^\n]*)", full_text, re.MULTILINE | re.IGNORECASE)
    if pockets:
        data["pockets"] = [p.strip() for p in pockets if p.strip()]

    # Grab the raw text too — useful for display and for future parsing improvements
    data["raw_text"] = full_text.strip()

    return data


def scrape_sites(sites: list[dict], session: requests.Session) -> list[dict]:
    """Visit each site page and add its data to the site dict."""
    print(f"\nPhase 2: Scraping {len(sites)} site pages...")
    total = len(sites)

    for i, site in enumerate(sites, 1):
        print(f"  [{i}/{total}] {site['display_name']}")
        try:
            soup = fetch_page(site["name"], session)
            site_data = parse_site_page(soup)
            site.update(site_data)
        except requests.RequestException as e:
            print(f"    ERROR fetching {site['name']}: {e}")
            site["fetch_error"] = str(e)

        # Rate limiting — pause between requests
        if i < total:
            time.sleep(DELAY_SECONDS)

    return sites


# --- Main ---

def main():
    # A Session reuses the TCP connection across requests — faster and friendlier
    session = requests.Session()
    session.headers["User-Agent"] = "EVE-Wormhole-Ref-Scraper/1.0 (personal learning project)"

    try:
        sites = scrape_index(session)
        if not sites:
            print("No sites found on the index page. The page structure may have changed.")
            sys.exit(1)

        sites = scrape_sites(sites, session)

        # Drop any entries that didn't resolve to a real site page
        # (all genuine wormhole sites have a "faction" field)
        before = len(sites)
        sites = [s for s in sites if "faction" in s]
        dropped = before - len(sites)
        if dropped:
            print(f"\nFiltered out {dropped} non-site entries.")

    except requests.RequestException as e:
        print(f"Network error: {e}")
        sys.exit(1)

    # Write output
    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(sites, f, indent=2, ensure_ascii=False)

    print(f"\nDone! Saved {len(sites)} sites to {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
