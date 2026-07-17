from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from book_layout import (
    ASSEMBLY_DIRECTORY,
    ASSEMBLY_SUBDIRECTORIES,
    resolve_book_paths,
    validate_public_root_name,
)


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def validate_layout(book_root: Path, mode: str, allow_legacy: bool = False) -> list[str]:
    try:
        paths = resolve_book_paths(book_root, allow_legacy=allow_legacy)
    except RuntimeError as error:
        return [str(error)]

    errors: list[str] = []
    if paths.layout_kind == "legacy":
        return [] if allow_legacy else [f"Legacy book layout is not allowed: {paths.public_root}"]

    assembly_entries = {entry.name: entry for entry in paths.assembly_root.iterdir()}
    expected_assembly = set(ASSEMBLY_SUBDIRECTORIES)
    missing = sorted(expected_assembly - set(assembly_entries))
    extra = sorted(set(assembly_entries) - expected_assembly)
    if missing:
        errors.append(f"assembly is missing required directories: {missing}")
    if extra:
        errors.append(f"assembly contains unsupported top-level entries: {extra}")
    for name in expected_assembly & set(assembly_entries):
        if not assembly_entries[name].is_dir():
            errors.append(f"assembly/{name} must be a directory")

    map_path = paths.assembly_root / "metadata" / "book-map.json"
    if not map_path.is_file():
        errors.append(f"Book map is missing: {map_path}")
    else:
        try:
            book_map = load_json(map_path)
        except RuntimeError as error:
            errors.append(str(error))
        else:
            book = book_map.get("book") if isinstance(book_map, dict) else None
            errors += validate_public_root_name(paths, book)

    public_entries = {entry.name: entry for entry in paths.public_root.iterdir()}
    final_entries = [
        entry
        for name, entry in public_entries.items()
        if name != ASSEMBLY_DIRECTORY
    ]
    unsupported = [
        entry.name
        for entry in final_entries
        if not entry.is_file() or entry.suffix.casefold() not in {".epub", ".pdf", ".mp3"}
    ]
    if unsupported:
        errors.append(f"Book root contains unsupported entries: {sorted(unsupported)}")

    by_suffix = {
        suffix: [entry for entry in final_entries if entry.suffix.casefold() == suffix]
        for suffix in (".epub", ".pdf", ".mp3")
    }
    if mode == "working":
        for suffix, entries in by_suffix.items():
            if len(entries) > 1:
                errors.append(f"Book root contains more than one {suffix} file")
    else:
        for suffix, entries in by_suffix.items():
            if len(entries) != 1:
                errors.append(f"Published book root must contain exactly one {suffix} file")
            elif entries[0].stem != paths.public_root.name:
                errors.append(
                    f"Published {suffix} filename must use the canonical book folder name"
                )
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an Audiobook Codex book folder layout.")
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--mode", choices=("working", "published"), default="working")
    parser.add_argument("--allow-legacy", action="store_true")
    args = parser.parse_args()

    errors = validate_layout(args.book_root, args.mode, args.allow_legacy)
    if errors:
        print("INVALID book layout:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID book layout")


if __name__ == "__main__":
    main()
