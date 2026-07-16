from __future__ import annotations

import argparse
from pathlib import Path
import sys

from epub_layout import load_json, sha256_file, validate_layout
from verify_text_ledger import expected_chapter_outputs
from verify_text_ledger import verify as verify_text_ledger
from validate_book_map import load_json as load_book_map_json


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an Audiobook Codex semantic EPUB layout.")
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--layout", type=Path)
    args = parser.parse_args()

    try:
        book_root = args.book_root.expanduser().resolve()
        map_path = book_root / "metadata" / "book-map.json"
        ledger_path = book_root / "metadata" / "text-ledger.json"
        layout_path = args.layout.expanduser().resolve() if args.layout else book_root / "metadata" / "epub-layout.json"
        book_map = load_book_map_json(map_path)
        ledger = load_json(ledger_path)
        layout = load_json(layout_path)
        if not isinstance(book_map, dict) or not isinstance(ledger, dict):
            raise RuntimeError("Book map and text ledger must be JSON objects.")
        errors = verify_text_ledger(
            book_map,
            sha256_file(map_path),
            ledger,
            book_root / "text",
            False,
            True,
        )
        expected_outputs, expected_errors = expected_chapter_outputs(book_map, book_root / "text")
        errors += expected_errors
        expected_document_ids = list(expected_outputs)
        errors += validate_layout(
            layout,
            book_root,
            sha256_file(map_path),
            sha256_file(ledger_path),
            ledger,
            expected_document_ids,
        )
        if errors:
            raise RuntimeError("; ".join(errors))
    except RuntimeError as error:
        print(f"INVALID EPUB layout: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"VALID EPUB layout: {layout_path}")


if __name__ == "__main__":
    main()
