#!/usr/bin/env python3
"""
add_place_category.py

Computes an exact, per-point `named_place` field for every point in
data/occurrences.geojson, using point-in-polygon against real, official
boundaries for named localities mentioned in the book's thematic categories
(data/nevada_named_places.geojson).

Why this exists: `book_thematic_category` (from taxa_metadata.json) turned
out to be two different kinds of things sharing one field. Most values
(Disjunct, Cliff-dwelling, Foothills, One Mountain Range, etc.) describe
habitat or distribution *pattern*, not a location -- there's no polygon for
"cliff-dwelling habitat" statewide, so those were never a spatial claim and
this script leaves them alone. Only a couple of values are actual named
places with a real, checkable boundary: currently just "Ash Meadows"
(Spring Mountains pending -- add its boundary to
nevada_named_places.geojson and re-run, no code changes needed).

This does NOT touch book_thematic_category. It adds a new, independent
field, `named_place`, computed straight from each point's coordinates --
same relationship add_county.py's `county` field has to the raw source
locality string.

Same three-tier fallback as add_county.py / add_regions.py:
- "exact": point falls inside a named place's boundary.
- "near_miss": outside, but within NEAR_MISS_KM -- assigned to nearest.
- "flagged": 15-100km away -- assigned to nearest, but worth a look.
- "unresolved" / not applicable: >100km from every tracked place, or no
  tracked place nearby at all -- named_place stays null. This is the
  expected, normal case for the vast majority of points, since only a
  couple of small named places are tracked at all (unlike county/region,
  which cover the whole state) -- null here means "not in a tracked place,"
  not "error."

Usage:
    python add_place_category.py
"""

import argparse
import json
from pathlib import Path

from shapely.geometry import shape, Point

DATA_DIR = Path(__file__).parent / "data"
OCCURRENCES = DATA_DIR / "occurrences.geojson"
PLACES = DATA_DIR / "nevada_named_places.geojson"

NEAR_MISS_KM = 1   # small tolerance for minor boundary-precision issues only
                   # (e.g. official refuge line vs. actual population extending
                   # slightly past it). NOT a "assign to nearest anyway"
                   # fallback like county/region use -- unlike those, most
                   # points correctly do NOT belong to any tracked place at
                   # all, so there's no reason to force a loose match.
REVIEW_LOG_KM = 10  # points within this range get logged as "close, but not
                    # assigned" for a human glance -- never written to
                    # named_place. Purely informational.


def load_places(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    return [(f["properties"]["place"], shape(f["geometry"])) for f in data["features"]]


def find_place(lon: float, lat: float, places):
    """
    Returns (assigned_name, status, log_name) where:
      assigned_name -- what to write to named_place. None unless the point
                        is genuinely inside a place or within NEAR_MISS_KM
                        of one.
      status         -- "exact" | "near_miss" | "close_not_assigned" | "far"
      log_name        -- nearest place, for logging purposes even when not
                        assigned (so close-but-excluded points are still
                        visible in the console output for a human glance).
    """
    pt = Point(lon, lat)
    for name, poly in places:
        if poly.contains(pt) or poly.touches(pt):
            return name, "exact", name

    name, nearest_poly = min(places, key=lambda p: p[1].distance(pt))
    dist_km = nearest_poly.distance(pt) * 111
    if dist_km <= NEAR_MISS_KM:
        return name, "near_miss", name
    elif dist_km <= REVIEW_LOG_KM:
        return None, "close_not_assigned", name
    else:
        return None, "far", None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--places", type=Path, default=PLACES)
    parser.add_argument("--occurrences", type=Path, default=OCCURRENCES)
    args = parser.parse_args()

    print(f"Loading named place boundaries from {args.places} ...")
    places = load_places(args.places)
    print(f"  {len(places)} place(s) loaded: {', '.join(n for n, _ in places)}")

    print(f"Loading occurrences from {args.occurrences} ...")
    data = json.loads(args.occurrences.read_text(encoding="utf-8"))
    feats = data["features"]
    print(f"  {len(feats)} points loaded")

    n_exact = 0
    n_near_miss = 0
    close_not_assigned_list = []

    for f in feats:
        props = f["properties"]
        lon, lat = f["geometry"]["coordinates"][:2]
        assigned, status, nearest = find_place(lon, lat, places)

        props["named_place"] = assigned
        props["named_place_match"] = status if assigned else None

        if status == "exact":
            n_exact += 1
        elif status == "near_miss":
            n_near_miss += 1
        elif status == "close_not_assigned":
            close_not_assigned_list.append((props.get("taxon"), props.get("book_thematic_category"), lat, lon, nearest))

    print()
    print(f"Exact point-in-polygon match: {n_exact} / {len(feats)}")
    print(f"Near-miss, assigned via <{NEAR_MISS_KM}km fallback: {n_near_miss}")
    print(f"Close but NOT assigned ({NEAR_MISS_KM}-{REVIEW_LOG_KM}km away -- logged only, named_place stays null): {len(close_not_assigned_list)}")
    print(f"Everything else: named_place=null (not near any tracked place -- expected/normal)")

    # Write BEFORE the verbose per-record lists -- see add_county.py for why.
    args.occurrences.write_text(json.dumps(data), encoding="utf-8")
    print(f"\nWrote updated {args.occurrences}")

    if close_not_assigned_list:
        print()
        print(f"CLOSE BUT NOT ASSIGNED (showing first 15 of {len(close_not_assigned_list)}):")
        for taxon, old_cat, lat, lon, nearest in close_not_assigned_list[:15]:
            print(f"  - {taxon!r} [old book_thematic_category: {old_cat!r}] at ({lat:.5f}, {lon:.5f}) -- near {nearest!r} but outside NEAR_MISS_KM, left null")
        if len(close_not_assigned_list) > 15:
            print(f"  ... and {len(close_not_assigned_list) - 15} more")

    # Sanity check per tracked place: of the points whose OLD taxon-level
    # book_thematic_category named this place, how many actually land in
    # the real boundary? Add a (short_book_tag, full_place_name) pair here
    # any time a new place is added to nevada_named_places.geojson and its
    # short form also happens to appear as a book_thematic_category value.
    book_tag_to_place = {
        "Ash Meadows": "Ash Meadows National Wildlife Refuge",
        "Spring Mountains": "Spring Mountains National Recreation Area",
    }
    for book_tag, place_name in book_tag_to_place.items():
        old_tagged = [f for f in feats if f["properties"].get("book_thematic_category") == book_tag]
        if not old_tagged:
            continue
        confirmed = sum(1 for f in old_tagged if f["properties"].get("named_place") == place_name)
        print()
        print(f"Of {len(old_tagged)} points whose taxon was old-tagged {book_tag!r}: "
              f"{confirmed} actually fall in/near the real {place_name}, "
              f"{len(old_tagged) - confirmed} do not (the outlier problem this replaces).")

if __name__ == "__main__":
    main()
