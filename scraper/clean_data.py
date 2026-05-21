"""
One-time script to clean up sites.json without re-scraping.
Run this instead of re-running the full scraper.

Run with:
    python clean_data.py
"""

import json
import re
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "data" / "sites.json"

with open(DATA_FILE, encoding="utf-8") as f:
    sites = json.load(f)

print(f"Before: {len(sites)} entries")

# 1. Drop entries that aren't real wormhole site pages
sites = [s for s in sites if "faction" in s]
print(f"After removing non-sites: {len(sites)} entries")

# 2. Fix blue_loot_value — extract just the "X Mill" or "X Bill" portion
def clean_loot_value(raw: str) -> str:
    match = re.search(r"([\d.,]+ ?[MmBb]ill?)", raw)
    return match.group(1).strip() if match else raw

for site in sites:
    if "blue_loot_value" in site:
        site["blue_loot_value"] = clean_loot_value(site["blue_loot_value"])

with open(DATA_FILE, "w", encoding="utf-8") as f:
    json.dump(sites, f, indent=2, ensure_ascii=False)

print(f"Done. Saved {len(sites)} sites to {DATA_FILE}")
