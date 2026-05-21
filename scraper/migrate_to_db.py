"""
Database Migration Script
Merges all three scraped JSON sources into a single PostgreSQL table.

Merge strategy (best-of-source per field):
  - wh_class       → whdb/eveuni preferred (both use ORE/GAS/C1-C6)
  - site_type      → eveuni preferred (current EVE naming: Ore/Gas/Relic/Data)
  - faction        → eve-survival preferred (most complete)
  - damage_dealt   → eve-survival preferred
  - loot_isk       → eveuni preferred, eve-survival fallback
  - waves/npcs     → eve-survival preferred (most detailed)
  - dps/ehp        → whdb (only source with these fields)

Run with:
    pip install psycopg2-binary
    python migrate_to_db.py

Expects PostgreSQL running at localhost:5432 (via docker-compose).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import psycopg2
from psycopg2.extras import Json

DATA_DIR = Path(__file__).parent.parent / "data"

DB_CONFIG = {
    "host": "localhost",
    "port": 5432,
    "dbname": "wormholes",
    "user": "wh_user",
    "password": "wh_pass",
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def normalize_name(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", name.lower())


def normalize_class(value: str) -> str:
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


# ---------------------------------------------------------------------------
# Load and index sources
# ---------------------------------------------------------------------------

def index_by_name(records: list[dict]) -> dict[str, dict]:
    out = {}
    for r in records:
        display = r.get("display_name") or r.get("name") or ""
        key = normalize_name(display)
        if key:
            out[key] = r
    return out


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def merge_site(key: str, survival: dict, whdb: dict, eveuni: dict) -> dict:
    """
    Build one merged site record using best-of-source per field.
    All three dicts may be empty ({}) if that source doesn't have the site.
    """

    # Display name — prefer the most readable version
    display_name = (
        eveuni.get("display_name")
        or survival.get("display_name")
        or whdb.get("display_name")
        or key
    )

    # WH class — whdb and eveuni both use C1-C6/ORE/GAS; eve-survival uses CLASSLESS
    raw_class = (
        whdb.get("wh_class")
        or eveuni.get("wh_class")
        or survival.get("wh_class")
    )
    # Treat CLASSLESS as None — these sites appear across all classes
    wh_class = normalize_class(raw_class) if raw_class and raw_class.upper() != "CLASSLESS" else None

    # Site type — eveuni uses current EVE names (Ore, Gas, Relic, Data, Cosmic Anomaly)
    site_type = eveuni.get("site_type") or survival.get("site_type")

    # Faction and combat info — eve-survival is most complete
    faction = survival.get("faction") or eveuni.get("faction")
    damage_dealt = survival.get("damage_dealt") or eveuni.get("damage_dealt")
    webbers = survival.get("webbers") or eveuni.get("webbers")
    scramblers = survival.get("scramblers") or eveuni.get("scramblers")
    recommended_ships = survival.get("recommended_ships") or eveuni.get("recommended_ships")

    # Loot — eveuni preferred (more current), survival fallback
    eveuni_loot = normalize_loot(eveuni.get("blue_loot"))
    survival_loot = normalize_loot(survival.get("blue_loot_value"))
    # Treat obviously broken values (< 100 ISK) as missing
    if survival_loot is not None and survival_loot < 100:
        survival_loot = None
    loot_isk = eveuni_loot or survival_loot

    # DPS and EHP — whdb only
    dps = whdb.get("dps")
    total_ehp = whdb.get("total_ehp")

    # Wave/NPC data — eve-survival has the most structured pocket data
    waves = survival.get("pockets") or eveuni.get("waves")

    return {
        "key": key,
        "display_name": display_name,
        "wh_class": wh_class,
        "site_type": site_type,
        "faction": faction,
        "damage_dealt": damage_dealt,
        "webbers": webbers,
        "scramblers": scramblers,
        "recommended_ships": recommended_ships,
        "loot_isk": loot_isk,
        "dps": dps,
        "total_ehp": total_ehp,
        "waves": waves,
    }


# ---------------------------------------------------------------------------
# Database
# ---------------------------------------------------------------------------

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS sites (
    id               SERIAL PRIMARY KEY,
    key              TEXT UNIQUE NOT NULL,      -- normalized name used for matching
    display_name     TEXT NOT NULL,
    wh_class         TEXT,                      -- C1-C6, ORE, GAS, ICE, or NULL (classless)
    site_type        TEXT,                      -- Cosmic Anomaly, Data, Relic, Gas, Ore
    faction          TEXT,
    damage_dealt     TEXT,
    webbers          TEXT,
    scramblers       TEXT,
    recommended_ships TEXT,
    loot_isk         BIGINT,                    -- blue loot value in ISK
    dps              INTEGER,                   -- estimated incoming DPS
    total_ehp        BIGINT,                    -- total NPC hitpoints
    waves            JSONB,                     -- full wave/NPC structure
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


def run_migration(sites: list[dict]) -> None:
    print(f"\nConnecting to database...")
    conn = psycopg2.connect(**DB_CONFIG)
    cur = conn.cursor()

    print("Creating table if it doesn't exist...")
    cur.execute(CREATE_TABLE)

    print(f"Inserting/updating {len(sites)} sites...")
    for site in sites:
        # JSONB columns need the Json wrapper
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

    # Union of all known site keys
    all_keys = set(survival) | set(whdb) | set(eveuni)
    print(f"  Total unique: {len(all_keys)} sites")

    print("\nMerging...")
    merged = [
        merge_site(
            key,
            survival.get(key, {}),
            whdb.get(key, {}),
            eveuni.get(key, {}),
        )
        for key in sorted(all_keys)
    ]

    run_migration(merged)

    # Quick summary
    with_loot  = sum(1 for s in merged if s["loot_isk"])
    with_class = sum(1 for s in merged if s["wh_class"])
    with_waves = sum(1 for s in merged if s["waves"])
    print(f"\nSummary:")
    print(f"  {len(merged)} total sites")
    print(f"  {with_class} have a WH class")
    print(f"  {with_loot}  have a loot value")
    print(f"  {with_waves} have wave/NPC data")


if __name__ == "__main__":
    main()
