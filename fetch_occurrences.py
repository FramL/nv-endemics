#!/usr/bin/env python3
"""
fetch_occurrences.py

Fetches herbarium occurrence records (via GBIF, which aggregates SEINet/
Symbiota/Intermountain Region Herbarium Network data among many other
providers) and iNaturalist observations for Nevada endemic and near-endemic
plant taxa.

Outputs: data/occurrences.geojson

Requirements:
    pip install requests pandas tqdm

Usage:
    python fetch_occurrences.py                  # full run
    python fetch_occurrences.py --test           # test on first 5 taxa only
    python fetch_occurrences.py --taxon "Lepidium tiehmii"  # single taxon
"""

import argparse
import csv
import json
import time
import sys
from pathlib import Path

import requests
from tqdm import tqdm

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

TAXA_CSV = Path("data/taxa.csv")
OUTPUT_GEOJSON = Path("data/occurrences.geojson")
OUTPUT_TAXA_JSON = Path("data/taxa_metadata.json")

# GBIF — Global Biodiversity Information Facility
# GBIF aggregates herbarium/specimen records from SEINet/Symbiota portals
# (including the Intermountain Region Herbarium Network) along with hundreds
# of other data providers worldwide. Its API is public, stable, well documented,
# and does not require an API key for read access — unlike scraping the
# Symbiota portal's own API directly, which returned HTTP 403 (the portal's
# robots.txt disallows automated access; GBIF is the sanctioned bulk-access route).
# Docs: https://techdocs.gbif.org/en/openapi/v1/occurrence
GBIF_BASE = "https://api.gbif.org/v1/occurrence/search"

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

# iNaturalist  — Nevada place_id = 50 (confirmed via inaturalist.org/places/nevada,
# which links to observations?place_id=50 and places/50/widget)
INAT_BASE = "https://api.inaturalist.org/v1/observations"
INAT_PLACE_ID = 50         # Nevada
INAT_PER_PAGE = 200        # max allowed per request
INAT_RATE_LIMIT = 1.0      # seconds between requests (be polite)

# GBIF rate limit (GBIF has no strict published limit for this volume, but
# being polite avoids any throttling)
GBIF_RATE_LIMIT = 0.5

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def load_taxa(csv_path: Path) -> list[dict]:
    with open(csv_path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def make_feature(lat: float, lon: float, props: dict) -> dict:
    """Wrap a point in a GeoJSON Feature."""
    return {
        "type": "Feature",
        "geometry": {"type": "Point", "coordinates": [lon, lat]},
        "properties": props
    }


# ---------------------------------------------------------------------------
# GBIF fetch (herbarium / preserved specimen records)
# ---------------------------------------------------------------------------

def fetch_gbif(taxon_name: str) -> list[dict]:
    """
    Query the GBIF occurrence API for preserved herbarium specimens of a
    given taxon in Nevada. Returns a list of GeoJSON feature dicts.

    GBIF API docs: https://techdocs.gbif.org/en/openapi/v1/occurrence
    Key params:
      scientificName    — exact scientific name to match against GBIF's backbone taxonomy
      stateProvince     — free-text state/province filter
      country           — ISO 2-letter country code (US)
      basisOfRecord     — PRESERVED_SPECIMEN restricts to herbarium vouchers
                          (excludes HUMAN_OBSERVATION, which would double up with iNat)
      hasCoordinate     — true restricts to georeferenced records only
      limit / offset    — pagination (max limit is 300 per request)
    """
    features = []
    offset = 0
    limit = 300

    while True:
        params = {
            "scientificName": taxon_name,
            "country": "US",
            "stateProvince": "Nevada",
            "basisOfRecord": "PRESERVED_SPECIMEN",
            "hasCoordinate": "true",
            "limit": limit,
            "offset": offset,
        }
        try:
            resp = requests.get(GBIF_BASE, params=params, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  [GBIF] Error for {taxon_name}: {e}", file=sys.stderr)
            break
        except json.JSONDecodeError:
            print(f"  [GBIF] Bad JSON for {taxon_name}", file=sys.stderr)
            break

        records = data.get("results", [])
        if not records:
            break

        for rec in records:
            lat = rec.get("decimalLatitude")
            lon = rec.get("decimalLongitude")
            if lat is None or lon is None:
                continue
            try:
                lat, lon = float(lat), float(lon)
            except (TypeError, ValueError):
                continue
            if lat == 0 and lon == 0:
                continue

            # Some GBIF stateProvince values are inconsistent ("Nevada", "NV", "nevada");
            # double-check here as a safety net since the API filter is approximate.
            state_val = (rec.get("stateProvince") or "").strip().lower()
            if state_val and state_val not in ("nevada", "nv"):
                continue

            institution = rec.get("institutionCode", "") or rec.get("publisher", "")
            collectors = rec.get("recordedBy", "")

            props = {
                "source": "herbarium",
                "taxon": taxon_name,
                "collection_date": rec.get("eventDate", "") or rec.get("year", ""),
                "collector": collectors,
                "institution": institution,
                "catalog_number": rec.get("catalogNumber", ""),
                "locality": rec.get("locality", "") or rec.get("verbatimLocality", ""),
                "county": rec.get("county", ""),
                "state": rec.get("stateProvince", ""),
                "elevation_m": rec.get("elevation", ""),
                "habitat": rec.get("habitat", ""),
                "url": f"https://www.gbif.org/occurrence/{rec.get('key', '')}" if rec.get("key") else "",
                "dataset_name": rec.get("datasetName", ""),
            }
            features.append(make_feature(lat, lon, props))

        total = data.get("count", 0)
        if offset + limit >= total or len(records) < limit:
            break
        offset += limit
        time.sleep(GBIF_RATE_LIMIT)

    return features


# ---------------------------------------------------------------------------
# iNaturalist fetch
# ---------------------------------------------------------------------------

def fetch_inat(taxon_name: str, research_grade_only: bool = False) -> list[dict]:
    """
    Query the iNaturalist v1 API for observations of a taxon in Nevada.
    Returns a list of GeoJSON feature dicts.

    Set research_grade_only=False to include needs_id and casual grades —
    useful for taxa with few or no RG records.
    """
    features = []
    page = 1

    quality_grade = "research" if research_grade_only else None

    while True:
        params = {
            "taxon_name": taxon_name,
            "place_id": INAT_PLACE_ID,
            "per_page": INAT_PER_PAGE,
            "page": page,
            "geo": "true",           # only geolocated records
            "order_by": "created_at",  # field to sort by
            "order": "desc",            # sort direction
        }
        if quality_grade:
            params["quality_grade"] = quality_grade

        try:
            resp = requests.get(INAT_BASE, params=params, headers=REQUEST_HEADERS, timeout=30)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            print(f"  [iNat] Error for {taxon_name}: {e}", file=sys.stderr)
            break
        except json.JSONDecodeError:
            print(f"  [iNat] Bad JSON for {taxon_name}", file=sys.stderr)
            break

        results = data.get("results", [])
        total = data.get("total_results", 0)

        for obs in results:
            loc = obs.get("location")
            if not loc:
                continue
            try:
                lat, lon = map(float, loc.split(","))
            except (ValueError, AttributeError):
                continue

            taxon_info = obs.get("taxon") or {}
            user_info = obs.get("user") or {}

            # IMPORTANT: "positional_accuracy" reflects the observer's device/GPS
            # accuracy at the moment of observation — it does NOT account for
            # iNat's automatic obscuring of sensitive/threatened taxa. A point can
            # show positional_accuracy=2 (meters) and still have been moved by
            # ~20+ km for public display. "public_positional_accuracy" is the
            # correct field for assessing how trustworthy the DISPLAYED coordinates
            # actually are — it's inflated to reflect the full obscuring radius
            # (~0.2°x0.2° cell, ~22km box) whenever obscuring applies, and falls
            # back to the same value as positional_accuracy when it doesn't.
            # Always prefer public_positional_accuracy; fall back to
            # positional_accuracy only if the public field is genuinely absent.
            pub_acc = obs.get("public_positional_accuracy")
            raw_acc = obs.get("positional_accuracy")
            display_acc = pub_acc if pub_acc is not None else raw_acc

            # "coordinates_obscured" is not a populated field on this endpoint in
            # practice (observed to return None). The actual signals are
            # "geoprivacy" (user-set) and "taxon_geoprivacy" (auto-set by iNat for
            # at-risk taxa) — either being "obscured" or "private" means the
            # displayed point is NOT the true location.
            geoprivacy = obs.get("geoprivacy")
            taxon_geoprivacy = obs.get("taxon_geoprivacy")
            is_obscured = (geoprivacy in ("obscured", "private")) or \
                          (taxon_geoprivacy in ("obscured", "private"))

            props = {
                "source": "inat",
                "taxon": taxon_name,
                "inat_taxon_name": taxon_info.get("name", taxon_name),
                "observed_on": obs.get("observed_on", ""),
                "observer": user_info.get("login", ""),
                "quality_grade": obs.get("quality_grade", ""),
                "obs_id": obs.get("id", ""),
                "url": f"https://www.inaturalist.org/observations/{obs.get('id', '')}",
                "county": "",   # iNat doesn't expose county directly; extractable from place_guess
                "place_guess": obs.get("place_guess", ""),
                "description": obs.get("description", ""),
                "num_identification_agreements": obs.get("num_identification_agreements", 0),
                "num_identification_disagreements": obs.get("num_identification_disagreements", 0),
                "positional_accuracy_m": display_acc if display_acc is not None else "",
                "raw_positional_accuracy_m": raw_acc if raw_acc is not None else "",
                "coordinates_obscured": is_obscured,
                "geoprivacy": geoprivacy or "",
                "taxon_geoprivacy": taxon_geoprivacy or "",
            }
            features.append(make_feature(lat, lon, props))

        if page * INAT_PER_PAGE >= total:
            break
        page += 1
        time.sleep(INAT_RATE_LIMIT)

    return features


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    # Version marker — bump this string whenever the script changes, so a
    # glance at the run output confirms which version actually executed.
    # Current: place_id=50 (Nevada, corrected from erroneous 7); order_by/order
    # params correctly assigned (order_by=field, order=direction).
    SCRIPT_VERSION = "2026-06-21-v5-geoprivacy-fix"
    print(f"fetch_occurrences.py version: {SCRIPT_VERSION}")
    print(f"  INAT_PLACE_ID = {INAT_PLACE_ID} (should be 50 for Nevada)")
    print()

    parser = argparse.ArgumentParser(description="Fetch Nevada endemic plant occurrences")
    parser.add_argument("--test", action="store_true", help="Run on first 5 taxa only")
    parser.add_argument("--taxon", type=str, help="Run for a single taxon name")
    parser.add_argument("--inat-rg-only", action="store_true",
                        help="Fetch only research-grade iNat observations (default: all grades)")
    parser.add_argument("--skip-gbif", action="store_true", help="Skip GBIF (herbarium) fetch")
    parser.add_argument("--skip-inat", action="store_true", help="Skip iNat fetch")
    args = parser.parse_args()

    taxa = load_taxa(TAXA_CSV)

    if args.taxon:
        taxa = [t for t in taxa if args.taxon.lower() in t["taxon"].lower()]
        if not taxa:
            print(f"No taxon matching '{args.taxon}' found in {TAXA_CSV}")
            sys.exit(1)
    elif args.test:
        taxa = taxa[:5]

    print(f"Processing {len(taxa)} taxa...")

    all_features = []
    taxa_metadata = {}  # keyed by taxon name; used by the web app

    for row in tqdm(taxa, unit="taxon"):
        name = row["taxon"].strip()
        if not name:
            continue

        # Build metadata entry for the web app
        taxa_metadata[name] = {
            "taxon": name,
            "genus": row.get("genus", ""),
            "endemism_category": row.get("endemism_category", "endemic"),
            "esa_status": row.get("esa_status", ""),
            "nv_state_status": row.get("nv_state_status", ""),
            "book_thematic_category": row.get("book_thematic_category", ""),
            "physiographic_region": row.get("physiographic_region", ""),
            "physiographic_section": row.get("physiographic_section", ""),
            "notes": row.get("notes", ""),
        }

        # GBIF (herbarium specimens)
        if not args.skip_gbif:
            gbif_features = fetch_gbif(name)
            # Merge metadata into each feature
            for f in gbif_features:
                f["properties"].update({
                    "genus": row.get("genus", ""),
                    "endemism_category": row.get("endemism_category", "endemic"),
                    "esa_status": row.get("esa_status", ""),
                    "nv_state_status": row.get("nv_state_status", ""),
                    "book_thematic_category": row.get("book_thematic_category", ""),
                    "physiographic_region": row.get("physiographic_region", ""),
                    "physiographic_section": row.get("physiographic_section", ""),
                })
            all_features.extend(gbif_features)
            if gbif_features:
                tqdm.write(f"  {name}: {len(gbif_features)} herbarium records")
            time.sleep(GBIF_RATE_LIMIT)

        # iNaturalist
        if not args.skip_inat:
            inat_features = fetch_inat(name, research_grade_only=args.inat_rg_only)
            for f in inat_features:
                f["properties"].update({
                    "genus": row.get("genus", ""),
                    "endemism_category": row.get("endemism_category", "endemic"),
                    "esa_status": row.get("esa_status", ""),
                    "nv_state_status": row.get("nv_state_status", ""),
                    "book_thematic_category": row.get("book_thematic_category", ""),
                    "physiographic_region": row.get("physiographic_region", ""),
                    "physiographic_section": row.get("physiographic_section", ""),
                })
            all_features.extend(inat_features)
            if inat_features:
                tqdm.write(f"  {name}: {len(inat_features)} iNat observations")
            time.sleep(INAT_RATE_LIMIT)

    # Write GeoJSON
    geojson = {
        "type": "FeatureCollection",
        "features": all_features
    }
    OUTPUT_GEOJSON.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_GEOJSON, "w", encoding="utf-8") as f:
        json.dump(geojson, f, ensure_ascii=False)
    print(f"\nWrote {len(all_features)} features to {OUTPUT_GEOJSON}")

    # Write taxa metadata JSON (consumed by web app)
    with open(OUTPUT_TAXA_JSON, "w", encoding="utf-8") as f:
        json.dump(taxa_metadata, f, ensure_ascii=False, indent=2)
    print(f"Wrote taxa metadata to {OUTPUT_TAXA_JSON}")


if __name__ == "__main__":
    main()
