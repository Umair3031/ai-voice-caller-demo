"""
Exports the OpenAPI document without starting a server.

The frontend generates its typed API client from this file, so it is the
contract between the two halves of the stack. Run it in CI after any router or
schema change and fail the build if the committed copy drifts:

    python scripts/export_openapi.py --out openapi.json --check
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Run from anywhere: sys.path[0] is this script's directory, not the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", default="openapi.json", help="Destination path.")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the file on disk differs, instead of rewriting it.",
    )
    args = parser.parse_args()

    # Building the app only constructs routes and schemas — no provider client
    # and no network call — but Settings still validates, so give it a value.
    os.environ.setdefault("SARVAM_API_KEY", "openapi-export-placeholder")

    from app.main import create_app

    document = json.dumps(create_app().openapi(), indent=2, sort_keys=True) + "\n"
    destination = Path(args.out)

    if args.check:
        current = destination.read_text(encoding="utf-8") if destination.exists() else ""
        if current != document:
            print(
                f"{destination} is out of date. Run: python scripts/export_openapi.py",
                file=sys.stderr,
            )
            return 1
        print(f"{destination} is up to date.")
        return 0

    destination.write_text(document, encoding="utf-8")
    print(f"Wrote {destination} ({len(document):,} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
