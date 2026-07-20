from __future__ import annotations

import argparse
import sys
from pathlib import Path

from swarm_claims import load_json, validate_claim_map


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an Audiobook Codex claim map.")
    parser.add_argument("claim_map", type=Path)
    parser.add_argument(
        "--book-root",
        type=Path,
        help="Optional book/public root used to verify read_set SHA-256 values.",
    )
    args = parser.parse_args()

    try:
        errors = validate_claim_map(
            load_json(args.claim_map.expanduser().resolve()),
            args.book_root.expanduser().resolve() if args.book_root else None,
        )
    except RuntimeError as error:
        print(f"INVALID claim map: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if errors:
        print("INVALID claim map:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID claim map")


if __name__ == "__main__":
    main()
