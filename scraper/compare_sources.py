"""
Cross-Source Data Comparison
Loads all three scraped JSON files and reports discrepancies where sources
disagree on the same site's fields.

Sources:
  - sites.json        (eve-survival.org)
  - sites_whdb.json   (whdb.scan-stakan.space)
  - sites_eveuni.json (wiki.eveuniversity.org)

Run with:
    python compare_sources.py
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from collections import defaultdict

DATA_DIR = Path(__file__).parent.parent / "data"

SOURCES = {
    "eve-survival": DATA_DIR / "sites.json",
    "whdb":         DATA_DIR / "sites_whdb.json",
    "eveuni":       DATA_DIR / "sites_eveuni.json",
}
REPORT_FILE = DATA_DIR / "comparison_report.txt"


# ---------------------------------------------------------------------------
# Normalization helpers
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    """Collapse a site name to lowercase letters and digits only for matching."""
    return re.sub(r"[^a-z0-9]", "", name.lower())


def normalize_class(value: str) -> str:
    """Normalize wormhole class to a consistent format like 'C1', 'C2', etc."""
    if not value:
        return ""
    m = re.search(r"(\d)", str(value))
    return f"C{m.group(1)}" if m else value.strip().upper()


def normalize_loot(value: str | int | float | None) -> int | None:
    """Convert any loot representation to a plain integer ISK value."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    # Handle "4.2 mill" / "4.2m" / "4,200,000"
    m = re.search(r"([\d.]+)\s*m", s)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d[\d,]*)", s)
    if m:
        digits = m.group(1).replace(",", "")
        return int(digits) if digits else None
    return None


# ---------------------------------------------------------------------------
# Field extraction per source
# ---------------------------------------------------------------------------

def extract_fields(site: dict, source: str) -> dict:
    """Return a normalized field dict from a raw site record."""
    fields = {}

    fields["wh_class"] = normalize_class(site.get("wh_class", ""))
    fields["site_type"] = site.get("site_type", "").strip()
    fields["faction"] = site.get("faction", "").strip()
    fields["damage_dealt"] = site.get("damage_dealt", "").strip()

    # Loot value — different field names per source
    loot_raw = (
        site.get("blue_loot_value")    # eve-survival
        or site.get("blue_loot")       # eveuni
        or site.get("total_loot_isk")  # whdb (already an int)
    )
    fields["loot_isk"] = normalize_loot(loot_raw)

    return {k: v for k, v in fields.items() if v not in (None, "", 0)}


# ---------------------------------------------------------------------------
# Load + index
# ---------------------------------------------------------------------------

def load_sources() -> dict[str, dict[str, dict]]:
    """
    Returns { source_name: { normalized_site_name: field_dict } }
    Skips any source file that doesn't exist yet.
    """
    loaded = {}
    for name, path in SOURCES.items():
        if not path.exists():
            print(f"  [skip] {name}: {path.name} not found")
            continue
        with open(path, encoding="utf-8") as f:
            records = json.load(f)
        index = {}
        for record in records:
            display = record.get("display_name") or record.get("name") or ""
            key = normalize_name(display)
            if key:
                index[key] = extract_fields(record, name)
        loaded[name] = index
        print(f"  [ok]   {name}: {len(index)} sites")
    return loaded


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def find_discrepancies(loaded: dict[str, dict[str, dict]]) -> list[dict]:
    """
    For every site that appears in 2+ sources, compare each shared field.
    Returns a list of discrepancy records.
    """
    # Collect all normalized names across all sources
    all_names: set[str] = set()
    for index in loaded.values():
        all_names.update(index.keys())

    discrepancies = []

    for norm_name in sorted(all_names):
        # Gather which sources have this site and what they say
        present = {
            src: data[norm_name]
            for src, data in loaded.items()
            if norm_name in data
        }
        if len(present) < 2:
            continue  # can't compare a site seen in only one source

        # Find a display name to use in the report
        display = norm_name  # fallback
        for src_data in loaded.values():
            if norm_name in src_data:
                display = norm_name  # we don't store display here, use normalized

        # Check each field that appears in 2+ sources
        all_fields: set[str] = set()
        for fields in present.values():
            all_fields.update(fields.keys())

        for field in sorted(all_fields):
            values_by_source = {
                src: fields[field]
                for src, fields in present.items()
                if field in fields
            }
            if len(values_by_source) < 2:
                continue

            unique_values = set(str(v) for v in values_by_source.values())
            if len(unique_values) > 1:
                discrepancies.append({
                    "site": norm_name,
                    "field": field,
                    "values": values_by_source,
                })

    return discrepancies


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def build_report(discrepancies: list[dict], loaded: dict) -> str:
    source_names = list(loaded.keys())
    lines = []

    if not discrepancies:
        lines.append("\nNo discrepancies found — all shared fields agree across sources.")
        return "\n".join(lines)

    by_field: dict[str, list] = defaultdict(list)
    for d in discrepancies:
        by_field[d["field"]].append(d)

    lines.append(f"\n{'='*60}")
    lines.append(f"  {len(discrepancies)} discrepancies across {len(loaded)} sources")
    lines.append(f"{'='*60}")

    for field, items in sorted(by_field.items()):
        lines.append(f"\n[{field.upper()}]  ({len(items)} sites differ)")
        header = f"  {'Site':<35}"
        for src in source_names:
            if src in loaded:
                header += f"  {src:<20}"
        lines.append(header)
        divider = f"  {'-'*35}"
        for src in source_names:
            if src in loaded:
                divider += f"  {'-'*20}"
        lines.append(divider)

        for item in items:
            row = f"  {item['site']:<35}"
            for src in source_names:
                if src not in loaded:
                    continue
                val = item["values"].get(src, "—")
                row += f"  {str(val):<20}"
            lines.append(row)

    lines.append(f"\n{'='*60}")
    lines.append("Summary by field:")
    for field, items in sorted(by_field.items(), key=lambda x: -len(x[1])):
        lines.append(f"  {field:<25} {len(items)} sites disagree")
    lines.append(f"{'='*60}")

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading sources...")
    loaded = load_sources()

    if len(loaded) < 2:
        print("\nNeed at least 2 source files to compare. Run the scrapers first.")
        return

    print("\nComparing...")
    discrepancies = find_discrepancies(loaded)
    report = build_report(discrepancies, loaded)

    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(report)

    print(f"Report saved to {REPORT_FILE}")


if __name__ == "__main__":
    main()
