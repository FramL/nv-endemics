#!/usr/bin/env python3
"""
add_regions.py

Computes exact, per-point `physiographic_region` and `physiographic_section`
fields for every point in data/occurrences.geojson, using real point-in-polygon
tests against hand-digitized region/section boundaries
(data/nevada_regions_sections.geojson — traced from Tiehm & Nachlinger's
regions map, Fig. 1, via Map Warper + QGIS).

Why this exists: physiographic_region and physiographic_section were
previously taxon-level tags in taxa_metadata.json, copied onto every
occurrence of that taxon regardless of where the individual point actually
sits. Confirmed empirically before this fix: 0 of 194 taxa had more than one
distinct region/section value across all their points, i.e. every record of
a given taxon inherited one blanket tag from a book-intro text association,
not from its own coordinates. This script replaces that with the same
point-in-polygon pattern used for county (see add_county.py).

Sections that exist as multiple disjoint polygons (Bonneville Section: 4
islands; Sierra Nevada Section: 2 pieces) are handled correctly -- a point
matching ANY polygon carrying that section name counts as a match.

Behavior, same three-tier fallback as add_county.py:
- "exact": point falls inside a traced section polygon.
- "near_miss": outside every polygon, but within NEAR_MISS_KM of one --
  assigned to the nearest section. Expected here even more than for
  counties, since the traced boundaries don't perfectly tile 100% of the
  state (about 0.33% of Nevada's area isn't covered by any traced polygon,
  a mix of digitizing-precision limits and the underlying map's own ~6km
  georeferencing RMS -- not missing data).
- "flagged": 15-100km away -- assigned to nearest, but worth a look.
- "unresolved": >100km away -- not assigned; almost certainly the same
  handful of genuine source-data geocoding errors already identified by
  add_county.py (points in another state, or literally in the ocean).

Unlike add_county.py, this does NOT overwrite physiographic_region/section
with null county_raw-style provenance -- the taxon-level values are
preserved as physiographic_region_raw / physiographic_section_raw so the
old book-intro-derived tags remain auditable.

Usage:
    python add_regions.py
"""

import argparse
import json
from pathlib import Path

from shapely.geometry import shape, Point

DATA_DIR = Path(__file__).parent / "data"
OCCURRENCES = DATA_DIR / "occurrences.geojson"
REGIONS = DATA_DIR / "nevada_regions_sections.geojson"

NEAR_MISS_KM = 15
REVIEW_KM = 100


def load_sections(path: Path):
    """Return list of (region, section, shapely_polygon) -- one entry per
    traced polygon, so multi-part sections (Bonneville, Sierra Nevada)
    naturally contribute multiple entries with the same region/section."""
    data = json.loads(path.read_text(encoding="utf-8"))
    out = []
    for feat in data["features"]:
        props = feat["properties"]
        out.append((props["region"], props["section"], shape(feat["geometry"])))
    return out


def find_section(lon: float, lat: float, sections):
    pt = Point(lon, lat)
    for region, section, poly in sections:
        if poly.contains(pt) or poly.touches(pt):
            return region, section, "exact"

    region, section, nearest_poly = min(
        sections, key=lambda s: s[2].distance(pt)
    )
    dist_km = nearest_poly.distance(pt) * 111
    if dist_km <= NEAR_MISS_KM:
        return region, section, "near_miss"
    elif dist_km <= REVIEW_KM:
        return region, section, "flagged"
    else:
        return None, None, "unresolved"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--regions", type=Path, default=REGIONS)
    parser.add_argument("--occurrences", type=Path, default=OCCURRENCES)
    args = parser.parse_args()

    print(f"Loading region/section boundaries from {args.regions} ...")
    sections = load_sections(args.regions)
    print(f"  {len(sections)} traced polygon(s) loaded")

    print(f"Loading occurrences from {args.occurrences} ...")
    data = json.loads(args.occurrences.read_text(encoding="utf-8"))
    feats = data["features"]
    print(f"  {len(feats)} points loaded")

    n_exact = 0
    n_near_miss = 0
    n_changed_from_taxon_tag = 0
    review_list = []
    unresolved_list = []

    for f in feats:
        props = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        raw_region = props.get("physiographic_region")
        raw_section = props.get("physiographic_section")
        region, section, status = find_section(lon, lat, sections)

        props["physiographic_region_raw"] = raw_region
        props["physiographic_section_raw"] = raw_section
        props["physiographic_region"] = region
        props["physiographic_section"] = section
        props["physiographic_match"] = status

        if status in ("exact", "near_miss", "flagged"):
            if status == "exact":
                n_exact += 1
            elif status == "near_miss":
                n_near_miss += 1
            if section != raw_section:
                n_changed_from_taxon_tag += 1
            if status == "flagged":
                review_list.append((props.get("taxon"), props.get("source"), lat, lon, raw_section, section))
        else:
            unresolved_list.append((props.get("taxon"), props.get("source"), lat, lon, raw_section))

    print()
    print(f"Exact point-in-polygon match: {n_exact} / {len(feats)}")
    print(f"Near-miss, assigned via <{NEAR_MISS_KM}km fallback: {n_near_miss}")
    print(f"Differs from the old taxon-level tag: {n_changed_from_taxon_tag}")
    print(f"Flagged, {NEAR_MISS_KM}-{REVIEW_KM}km away (assigned to nearest, worth a look): {len(review_list)}")
    print(f"Unresolved, >{REVIEW_KM}km away (region/section=null): {len(unresolved_list)}")

    # Write BEFORE the verbose per-record lists -- see add_county.py for why.
    args.occurrences.write_text(json.dumps(data), encoding="utf-8")
    print(f"\nWrote updated {args.occurrences}")

    if review_list:
        print()
        print(f"FLAGGED (assigned to nearest section, {NEAR_MISS_KM}-{REVIEW_KM}km away):")
        for taxon, source, lat, lon, old_sec, new_sec in review_list:
            print(f"  - {taxon!r} [{source}] at ({lat:.5f}, {lon:.5f}) — old tag {old_sec!r} -> {new_sec!r}")

    if unresolved_list:
        print()
        print("UNRESOLVED (region/section=null):")
        for taxon, source, lat, lon, old_sec in unresolved_list:
            print(f"  - {taxon!r} [{source}] at ({lat:.5f}, {lon:.5f}) — old tag was {old_sec!r}")

if __name__ == "__main__":
    main()
