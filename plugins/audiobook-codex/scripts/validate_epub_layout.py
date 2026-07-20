from __future__ import annotations

import argparse
from pathlib import Path
import sys

from book_layout import resolve_book_paths
from epub_layout import load_json, sha256_file, validate_layout
from verify_text_ledger import expected_chapter_outputs
from verify_text_ledger import verify as verify_text_ledger
from validate_book_map import load_json as load_book_map_json
from verify_fluid_edition_ledger import fluid_chapter_output_records
from verify_fluid_edition_ledger import verify as verify_fluid_edition_ledger
from verify_translation_ledger import translation_chapter_output_records
from verify_translation_ledger import verify as verify_translation_ledger


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an Audiobook Codex semantic EPUB layout.")
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--layout", type=Path)
    parser.add_argument(
        "--text-edition",
        choices=("fluid-pt-br", "original", "translated-pt-br"),
        default="original",
    )
    args = parser.parse_args()

    try:
        book_root = resolve_book_paths(args.book_root).assembly_root
        map_path = book_root / "metadata" / "book-map.json"
        ledger_path = book_root / "metadata" / "text-ledger.json"
        layout_path = (
            args.layout.expanduser().resolve()
            if args.layout
            else book_root
            / "metadata"
            / (
                "epub-layout.fluid.json"
                if args.text_edition == "fluid-pt-br"
                else (
                    "epub-layout.pt-br.json"
                    if args.text_edition == "translated-pt-br"
                    else "epub-layout.json"
                )
            )
        )
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
        fluid_ledger = None
        fluid_ledger_path = book_root / "metadata" / "fluid-edition-ledger.json"
        translation_ledger = None
        translation_ledger_path = book_root / "metadata" / "translation-ledger.json"
        if args.text_edition == "fluid-pt-br":
            fluid_style_path = book_root / "metadata" / "fluid-style.json"
            fluid_style = load_json(fluid_style_path)
            fluid_ledger = load_json(fluid_ledger_path)
            fluid_translation_ledger_path = None
            if (
                isinstance(fluid_ledger, dict)
                and fluid_ledger.get("base_edition") == "translated-pt-br"
            ):
                fluid_translation_ledger_path = translation_ledger_path
                translation_ledger = load_json(fluid_translation_ledger_path)
            errors += verify_fluid_edition_ledger(
                book_map,
                sha256_file(map_path),
                ledger,
                sha256_file(ledger_path),
                translation_ledger,
                sha256_file(fluid_translation_ledger_path)
                if fluid_translation_ledger_path is not None
                else None,
                fluid_style,
                sha256_file(fluid_style_path),
                fluid_ledger,
                book_root / "text",
            )
        elif args.text_edition == "translated-pt-br":
            translation_ledger = load_json(translation_ledger_path)
            if not isinstance(translation_ledger, dict):
                raise RuntimeError("Translation ledger must be a JSON object.")
            errors += verify_translation_ledger(
                book_map,
                sha256_file(map_path),
                ledger,
                sha256_file(ledger_path),
                translation_ledger,
                book_root / "text",
            )
        errors += validate_layout(
            layout,
            book_root,
            sha256_file(map_path),
            sha256_file(ledger_path),
            ledger,
            expected_document_ids,
            text_edition=args.text_edition,
            edition_ledger_sha256=(
                sha256_file(fluid_ledger_path)
                if args.text_edition == "fluid-pt-br"
                else sha256_file(translation_ledger_path)
                if args.text_edition == "translated-pt-br"
                else None
            ),
            edition_outputs=(
                fluid_chapter_output_records(fluid_ledger)
                if isinstance(fluid_ledger, dict)
                else translation_chapter_output_records(translation_ledger)
                if isinstance(translation_ledger, dict)
                and args.text_edition == "translated-pt-br"
                else None
            ),
        )
        if errors:
            raise RuntimeError("; ".join(errors))
    except RuntimeError as error:
        print(f"INVALID EPUB layout: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"VALID EPUB layout: {layout_path}")


if __name__ == "__main__":
    main()
