from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from path_safety import resolve_under
from verify_text_ledger import (
    chapter_output_records,
    expected_chapter_outputs,
    verify as verify_text_ledger,
)


SCHEMA_VERSION = "1.0"
TARGET_LANGUAGE = "pt-BR"
REVISION_ROOT = Path("revision") / TARGET_LANGUAGE
CHANGE_KINDS = {
    "orthographic_correction",
    "punctuation",
    "transcription_correction",
}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return value


def require_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def revision_chapter_output_records(ledger: object) -> dict[str, dict]:
    if not isinstance(ledger, dict) or not isinstance(ledger.get("chapter_outputs"), list):
        return {}
    return {
        entry["id"]: entry
        for entry in ledger["chapter_outputs"]
        if isinstance(entry, dict) and require_text(entry.get("id"))
    }


def revision_changes_by_output(ledger: object) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    if not isinstance(ledger, dict) or not isinstance(ledger.get("changes"), list):
        return result
    for entry in ledger["changes"]:
        if isinstance(entry, dict) and require_text(entry.get("output_id")):
            result.setdefault(entry["output_id"], []).append(entry)
    return result


def _apply_approved_changes(source_text: str, changes: list[dict], output_id: str) -> tuple[str, list[str]]:
    errors: list[str] = []
    revised = source_text
    for change in changes:
        change_id = change.get("id")
        source_span = change.get("source_span")
        revised_span = change.get("revised_span")
        if not require_text(source_span) or not require_text(revised_span):
            continue
        occurrences = revised.count(source_span)
        if occurrences != 1:
            errors.append(
                f"revision change {change_id!r} for {output_id!r} must match exactly once "
                f"in the progressively revised chapter, found {occurrences}"
            )
            continue
        revised = revised.replace(source_span, revised_span, 1)
    return revised, errors


def verify(
    book_map: dict,
    book_map_sha256: str,
    source_ledger: dict,
    source_ledger_sha256: str,
    revision_ledger: dict,
    text_root: Path,
) -> list[str]:
    errors = verify_text_ledger(
        book_map,
        book_map_sha256,
        source_ledger,
        text_root,
        False,
        True,
    )
    if revision_ledger.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"revision ledger schema_version must be {SCHEMA_VERSION!r}")
    if revision_ledger.get("book_map_sha256") != book_map_sha256:
        errors.append("revision ledger.book_map_sha256 does not match book-map.json")
    if revision_ledger.get("text_ledger_sha256") != source_ledger_sha256:
        errors.append("revision ledger.text_ledger_sha256 does not match text-ledger.json")
    if revision_ledger.get("language") != TARGET_LANGUAGE:
        errors.append(f"revision ledger.language must be {TARGET_LANGUAGE!r}")
    if revision_ledger.get("status") != "approved":
        errors.append("revision ledger.status must be approved")
    if not require_text(revision_ledger.get("reviewed_by")):
        errors.append("revision ledger.reviewed_by must be non-empty")

    expected_outputs, expected_errors = expected_chapter_outputs(book_map, text_root)
    errors += expected_errors
    source_outputs = chapter_output_records(source_ledger)
    outputs = revision_ledger.get("chapter_outputs")
    if not isinstance(outputs, list) or not outputs:
        return errors + ["revision ledger.chapter_outputs must be a non-empty array"]

    output_records: dict[str, dict] = {}
    for index, entry in enumerate(outputs):
        label = f"revision ledger.chapter_outputs[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        output_id = entry.get("id")
        if not require_text(output_id) or output_id in output_records:
            errors.append(f"{label}.id must be unique and non-empty")
            continue
        output_records[output_id] = entry
        source_output = source_outputs.get(output_id)
        if not isinstance(source_output, dict):
            errors.append(f"{label} has no verified source output")
            continue
        for key in ("source_file", "source_sha256"):
            if entry.get(key) != source_output.get(key):
                errors.append(f"{label}.{key} does not match text-ledger.json")
        revised_file = entry.get("revised_file")
        revised_path = resolve_under(text_root, revised_file, (REVISION_ROOT / "chapters",))
        if revised_path is None:
            errors.append(
                f"{label}.revised_file must resolve under {REVISION_ROOT.as_posix()}/chapters"
            )
        elif not revised_path.is_file():
            errors.append(f"{label}.revised_file is missing: {revised_file}")
        elif entry.get("revised_sha256") != sha256_file(revised_path):
            errors.append(f"{label}.revised_sha256 does not match revised_file")
        if entry.get("source_pages") != source_output.get("source_pages"):
            errors.append(f"{label}.source_pages does not match text-ledger.json")
        if not require_text(entry.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")

    expected_ids = list(expected_outputs)
    if list(output_records) != expected_ids:
        errors.append(
            "revision ledger.chapter_outputs must exactly preserve source output order: "
            f"{expected_ids}"
        )

    changes = revision_ledger.get("changes")
    if not isinstance(changes, list) or not changes:
        errors.append("revision ledger.changes must be a non-empty array")
        changes = []
    seen_change_ids: set[str] = set()
    grouped_changes: dict[str, list[dict]] = {}
    for index, change in enumerate(changes):
        label = f"revision ledger.changes[{index}]"
        if not isinstance(change, dict):
            errors.append(f"{label} must be an object")
            continue
        change_id = change.get("id")
        output_id = change.get("output_id")
        if not require_text(change_id) or change_id in seen_change_ids:
            errors.append(f"{label}.id must be unique and non-empty")
        else:
            seen_change_ids.add(change_id)
        if output_id not in output_records:
            errors.append(f"{label}.output_id is not a revised chapter output")
            continue
        grouped_changes.setdefault(output_id, []).append(change)
        if change.get("kind") not in CHANGE_KINDS:
            errors.append(f"{label}.kind is invalid")
        source_span = change.get("source_span")
        revised_span = change.get("revised_span")
        if not require_text(source_span) or not require_text(revised_span):
            errors.append(f"{label} source_span and revised_span must be non-empty")
        elif source_span == revised_span:
            errors.append(f"{label} must actually change the source span")
        logical_pages = change.get("logical_pages")
        output_pages = {
            page.get("logical_page")
            for page in output_records[output_id].get("source_pages", [])
            if isinstance(page, dict)
        }
        if (
            not isinstance(logical_pages, list)
            or not logical_pages
            or any(not isinstance(page, int) for page in logical_pages)
            or not set(logical_pages).issubset(output_pages)
        ):
            errors.append(f"{label}.logical_pages must be covered by its source output")
        if not require_text(change.get("reason")):
            errors.append(f"{label}.reason must be non-empty")
        if not require_text(change.get("reviewed_by")):
            errors.append(f"{label}.reviewed_by must be non-empty")

    for output_id, output in output_records.items():
        source_path = resolve_under(text_root, output.get("source_file"), (Path("source") / "chapters",))
        revised_path = resolve_under(
            text_root,
            output.get("revised_file"),
            (REVISION_ROOT / "chapters",),
        )
        if source_path is None or revised_path is None or not source_path.is_file() or not revised_path.is_file():
            continue
        expected_text, change_errors = _apply_approved_changes(
            source_path.read_text(encoding="utf-8"),
            grouped_changes.get(output_id, []),
            output_id,
        )
        errors += change_errors
        if revised_path.read_text(encoding="utf-8") != expected_text:
            errors.append(
                f"revised chapter {output_id!r} differs from source beyond its approved changes"
            )

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Verify an approved same-language revised EPUB text edition."
    )
    parser.add_argument("--book-map", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--revision-ledger", required=True, type=Path)
    parser.add_argument("--text-root", required=True, type=Path)
    args = parser.parse_args()
    try:
        map_path = args.book_map.expanduser().resolve()
        ledger_path = args.ledger.expanduser().resolve()
        errors = verify(
            load_json(map_path),
            sha256_file(map_path),
            load_json(ledger_path),
            sha256_file(ledger_path),
            load_json(args.revision_ledger.expanduser().resolve()),
            args.text_root.expanduser().resolve(),
        )
    except RuntimeError as error:
        errors = [str(error)]
    if errors:
        print("INVALID revision ledger:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"VALID revision ledger: {args.revision_ledger.expanduser().resolve()}")


if __name__ == "__main__":
    main()
