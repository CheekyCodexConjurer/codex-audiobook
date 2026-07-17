from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import os
from pathlib import Path
import sys

from book_layout import (
    ASSEMBLY_SUBDIRECTORIES,
    assert_no_reparse_ancestors,
    book_identity,
    canonical_book_folder_name,
    is_reparse_point,
    lexical_absolute,
    path_lexists,
)


CONFIRMATION = "MOVE_LIBRARY_LAYOUT"
HISTORICAL_CALIBRATION_PREFIX = "_voice-calibration-"
OPTIONAL_LEGACY_DIRECTORIES = ("restoration",)
FINAL_SUFFIXES = {".epub", ".pdf", ".mp3"}
PUBLICATION_RECORD_KEYS = {
    "path",
    "sha256",
    "source_path",
    "source_sha256",
    "published_at",
}


@dataclass(frozen=True)
class JsonUpdate:
    relative_path: Path
    original_bytes: bytes
    updated_bytes: bytes


@dataclass(frozen=True)
class Migration:
    source: Path
    target: Path
    final_renames: tuple[tuple[str, str], ...]
    json_updates: tuple[JsonUpdate, ...]


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def normalize_migrated_paths(
    value: object,
    final_renames: dict[str, str],
) -> bool:
    changed = False
    if isinstance(value, dict):
        for key, child in list(value.items()):
            if key == "path" and isinstance(child, str):
                canonical_name = final_renames.get(child.casefold())
                if canonical_name is not None and child != canonical_name:
                    value[key] = canonical_name
                    child = canonical_name
                    changed = True
                normalized_child = child.replace("\\", "/")
                if normalized_child.startswith("restoration/"):
                    value[key] = f"assets/{normalized_child}"
                    changed = True
            changed = normalize_migrated_paths(value.get(key), final_renames) or changed
        if PUBLICATION_RECORD_KEYS.issubset(value):
            if value.get("path_root") != "book":
                value["path_root"] = "book"
                changed = True
            if value.get("source_path_root") != "assembly":
                value["source_path_root"] = "assembly"
                changed = True
    elif isinstance(value, list):
        for child in value:
            changed = normalize_migrated_paths(child, final_renames) or changed
    return changed


def first_reparse_descendant(root: Path) -> Path | None:
    for current, directories, files in os.walk(root, topdown=True, followlinks=False):
        current_path = Path(current)
        for name in (*directories, *files):
            candidate = current_path / name
            if is_reparse_point(candidate):
                return candidate
    return None


def plan_json_updates(
    source: Path,
    final_renames: tuple[tuple[str, str], ...],
) -> tuple[JsonUpdate, ...]:
    candidates = [
        source / "metadata" / "assets-manifest.json",
        source / "metadata" / "audio-manifest.json",
        source / "metadata" / "publication-manifest.json",
        *(source / "exports" / "epub").rglob("*.epub.json"),
        *(source / "exports" / "pdf").rglob("*.pdf.json"),
    ]
    rename_map = {
        original.casefold(): canonical
        for original, canonical in final_renames
    }
    updates: list[JsonUpdate] = []
    for path in sorted(set(candidates), key=lambda candidate: str(candidate).casefold()):
        if not path.is_file():
            continue
        original_bytes = path.read_bytes()
        try:
            value = json.loads(original_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError(f"Cannot read JSON {path}: {error}") from error
        changed = normalize_migrated_paths(value, rename_map)
        if (
            changed
            and path == source / "metadata" / "publication-manifest.json"
            and isinstance(value, dict)
            and value.get("schema_version") != "1.1"
        ):
            value["schema_version"] = "1.1"
        updated_bytes = json_bytes(value)
        if changed and updated_bytes != original_bytes:
            updates.append(
                JsonUpdate(path.relative_to(source), original_bytes, updated_bytes)
            )
    return tuple(updates)


def classify_book(source: Path, target_library: Path) -> Migration:
    if is_reparse_point(source):
        raise RuntimeError("book directory must not be a reparse point")
    source = source.resolve()
    reparse_descendant = first_reparse_descendant(source)
    if reparse_descendant is not None:
        raise RuntimeError(
            f"book contents must not contain reparse points: "
            f"{reparse_descendant.relative_to(source)}"
        )
    map_path = source / "metadata" / "book-map.json"
    if not map_path.is_file():
        raise RuntimeError("missing metadata/book-map.json")
    book_map = load_json(map_path)
    if not isinstance(book_map, dict):
        raise RuntimeError("book map must be an object")
    title, year, author = book_identity(book_map.get("book"))
    target_library = target_library.resolve()
    target = target_library / canonical_book_folder_name(title, year, author)
    if path_lexists(target):
        raise RuntimeError(f"target already exists: {target}")
    if target.parent.resolve() != target_library:
        raise RuntimeError(f"target escapes target library: {target}")
    if source.drive.casefold() != target_library.drive.casefold():
        raise RuntimeError("migration requires source and target on the same volume")

    entries = {entry.name: entry for entry in source.iterdir()}
    missing = sorted(set(ASSEMBLY_SUBDIRECTORIES) - set(entries))
    if missing:
        raise RuntimeError(f"missing assembly directories: {missing}")
    for name in ASSEMBLY_SUBDIRECTORIES:
        if not entries[name].is_dir():
            raise RuntimeError(f"{name} must be a directory")
        if is_reparse_point(entries[name]):
            raise RuntimeError(f"{name} must not be a reparse point")
    legacy_restoration = entries.get("restoration")
    if legacy_restoration is not None:
        if not legacy_restoration.is_dir():
            raise RuntimeError("restoration must be a directory")
        if is_reparse_point(legacy_restoration):
            raise RuntimeError("restoration must not be a reparse point")
        if path_lexists(entries["assets"] / "restoration"):
            raise RuntimeError(
                "legacy restoration conflicts with assets/restoration"
            )

    allowed_names = set(ASSEMBLY_SUBDIRECTORIES) | set(OPTIONAL_LEGACY_DIRECTORIES)
    final_files: list[str] = []
    unsupported: list[str] = []
    for name, entry in entries.items():
        if name in allowed_names:
            continue
        if entry.is_file() and entry.suffix.casefold() in FINAL_SUFFIXES:
            final_files.append(name)
        else:
            unsupported.append(name)
    if unsupported:
        raise RuntimeError(f"unsupported public entries: {sorted(unsupported)}")
    suffix_counts = {
        suffix: sum(Path(name).suffix.casefold() == suffix for name in final_files)
        for suffix in FINAL_SUFFIXES
    }
    duplicates = sorted(suffix for suffix, count in suffix_counts.items() if count > 1)
    if duplicates:
        raise RuntimeError(f"multiple final artifacts for suffixes: {duplicates}")
    final_renames = tuple(
        sorted(
            (
                name,
                f"{target.name}{Path(name).suffix.casefold()}",
            )
            for name in final_files
        )
    )
    return Migration(
        source,
        target,
        final_renames,
        plan_json_updates(source, final_renames),
    )


def inventory(source_library: Path, target_library: Path) -> tuple[list[Migration], list[str]]:
    migrations: list[Migration] = []
    skipped: list[str] = []
    source_library = assert_no_reparse_ancestors(
        source_library, "Source library"
    ).resolve()
    target_library = assert_no_reparse_ancestors(
        target_library, "Target library"
    ).resolve()
    if not source_library.is_dir():
        raise RuntimeError(f"Source library does not exist: {source_library}")
    if (
        target_library == source_library
        or target_library.is_relative_to(source_library)
        or source_library.is_relative_to(target_library)
    ):
        raise RuntimeError(
            f"Source and target libraries must not overlap: "
            f"{source_library} <-> {target_library}"
        )
    for entry in sorted(source_library.iterdir(), key=lambda path: path.name.casefold()):
        if not entry.is_dir():
            skipped.append(f"{entry.name}: not a directory")
            continue
        if is_reparse_point(entry) or entry.resolve().parent != source_library:
            skipped.append(f"{entry.name}: directory must be a direct non-reparse child")
            continue
        if entry.name.startswith(HISTORICAL_CALIBRATION_PREFIX):
            skipped.append(f"{entry.name}: historical calibration evidence")
            continue
        try:
            migrations.append(classify_book(entry, target_library))
        except RuntimeError as error:
            skipped.append(f"{entry.name}: {error}")
    migrations_by_target: dict[str, list[Migration]] = {}
    for migration in migrations:
        migrations_by_target.setdefault(str(migration.target).casefold(), []).append(
            migration
        )
    collisions = [
        group
        for group in migrations_by_target.values()
        if len(group) > 1
    ]
    if collisions:
        details = "; ".join(
            f"{group[0].target} <= {', '.join(str(item.source) for item in group)}"
            for group in collisions
        )
        raise RuntimeError(f"duplicate canonical migration target: {details}")
    return migrations, skipped


def replace_bytes(path: Path, value: bytes) -> None:
    staged = path.with_name(f".{path.name}.{os.getpid()}.migration")
    if staged.exists():
        raise RuntimeError(f"Migration temporary file already exists: {staged}")
    try:
        staged.write_bytes(value)
        os.replace(staged, path)
    finally:
        if staged.exists():
            staged.unlink()


def rename_file_exact(source: Path, destination: Path) -> None:
    if source.name == destination.name:
        return
    if source.name.casefold() != destination.name.casefold():
        if path_lexists(destination):
            raise RuntimeError(f"Migration destination already exists: {destination}")
        os.replace(source, destination)
        return

    staged = source.with_name(f".{source.name}.{os.getpid()}.case-rename")
    if path_lexists(staged):
        raise RuntimeError(f"Case-rename temporary file already exists: {staged}")
    os.replace(source, staged)
    try:
        os.replace(staged, destination)
    except Exception:
        if path_lexists(staged) and not path_lexists(source):
            os.replace(staged, source)
        raise


def execute_migration(migration: Migration) -> None:
    source = migration.source
    target = migration.target
    assembly = source / "assembly"
    if assembly.exists():
        raise RuntimeError(f"Assembly staging path already exists: {assembly}")
    target_parent = target.parent
    assert_no_reparse_ancestors(target_parent, "Target library")
    target_parent.mkdir(parents=True, exist_ok=True)
    assert_no_reparse_ancestors(target_parent, "Target library")
    if path_lexists(target):
        raise RuntimeError(f"Migration target already exists: {target}")

    moved_into_assembly: list[tuple[Path, Path]] = []
    moved_restoration: tuple[Path, Path] | None = None
    renamed_final_files: list[tuple[Path, Path]] = []
    updated_json: list[JsonUpdate] = []
    public_moved = False
    try:
        for update in migration.json_updates:
            path = source / update.relative_path
            if path.read_bytes() != update.original_bytes:
                raise RuntimeError(
                    f"Migration input changed after inventory: {path}"
                )
            replace_bytes(path, update.updated_bytes)
            updated_json.append(update)
        for original_name, canonical_name in migration.final_renames:
            original = source / original_name
            destination = source / canonical_name
            if original_name == canonical_name:
                continue
            rename_file_exact(original, destination)
            renamed_final_files.append((destination, original))
        assembly.mkdir()
        for name in ASSEMBLY_SUBDIRECTORIES:
            original = source / name
            destination = assembly / name
            os.replace(original, destination)
            moved_into_assembly.append((destination, original))
        legacy_restoration = source / "restoration"
        if legacy_restoration.exists():
            restoration_target = assembly / "assets" / "restoration"
            if path_lexists(restoration_target):
                raise RuntimeError(
                    f"Restoration target already exists: {restoration_target}"
                )
            os.replace(legacy_restoration, restoration_target)
            moved_restoration = (restoration_target, legacy_restoration)
        os.replace(source, target)
        public_moved = True
    except Exception:
        if public_moved and target.exists() and not source.exists():
            os.replace(target, source)
        if moved_restoration is not None:
            current, original = moved_restoration
            if current.exists() and not original.exists():
                os.replace(current, original)
        for current, original in reversed(moved_into_assembly):
            if current.exists() and not original.exists():
                os.replace(current, original)
        for current, original in reversed(renamed_final_files):
            if path_lexists(current):
                rename_file_exact(current, original)
        for update in reversed(updated_json):
            path = source / update.relative_path
            if path.exists():
                replace_bytes(path, update.original_bytes)
        if assembly.exists() and not any(assembly.iterdir()):
            assembly.rmdir()
        raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Inventory or migrate canonical Audiobook Codex books into Library/assembly."
    )
    parser.add_argument("--source-library", required=True, type=Path)
    parser.add_argument("--target-library", required=True, type=Path)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Required with --execute; must equal {CONFIRMATION}.",
    )
    args = parser.parse_args()

    try:
        source_library = lexical_absolute(args.source_library)
        target_library = lexical_absolute(args.target_library)
        assert_no_reparse_ancestors(source_library, "Source library")
        assert_no_reparse_ancestors(target_library, "Target library")
        target_library = target_library.resolve()
        migrations, skipped = inventory(source_library, target_library)
        for migration in migrations:
            print(f"READY {migration.source} -> {migration.target}")
            if migration.final_renames:
                renames = ", ".join(
                    source if source == target else f"{source} -> {target}"
                    for source, target in migration.final_renames
                )
                print(f"  final files: {renames}")
        for message in skipped:
            print(f"SKIP {message}")
        if not args.execute:
            print(f"DRY RUN: {len(migrations)} book(s) ready; no files moved.")
            return
        if args.confirm != CONFIRMATION:
            raise RuntimeError(
                f"--execute requires --confirm {CONFIRMATION}"
            )
        for migration in migrations:
            execute_migration(migration)
            print(f"MOVED {migration.target}")
    except (OSError, RuntimeError) as error:
        print(f"Cannot migrate library layout: {error}", file=sys.stderr)
        raise SystemExit(1) from error


if __name__ == "__main__":
    main()
