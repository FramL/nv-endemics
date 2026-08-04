#!/usr/bin/env python3
"""
refresh_geo_pipeline.py

Runs the three geographic-tagging scripts in the correct order:

    add_county.py  ->  add_regions.py  ->  add_place_category.py

Each one computes a different field on data/occurrences.geojson via
point-in-polygon (county, physiographic_region/section, named_place
respectively). They're independent of each other and safe to run in any
order individually, but running all three together after a fresh fetch is
the normal case, so this exists to make that one command instead of three.

Deliberately NOT included here: fetch_occurrences.py. That's a separate,
manual step on purpose -- taxa sometimes fail to come in on a given run and
need a follow-up fetch before the data is ready. Run that yourself, confirm
you're happy with the result, THEN run this script against the
occurrences.geojson it produced.

Usage:
    python fetch_occurrences.py          <- run this yourself first,
                                             follow up on any taxa that
                                             failed to come in, confirm
                                             you're happy with the result
    python refresh_geo_pipeline.py       <- then run this

If any step fails, this stops immediately rather than continuing with a
partially-tagged file -- you'll see exactly which script failed and why.
"""

import subprocess
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).parent

PIPELINE = [
    "add_county.py",
    "add_regions.py",
    "add_place_category.py",
]


def main():
    print("Running geographic tagging pipeline:")
    print("  " + "  ->  ".join(PIPELINE))
    print()

    for i, script in enumerate(PIPELINE, start=1):
        script_path = SCRIPT_DIR / script
        print(f"[{i}/{len(PIPELINE)}] Running {script} ...")
        print("-" * 60)
        result = subprocess.run([sys.executable, str(script_path)])
        print("-" * 60)
        if result.returncode != 0:
            print()
            print(f"STOPPED: {script} exited with an error (code {result.returncode}).")
            print("Fix the problem above and re-run this script -- earlier steps in")
            print("the sequence already completed and don't need to be redone unless")
            print("you suspect their output was affected.")
            sys.exit(result.returncode)
        print()

    print("All three steps completed. occurrences.geojson now has current")
    print("county, physiographic_region/section, and named_place fields.")
    print()
    print("Next: open index.html locally and spot-check a few filters before")
    print("committing -- the pipeline can run cleanly and still produce data")
    print("that's worth a quick look (e.g. if boundary files themselves need")
    print("updating, not just occurrences.geojson).")


if __name__ == "__main__":
    main()
