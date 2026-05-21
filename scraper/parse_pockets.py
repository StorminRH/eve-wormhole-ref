"""
Post-processing script: parse detailed pocket/wave/NPC data from raw_text.

We already have the raw page text in sites.json. This script re-reads that
text and extracts structured pocket data (waves, NPCs, triggers, DPS) without
hitting the network again.

Run with:
    python parse_pockets.py
"""

import json
import re
from pathlib import Path

DATA_FILE = Path(__file__).parent.parent / "data" / "sites.json"

# ── Text cleanup ──────────────────────────────────────────────────────────────

def clean_raw_text(raw: str) -> str:
    """
    The raw text has lots of blank lines and line-wrapped NPC names
    (because HTML links create separate text nodes). Collapse it so
    each logical entry is on one line.
    """
    lines = [line.strip() for line in raw.splitlines()]

    merged = []
    i = 0
    while i < len(lines):
        line = lines[i]
        if not line:
            i += 1
            continue

        # NPC count lines look like "2x Cruisers (" — the name may be
        # on the next line, then ")" on the line after, and TRIGGER on
        # the line after that. Join all of them onto one line.
        if re.match(r'^\d+x\s+\w', line) and line.endswith('('):
            name = lines[i + 1].strip() if i + 1 < len(lines) else ''
            rest = lines[i + 2].strip() if i + 2 < len(lines) else ''
            line = f"{line}{name}{rest}"
            i += 3
            # TRIGGER and bracket tags [web] can appear on their own lines
            # after the closing paren. Consume them onto the same line.
            while i < len(lines):
                token = lines[i].strip()
                if token.upper() == 'TRIGGER':
                    line = f"{line} TRIGGER"
                    i += 1
                elif re.match(r'^\[', token):
                    line = f"{line} {token}"
                    i += 1
                elif token == '':
                    i += 1  # skip blank lines between parts
                else:
                    break   # next real content — stop consuming
        else:
            i += 1

        merged.append(line)

    return '\n'.join(merged)


# ── Section detection ─────────────────────────────────────────────────────────

# These words mark the start of a new pocket or wave group
SECTION_PATTERN = re.compile(
    r'^(Single Pocket|Pocket \d+|Initial Group|Reinforcements? Wave \d+|Wave \d+)',
    re.IGNORECASE
)

DPS_LINE = re.compile(r'^(\d+)\s+DPS$', re.IGNORECASE)
MAX_DPS_LINE = re.compile(r'Max Incoming DPS:\s*([\d,]+)\s*\(([^)]+)\)', re.IGNORECASE)

# Matches NPC entries like:
#   2x Cruisers (Awakened Escort) TRIGGER
#   1x Frigate (Emergent Escort) [web]
#   3x Battleships (Sleepless Sentinel) [scram, web, nos]
NPC_PATTERN = re.compile(
    r'^(\d+)x\s+(\w[\w\s]*?)\s*\(([^)]+)\)'  # count, ship type, name
    r'((?:\s*\[[^\]]+\])*)'                    # optional [tag] blocks
    r'(\s+TRIGGER)?'                           # optional TRIGGER marker
    r'\s*$',
    re.IGNORECASE
)


# ── Pocket parser ─────────────────────────────────────────────────────────────

def parse_pockets(raw_text: str) -> list[dict]:
    """
    Parse the raw page text into a list of pocket/wave objects.
    Each pocket looks like:
      {
        "name": "Initial Group",
        "initial_dps": 180,
        "npcs": [
          {"count": 1, "ship_type": "Cruiser", "name": "Awakened Escort",
           "tags": [], "trigger": true},
          ...
        ],
        "max_dps": 157,
        "dps_breakdown": "EM/The 35%, Kin/Exp 15%"
      }
    """
    text = clean_raw_text(raw_text)
    lines = text.splitlines()

    pockets = []
    current = None

    for line in lines:
        # New section?
        section_match = SECTION_PATTERN.match(line)
        if section_match:
            if current is not None:
                pockets.append(current)
            current = {
                'name': section_match.group(1),
                'initial_dps': None,
                'npcs': [],
                'max_dps': None,
                'dps_breakdown': None,
            }
            continue

        if current is None:
            continue  # skip lines before the first section header

        # Initial DPS line (e.g. "180 DPS")
        dps_match = DPS_LINE.match(line)
        if dps_match:
            current['initial_dps'] = int(dps_match.group(1))
            continue

        # Max DPS line (e.g. "Max Incoming DPS: 157 (EM/The 35%, Kin/Exp 15%)")
        max_dps_match = MAX_DPS_LINE.search(line)
        if max_dps_match:
            current['max_dps'] = int(max_dps_match.group(1).replace(',', ''))
            current['dps_breakdown'] = max_dps_match.group(2).strip()
            continue

        # NPC entry?
        npc_match = NPC_PATTERN.match(line)
        if npc_match:
            count, ship_type, name, tag_block, trigger_marker = npc_match.groups()

            # Pull individual tags out of "[scram, web, nos]" style strings
            tags = re.findall(r'\[([^\]]+)\]', tag_block or '')
            tags = [t.strip().lower() for block in tags for t in block.split(',')]
            tags = [t for t in tags if t]  # remove empties

            current['npcs'].append({
                'count': int(count),
                'ship_type': ship_type.strip().rstrip('s'),  # normalize plural
                'name': name.strip(),
                'tags': tags,
                'trigger': bool(trigger_marker),
            })

    # Don't forget the last section
    if current is not None:
        pockets.append(current)

    # Drop empty/header-only pockets (e.g. "Single Pocket" with no NPCs)
    return [p for p in pockets if p['npcs']]


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    with open(DATA_FILE, encoding='utf-8') as f:
        sites = json.load(f)

    parsed_count = 0
    for site in sites:
        raw = site.get('raw_text', '')
        if not raw:
            continue

        pockets = parse_pockets(raw)
        if pockets:
            site['pockets'] = pockets
            parsed_count += 1

    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(sites, f, indent=2, ensure_ascii=False)

    print(f"Done. Parsed pocket data for {parsed_count}/{len(sites)} sites.")

    # Print a sample so we can verify it looks right
    sample = next((s for s in sites if s.get('pockets')), None)
    if sample:
        print(f"\nSample — {sample['display_name']}:")
        print(json.dumps(sample['pockets'], indent=2))


if __name__ == '__main__':
    main()
