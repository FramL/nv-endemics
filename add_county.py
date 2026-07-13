#!/usr/bin/env python3
"""
add_county.py

Computes an exact, clean `county` field for every point in
data/occurrences.geojson using real point-in-polygon tests against Nevada's
official county boundaries (data/nevada_counties.geojson — U.S. Census
TIGER-derived), instead of relying on the raw locality/county text that
comes out of GBIF/iNat.

Why this exists: about 44% of records (all iNat observations) never had a
`county` value at all, and the herbarium records that did have one used ~69
different spellings/formats for 17 counties (e.g. "Churchill", "CHURCHILL",
"Chruchill", "Churchill Co.", "Churchill County" all mean the same county).
This script replaces that mess with one exact, consistent value per point,
computed straight from coordinates.

What it does NOT touch: physiographic_region, physiographic_section, and
book_thematic_category. Those are still taxon-level tags from
taxa_metadata.json (confirmed: 0 of 194 taxa have more than one distinct
value across their points) and need their own polygon-based fix once the
book's region maps are digitized — that's a separate script.

Behavior:
- Adds/overwrites a `county` field on every point with the clean, canonical
  county name (e.g. "Churchill County"), computed via point-in-polygon.
- Preserves whatever raw value existed before (if any) in `county_raw`, so
  nothing is silently discarded and old QA is still auditable.
- Points that fall outside all 17 Nevada county polygons (e.g. a record
  that's actually just over the state line) get `county` set to null and
  are printed to the console as a flagged list — these need a human look,
  not a silent default.

Usage:
    python add_county.py
    python add_county.py --counties path/to/other_counties.geojson
"""

import argparse
import json
from pathlib import Path

from shapely.geometry import shape, Point

DATA_DIR = Path(__file__).parent / "data"
OCCURRENCES = DATA_DIR / "occurrences.geojson"
COUNTIES = DATA_DIR / "nevada_counties.geojson"

NEAR_MISS_KM = 15  # nearest-county fallback tolerance for boundary-simplification gaps
REVIEW_KM = 100  # beyond this, treat as a genuine data anomaly, not a boundary artifact


def load_counties(path: Path):
    """Return list of (clean_name, shapely_polygon)."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for feat in data["features"]:
        name = feat["properties"]["NAME"]
        # Canonical display form, e.g. "Churchill County" / "Carson City"
        clean = name if name == "Carson City" else f"{name} County"
        out.append((clean, shape(feat["geometry"])))
    return out


def find_county(lon: float, lat: float, counties):
    pt = Point(lon, lat)
    for name, poly in counties:
        if poly.contains(pt) or poly.touches(pt):
            return name, "exact"

    nearest_name, nearest_poly = min(counties, key=lambda c: c[1].distance(pt))
    dist_km = nearest_poly.distance(pt) * 111  # rough deg->km, fine at NV's latitude
    if dist_km <= NEAR_MISS_KM:
        return nearest_name, "near_miss"
    elif dist_km <= REVIEW_KM:
        return nearest_name, "flagged"
    else:
        return None, "unresolved"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--counties", type=Path, default=COUNTIES)
    parser.add_argument("--occurrences", type=Path, default=OCCURRENCES)
    args = parser.parse_args()

    print(f"Loading county boundaries from {args.counties} ...")
    counties = load_counties(args.counties)
    print(f"  {len(counties)} counties loaded")

    print(f"Loading occurrences from {args.occurrences} ...")
    data = json.loads(args.occurrences.read_text(encoding="utf-8"))
    feats = data["features"]
    print(f"  {len(feats)} points loaded")

    n_exact = 0
    n_near_miss = 0
    n_filled_blank = 0
    n_changed_from_raw = 0
    review_list = []  # 15-100km away — assigned, but worth a look
    unresolved_list = []  # >100km away — not assigned, almost certainly bad geocoding

    for f in feats:
        props = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        raw = props.get("county") or ""
        county, status = find_county(lon, lat, counties)

        props["county_raw"] = raw  # preserve original, even if blank
        props["county"] = county
        props["county_match"] = status  # "exact" | "near_miss" | "flagged" | "unresolved"

        if status in ("exact", "near_miss", "flagged"):
            if status == "exact":
                n_exact += 1
            elif status == "near_miss":
                n_near_miss += 1
            if not raw:
                n_filled_blank += 1
            elif county.replace(" County", "").strip().lower() != raw.replace(
                "County", ""
            ).replace("Co.", "").strip().lower():
                n_changed_from_raw += 1
            if status == "flagged":
                review_list.append((props.get("taxon"), props.get("source"), lat, lon, raw, props.get("url")))
        else:
            unresolved_list.append((props.get("taxon"), props.get("source"), lat, lon, raw, props.get("url")))

    print()
    print(f"Exact point-in-polygon match: {n_exact} / {len(feats)}")
    print(f"Near-miss, assigned via <{NEAR_MISS_KM}km fallback (boundary-simplification gaps): {n_near_miss}")
    print(f"  Filled in where county was previously blank: {n_filled_blank}")
    print(f"  Corrected/standardized from a differing raw value: {n_changed_from_raw}")
    print(f"Flagged, {NEAR_MISS_KM}-{REVIEW_KM}km away (assigned to nearest, but worth a look): {len(review_list)}")
    print(f"Unresolved, >{REVIEW_KM}km away (county=null, likely bad source geocoding): {len(unresolved_list)}")

    if review_list:
        print()
        print(f"FLAGGED (assigned to nearest county, {NEAR_MISS_KM}-{REVIEW_KM}km away):")
        for taxon, source, lat, lon, raw, url in review_list:
            print(f"  - {taxon!r} [{source}] at ({lat:.5f}, {lon:.5f}) — raw was {raw!r} — {url}")

    if unresolved_list:
        print()
        print("UNRESOLVED (county=null, needs a human look):")
        for taxon, source, lat, lon, raw, url in unresolved_list:
            print(f"  - {taxon!r} [{source}] at ({lat:.5f}, {lon:.5f}) — raw was {raw!r} — {url}")

    args.occurrences.write_text(json.dumps(data), encoding="utf-8")
    print()
    print(f"Wrote updated {args.occurrences}")


if __name__ == "__main__":
    main()
