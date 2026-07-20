from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
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


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


SHA256_PATTERN = re.compile(r"^[0-9a-fA-F]{64}$")
FLUID_IMAGE_EDITIONS = {"original", "approved-restored"}


def fluid_edition_records(
    artifacts: object,
    kind: str,
) -> tuple[dict[str, dict], list[str]]:
    if not isinstance(artifacts, dict):
        return {}, []
    map_key = f"{kind}_editions"
    suffix = f".{kind}"
    editions = artifacts.get(map_key)
    if not isinstance(editions, dict):
        return {}, []

    records: dict[str, dict] = {}
    errors: list[str] = []
    seen_paths: set[str] = set()
    for edition_key, record in editions.items():
        if not isinstance(edition_key, str) or not edition_key.startswith(
            "fluid-pt-br:"
        ):
            continue
        label = f"publication-manifest {map_key}[{edition_key!r}]"
        image_edition = edition_key.split(":", 1)[1]
        if not isinstance(record, dict):
            errors.append(f"{label} must be an object")
            continue
        if image_edition not in FLUID_IMAGE_EDITIONS:
            errors.append(f"{label} image edition is invalid")
        if record.get("text_edition") != "fluid-pt-br":
            errors.append(f"{label} text_edition must be fluid-pt-br")
        if record.get("image_edition") != image_edition:
            errors.append(f"{label} image_edition does not match its edition key")
        if record.get("path_root") != "book":
            errors.append(f"{label} path_root must be book")
        raw_path = record.get("path")
        if not isinstance(raw_path, str) or not raw_path:
            errors.append(f"{label} path must be a non-empty root filename")
            continue
        path = Path(raw_path)
        if path.name != raw_path or path.suffix.casefold() != suffix:
            errors.append(f"{label} path must be a root-level {suffix} filename")
            continue
        expected_hash = record.get("sha256")
        if not isinstance(expected_hash, str) or not SHA256_PATTERN.fullmatch(
            expected_hash
        ):
            errors.append(f"{label} sha256 must contain 64 hexadecimal characters")
            continue
        if raw_path in seen_paths:
            errors.append(f"{label} reuses supplemental path {raw_path}")
            continue
        seen_paths.add(raw_path)
        records[edition_key] = record
    return records, errors


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
    fluid_records = {"epub": {}, "pdf": {}}
    publication_manifest_path = (
        paths.assembly_root / "metadata" / "publication-manifest.json"
    )
    if publication_manifest_path.is_file():
        try:
            publication_manifest = load_json(publication_manifest_path)
        except RuntimeError as error:
            errors.append(str(error))
        else:
            artifacts = (
                publication_manifest.get("artifacts")
                if isinstance(publication_manifest, dict)
                else None
            )
            for kind in ("epub", "pdf"):
                records, record_errors = fluid_edition_records(artifacts, kind)
                fluid_records[kind] = records
                errors += record_errors

    fluid_keys = {
        kind: set(records)
        for kind, records in fluid_records.items()
    }
    if fluid_keys["epub"] != fluid_keys["pdf"]:
        errors.append(
            "Published fluid EPUB/PDF edition keys must match exactly"
        )
    for edition_key in sorted(fluid_keys["epub"] & fluid_keys["pdf"]):
        epub_record = fluid_records["epub"][edition_key]
        pdf_record = fluid_records["pdf"][edition_key]
        if Path(epub_record["path"]).stem != Path(pdf_record["path"]).stem:
            errors.append(
                f"Published fluid EPUB/PDF filenames must share one stem: "
                f"{edition_key}"
            )
        for kind, record in (("epub", epub_record), ("pdf", pdf_record)):
            canonical_name = f"{paths.public_root.name}.{kind}"
            if record["path"] == canonical_name:
                errors.append(
                    f"Published fluid {kind.upper()} must not replace "
                    f"{canonical_name}"
                )
                continue
            public_file = paths.public_root / record["path"]
            if not public_file.is_file():
                errors.append(
                    f"Published fluid {kind.upper()} is missing: {record['path']}"
                )
            elif sha256_file(public_file) != record["sha256"].casefold():
                errors.append(
                    f"Published fluid {kind.upper()} hash does not match "
                    f"publication-manifest.json: {record['path']}"
                )

    for suffix in (".epub", ".pdf"):
        kind = suffix.removeprefix(".")
        entries = by_suffix[suffix]
        canonical_name = f"{paths.public_root.name}{suffix}"
        canonical = [entry for entry in entries if entry.name == canonical_name]
        supplemental = [entry for entry in entries if entry.name != canonical_name]
        if mode == "published" and len(canonical) != 1:
            errors.append(
                f"Published book root must contain canonical {canonical_name}"
            )
        recorded_paths = {
            record["path"] for record in fluid_records[kind].values()
        }
        for entry in supplemental:
            if entry.name not in recorded_paths:
                errors.append(
                    f"Published supplemental {suffix} is not a valid fluid "
                    f"publication in publication-manifest.json: {entry.name}"
                )

    mp3_entries = by_suffix[".mp3"]
    canonical_mp3 = f"{paths.public_root.name}.mp3"
    if mode == "working":
        if len(mp3_entries) > 1:
            errors.append("Book root contains more than one .mp3 file")
    elif len(mp3_entries) != 1 or mp3_entries[0].name != canonical_mp3:
        errors.append(
            f"Published book root must contain exactly one canonical {canonical_mp3}"
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
