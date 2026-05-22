"""
Database Migration Script
Merges all three scraped JSON sources into a single PostgreSQL table.

Wave merge strategy:
  - EVE Uni is primary: provides per-NPC DPS, EHP, and wave grouping
  - eve-survival overlays trigger flags and ewar tags per NPC (matched by name)
  - Sites with no EVE Uni wave data fall back to eve-survival pockets

Other field strategy:
  - wh_class       → whdb/eveuni preferred
  - site_type      → eveuni preferred
  - loot_isk       → eveuni preferred, eve-survival fallback
  - webbers/scrams → eve-survival preferred (site-level summary text)

Run with:
    pip install psycopg2-binary
    DATABASE_URL="postgresql://..." python migrate_to_db.py
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

DATA_DIR = Path(__file__).parent.parent / "data"

DATABASE_URL = os.environ.get("DATABASE_URL")
DB_CONFIG = (
    {"dsn": DATABASE_URL}
    if DATABASE_URL
    else {
        "host": "localhost",
        "port": 5432,
        "dbname": "wormholes",
        "user": "wh_user",
        "password": "wh_pass",
    }
)

# ---------------------------------------------------------------------------
# General helpers
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (name or "").lower())


def normalize_class(value: str) -> str | None:
    if not value:
        return None
    m = re.search(r"(\d)", str(value))
    return f"C{m.group(1)}" if m else value.strip().upper()


def normalize_loot(value) -> int | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    s = str(value).strip().lower()
    m = re.search(r"([\d.]+)\s*m", s)
    if m:
        return int(float(m.group(1)) * 1_000_000)
    m = re.search(r"(\d[\d,]*)", s)
    if m:
        digits = m.group(1).replace(",", "")
        return int(digits) if digits else None
    return None


def load_json(filename: str) -> list[dict]:
    path = DATA_DIR / filename
    if not path.exists():
        print(f"  [skip] {filename} not found")
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def index_by_name(records: list[dict]) -> dict[str, dict]:
    out = {}
    for r in records:
        if not isinstance(r, dict):
            continue
        display = r.get("display_name") or r.get("name") or ""
        key = normalize_name(display)
        if key:
            out[key] = r
    return out


# ---------------------------------------------------------------------------
# EVE Uni wave parsing
# ---------------------------------------------------------------------------

def parse_dps(dv_str: str) -> int:
    """
    Parse DPS from EVE Uni 'DPSVolley (cycle s)' strings like '14112 (9 s)'.
    EVE Uni concatenates the DPS and Volley columns with no separator.
    We recover DPS by finding the split where Volley / cycle ≈ DPS (within 20%).
    """
    if not dv_str:
        return 0
    m = re.match(r"^([\d,]+)\s*\((\d+)\s*s\)", dv_str.strip())
    if not m:
        return 0
    number = m.group(1).replace(",", "")
    cycle = int(m.group(2))
    if cycle == 0 or not number:
        return 0
    for split in [2, 3]:
        if len(number) > split:
            dps_c = int(number[:split])
            vol_c = int(number[split:])
            if dps_c > 0 and abs(vol_c / cycle - dps_c) / dps_c < 0.20:
                return dps_c
    return 0


def parse_npc_entry(ship_name: str) -> tuple[int, str]:
    """Parse '2x Emergent Escort' → (2, 'Emergent Escort')."""
    m = re.match(r"^(\d+)x\s+(.+)$", ship_name.strip())
    if m:
        return int(m.group(1)), m.group(2).strip()
    return 1, ship_name.strip()


def group_eveuni_waves(eveuni_site: dict) -> list[dict]:
    """
    Convert EVE Uni's flat NPC list (with embedded wave-header rows) into
    a list of wave dicts: [{name, dps, npcs: [{count, name, dps_per_ship, ehp}]}]
    """
    raw_waves = eveuni_site.get("waves") or []
    if not raw_waves:
        return []

    # All data lives in waves[0].npcs — multiple logical waves are separated by header rows
    all_rows = raw_waves[0].get("npcs", []) if raw_waves else []

    waves: list[dict] = []
    current_name: str | None = None
    current_npcs: list[dict] = []

    def flush():
        if current_npcs:
            wave_dps = sum(n["count"] * n["dps_per_ship"] for n in current_npcs)
            waves.append({
                "name": current_name or "Wave 1",
                "dps": wave_dps,
                "npcs": current_npcs,
            })

    for row in all_rows:
        if not isinstance(row, dict):
            continue
        ship_name = row.get("sleeper_ship_name", "")
        header    = row.get("", "")

        if ship_name:
            count, name = parse_npc_entry(ship_name)
            current_npcs.append({
                "count":        count,
                "name":         name,
                "ship_type":    "",
                "dps_per_ship": parse_dps(row.get("dps/volley", "")),
                "ehp":          int(row.get("ehp") or 0),
                "trigger":      False,
                "tags":         [],
            })
        elif header and header.strip():
            # Wave header row — flush the previous wave and start a new one
            flush()
            current_name = header.strip()
            current_npcs = []

    flush()
    return waves


# ---------------------------------------------------------------------------
# eve-survival overlay (trigger + ewar tags)
# ---------------------------------------------------------------------------

def build_survival_lookup(survival_site: dict) -> dict[str, dict]:
    """
    Build a name → {trigger, tags, ship_type} lookup from eve-survival pockets.
    Multiple pockets may define the same ship name with different roles; we
    merge tags and take trigger=True if any pocket marks it so.
    """
    lookup: dict[str, dict] = {}
    for pocket in (survival_site.get("pockets") or []):
        if not isinstance(pocket, dict):
            continue
        for npc in pocket.get("npcs", []):
            if not isinstance(npc, dict):
                continue
            key = normalize_name(npc.get("name") or "")
            if not key:
                continue
            if key not in lookup:
                lookup[key] = {
                    "trigger":   npc.get("trigger", False),
                    "tags":      list(npc.get("tags") or []),
                    "ship_type": npc.get("ship_type", ""),
                }
            else:
                # Merge: trigger is OR, tags are union
                lookup[key]["trigger"] = lookup[key]["trigger"] or npc.get("trigger", False)
                existing_tags = set(lookup[key]["tags"])
                for t in (npc.get("tags") or []):
                    existing_tags.add(t)
                lookup[key]["tags"] = list(existing_tags)
                if not lookup[key]["ship_type"] and npc.get("ship_type"):
                    lookup[key]["ship_type"] = npc["ship_type"]
    return lookup


def overlay_survival(waves: list[dict], survival_site: dict) -> list[dict]:
    """Stamp trigger flags and ewar tags from eve-survival onto EVE Uni NPC records."""
    lookup = build_survival_lookup(survival_site)
    for wave in waves:
        for npc in wave.get("npcs", []):
            key = normalize_name(npc["name"])
            if key in lookup:
                npc["trigger"]   = lookup[key]["trigger"]
                npc["tags"]      = lookup[key]["tags"]
                if not npc["ship_type"]:
                    npc["ship_type"] = lookup[key]["ship_type"]
    return waves


# ---------------------------------------------------------------------------
# Survival pockets → unified wave format (fallback when no EVE Uni data)
# ---------------------------------------------------------------------------

def survival_to_waves(survival_site: dict) -> list[dict]:
    """Convert eve-survival pockets to the unified wave format."""
    waves = []
    for pocket in (survival_site.get("pockets") or []):
        if not isinstance(pocket, dict):
            continue
        npcs = []
        for npc in pocket.get("npcs", []):
            if not isinstance(npc, dict):
                continue
            npcs.append({
                "count":        npc.get("count", 1),
                "name":         npc.get("name", ""),
                "ship_type":    npc.get("ship_type", ""),
                "dps_per_ship": 0,
                "ehp":          0,
                "trigger":      npc.get("trigger", False),
                "tags":         list(npc.get("tags") or []),
            })
        if not npcs:
            continue
        dps = pocket.get("max_dps") or pocket.get("initial_dps") or 0
        waves.append({
            "name": pocket.get("name", "Wave 1"),
            "dps":  dps or 0,
            "npcs": npcs,
        })
    return waves


# ---------------------------------------------------------------------------
# Site merge
# ---------------------------------------------------------------------------

def merge_site(key: str, survival: dict, whdb: dict, eveuni: dict) -> dict | None:
    display_name = (
        eveuni.get("display_name")
        or survival.get("display_name")
        or whdb.get("display_name")
        or key
    )

    raw_class = (
        whdb.get("wh_class")
        or eveuni.get("wh_class")
        or survival.get("wh_class")
    )
    wh_class = normalize_class(raw_class) if raw_class and raw_class.upper() != "CLASSLESS" else None

    site_type = eveuni.get("site_type") or survival.get("site_type")

    # Skip stub entries scraped from wiki nav pages with no real content
    euni_npc_count = sum(
        1 for wb in eveuni.get("waves", [])
        for npc in wb.get("npcs", [])
        if isinstance(npc, dict) and npc.get("sleeper_ship_name")
    )
    surv_npc_count = sum(
        len(p.get("npcs", []))
        for p in (survival.get("pockets") or [])
        if isinstance(p, dict)
    )
    if euni_npc_count == 0 and surv_npc_count == 0 and not wh_class and not site_type:
        return None

    # Waves: EVE Uni primary (richer per-NPC stats), overlaid with survival triggers/ewar
    if euni_npc_count > 0:
        waves = group_eveuni_waves(eveuni)
        waves = overlay_survival(waves, survival)
    else:
        waves = survival_to_waves(survival)

    faction           = survival.get("faction") or eveuni.get("faction")
    damage_dealt      = survival.get("damage_dealt") or eveuni.get("damage_dealt")
    webbers           = survival.get("webbers") or eveuni.get("webbers")
    scramblers        = survival.get("scramblers") or eveuni.get("scramblers")
    recommended_ships = survival.get("recommended_ships") or eveuni.get("recommended_ships")

    eveuni_loot  = normalize_loot(eveuni.get("blue_loot"))
    survival_loot = normalize_loot(survival.get("blue_loot_value"))
    if survival_loot is not None and survival_loot < 100:
        survival_loot = None
    loot_isk = eveuni_loot or survival_loot

    return {
        "key":              key,
        "display_name":     display_name,
        "wh_class":         wh_class,
        "site_type":        site_type,
        "faction":          faction,
        "damage_dealt":     damage_dealt,
        "webbers":          webbers,
        "scramblers":       scramblers,
        "recommended_ships": recommended_ships,
        "loot_isk":         loot_isk,
        "dps":              whdb.get("dps"),
        "total_ehp":        whdb.get("total_ehp"),
        "waves":            waves or None,
    }


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sites (
    id               SERIAL PRIMARY KEY,
    key              TEXT UNIQUE NOT NULL,
    display_name     TEXT NOT NULL,
    wh_class         TEXT,
    site_type        TEXT,
    faction          TEXT,
    damage_dealt     TEXT,
    webbers          TEXT,
    scramblers       TEXT,
    recommended_ships TEXT,
    loot_isk         BIGINT,
    dps              INTEGER,
    total_ehp        BIGINT,
    waves            JSONB,
    created_at       TIMESTAMPTZ DEFAULT NOW(),
    updated_at       TIMESTAMPTZ DEFAULT NOW()
);
"""

UPSERT = """
INSERT INTO sites (
    key, display_name, wh_class, site_type, faction,
    damage_dealt, webbers, scramblers, recommended_ships,
    loot_isk, dps, total_ehp, waves
) VALUES (
    %(key)s, %(display_name)s, %(wh_class)s, %(site_type)s, %(faction)s,
    %(damage_dealt)s, %(webbers)s, %(scramblers)s, %(recommended_ships)s,
    %(loot_isk)s, %(dps)s, %(total_ehp)s, %(waves)s
)
ON CONFLICT (key) DO UPDATE SET
    display_name      = EXCLUDED.display_name,
    wh_class          = EXCLUDED.wh_class,
    site_type         = EXCLUDED.site_type,
    faction           = EXCLUDED.faction,
    damage_dealt      = EXCLUDED.damage_dealt,
    webbers           = EXCLUDED.webbers,
    scramblers        = EXCLUDED.scramblers,
    recommended_ships = EXCLUDED.recommended_ships,
    loot_isk          = EXCLUDED.loot_isk,
    dps               = EXCLUDED.dps,
    total_ehp         = EXCLUDED.total_ehp,
    waves             = EXCLUDED.waves,
    updated_at        = NOW();
"""

DELETE_REMOVED = """
DELETE FROM sites WHERE key = ANY(%(keys)s);
"""


def run_migration(sites: list[dict], all_keys: set[str]) -> None:
    print(f"\nConnecting to database...")
    conn = psycopg2.connect(**DB_CONFIG)  # type: ignore[arg-type]
    cur = conn.cursor()

    cur.execute(CREATE_TABLE)

    # Remove junk entries that may exist from a previous run
    valid_keys = {s["key"] for s in sites}
    removed = list(all_keys - valid_keys)
    if removed:
        cur.execute(DELETE_REMOVED, {"keys": removed})
        print(f"Removed {len(removed)} stale/junk entries: {removed}")

    print(f"Inserting/updating {len(sites)} sites...")
    for site in sites:
        row = {**site, "waves": Json(site["waves"]) if site["waves"] else None}
        cur.execute(UPSERT, row)

    conn.commit()
    cur.close()
    conn.close()
    print("Done.")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    print("Loading sources...")
    survival_records = load_json("sites.json")
    whdb_records     = load_json("sites_whdb.json")
    eveuni_records   = load_json("sites_eveuni.json")

    survival = index_by_name(survival_records)
    whdb     = index_by_name(whdb_records)
    eveuni   = index_by_name(eveuni_records)

    print(f"  eve-survival: {len(survival)} sites")
    print(f"  whdb:         {len(whdb)} sites")
    print(f"  eveuni:       {len(eveuni)} sites")

    all_keys = set(survival) | set(whdb) | set(eveuni)
    print(f"  Total unique: {len(all_keys)} sites")

    print("\nMerging...")
    merged = []
    skipped = []
    for key in sorted(all_keys):
        result = merge_site(
            key,
            survival.get(key, {}),
            whdb.get(key, {}),
            eveuni.get(key, {}),
        )
        if result is None:
            skipped.append(key)
        else:
            merged.append(result)

    if skipped:
        print(f"  Skipped {len(skipped)} stub entries: {skipped}")

    run_migration(merged, all_keys)

    with_loot  = sum(1 for s in merged if s["loot_isk"])
    with_class = sum(1 for s in merged if s["wh_class"])
    with_waves = sum(1 for s in merged if s["waves"])
    with_ewar  = sum(
        1 for s in merged
        if s["waves"] and any(
            npc.get("tags")
            for w in s["waves"] for npc in w.get("npcs", [])
        )
    )
    print(f"\nSummary:")
    print(f"  {len(merged)} sites migrated ({len(skipped)} stubs skipped)")
    print(f"  {with_class} have a WH class")
    print(f"  {with_loot}  have a loot value")
    print(f"  {with_waves} have wave/NPC data")
    print(f"  {with_ewar}  have per-NPC ewar tags")


if __name__ == "__main__":
    main()
