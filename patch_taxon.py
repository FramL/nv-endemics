#!/usr/bin/env python3
"""
patch_taxon.py

Safely re-fetches data for ONE taxon and merges it into the existing
data/occurrences.geojson, without touching any other taxon's records.

This exists because running `fetch_occurrences.py --taxon "X"` overwrites
the ENTIRE occurrences.geojson with just that one taxon's results — useful
for testing, but destructive if you just want to patch one taxon after a
full run. This script merges instead of overwriting.

Usage:
    python patch_taxon.py "Eriogonum kingii"                # GBIF + iNat
    python patch_taxon.py "Eriogonum kingii" --skip-inat     # GBIF only
    python patch_taxon.py "Eriogonum kingii" --skip-gbif     # iNat only

Run this from the same folder as fetch_occurrences.py (it imports from it).
"""

import argparse
import csv
import json
import sys
import time
from pathlib import Path

# Reuse the exact same fetch logic as the main script, so results are
# identical in shape/fields to what a full run would have produced.
from fetch_occurrences import (
    fetch_gbif, fetch_inat, TAXA_CSV, OUTPUT_GEOJSON, OUTPUT_TAXA_JSON,
    GBIF_RATE_LIMIT, INAT_RATE_LIMIT,
)


def load_taxon_row(taxon_name: str) -> dict:
    """Find this taxon's row in taxa.csv to pull its metadata (genus, status, etc.)."""
    with open(TAXA_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row["taxon"].strip().lower() == taxon_name.strip().lower():
                return row
    return None


def main():
    parser = argparse.ArgumentParser(description="Patch one taxon's data into occurrences.geojson")
    parser.add_argument("taxon", type=str, help="Exact taxon name to re-fetch and merge")
    parser.add_argument("--skip-gbif", action="store_true", help="Skip GBIF (herbarium) fetch")
    parser.add_argument("--skip-inat", action="store_true", help="Skip iNat fetch")
    args = parser.parse_args()

    taxon_name = args.taxon

    row = load_taxon_row(taxon_name)
    if row is None:
        print(f"ERROR: '{taxon_name}' not found in {TAXA_CSV}. Check exact spelling/capitalization.")
        sys.exit(1)

    print(f"Patching: {taxon_name}")
    print(f"  (matched taxa.csv row: genus={row.get('genus')}, "
          f"category={row.get('endemism_category')})")

    # Load existing GeoJSON
    if not OUTPUT_GEOJSON.exists():
        print(f"ERROR: {OUTPUT_GEOJSON} doesn't exist yet. Run the full fetch_occurrences.py first.")
        sys.exit(1)

    with open(OUTPUT_GEOJSON, encoding="utf-8") as f:
        geojson = json.load(f)

    all_features = geojson.get("features", [])
    before_count = len(all_features)

    # Remove ALL existing records for this taxon (both herbarium and iNat),
    # so the patch fully replaces stale/partial data rather than appending
    # duplicates alongside it.
    all_features = [f for f in all_features if f["properties"].get("taxon") != taxon_name]
    removed_count = before_count - len(all_features)
    print(f"  Removed {removed_count} existing record(s) for this taxon (will be replaced).")

    new_features = []

    metadata_update = {
        "genus": row.get("genus", ""),
        "endemism_category": row.get("endemism_category", "endemic"),
        "esa_status": row.get("esa_status", ""),
        "nv_state_status": row.get("nv_state_status", ""),
        "book_thematic_category": row.get("book_thematic_category", ""),
        "physiographic_region": row.get("physiographic_region", ""),
        "physiographic_section": row.get("physiographic_section", ""),
    }

    if not args.skip_gbif:
        print("  Fetching GBIF (herbarium)...")
        gbif_features = fetch_gbif(taxon_name)
        for f in gbif_features:
            f["properties"].update(metadata_update)
        new_features.extend(gbif_features)
        print(f"    Got {len(gbif_features)} herbarium records.")
        time.sleep(GBIF_RATE_LIMIT)

    if not args.skip_inat:
        print("  Fetching iNaturalist...")
        inat_features = fetch_inat(taxon_name)
        for f in inat_features:
            f["properties"].update(metadata_update)
        new_features.extend(inat_features)
        print(f"    Got {len(inat_features)} iNat records.")
        time.sleep(INAT_RATE_LIMIT)

    if not new_features:
        print("  WARNING: Got zero new records. NOT writing changes — "
              "this is almost certainly a fetch error, not a real zero result.")
        print("  (If you intended to clear this taxon's data, do so manually.)")
        sys.exit(1)

    all_features.extend(new_features)
    geojson["features"] = all_features

    with open(OUTPUT_GEOJSON, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)

    print(f"\nDone. {OUTPUT_GEOJSON} now has {len(all_features)} total features "
          f"({before_count} before this patch, {len(new_features)} new for {taxon_name}).")

    # Also make sure this taxon exists in taxa_metadata.json (used by the
    # info panel) — add it if it's somehow missing, otherwise leave as-is.
    if OUTPUT_TAXA_JSON.exists():
        with open(OUTPUT_TAXA_JSON, encoding="utf-8") as f:
            taxa_meta = json.load(f)
        if taxon_name not in taxa_meta:
            taxa_meta[taxon_name] = {"taxon": taxon_name, **metadata_update, "notes": row.get("notes", "")}
            with open(OUTPUT_TAXA_JSON, "w", encoding="utf-8") as f:
                json.dump(taxa_meta, f, ensure_ascii=False, indent=2)
            print(f"  Added '{taxon_name}' to {OUTPUT_TAXA_JSON} (was missing).")


if __name__ == "__main__":
    main()
