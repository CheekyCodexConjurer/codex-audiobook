from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys


LEDGER_STATES = {"verified", "blank", "excluded"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def resolve_under(root: Path, raw_path: object) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    target = (root / raw_path).resolve()
    try:
        target.relative_to(root.resolve())
    except ValueError:
        return None
    return target


def page_requires_text(page: dict) -> bool:
    if page.get("blank") is True:
        return False
    return page.get("kind") not in {"ignored", "excluded"}


def verify(
    book_map: object,
    book_map_sha256: str,
    ledger: object,
    text_root: Path,
    require_locutor: bool,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(book_map, dict) or not isinstance(book_map.get("pages"), list):
        return ["book map must include pages"]
    if not isinstance(ledger, dict) or not isinstance(ledger.get("pages"), list):
        return ["ledger must include pages"]
    if ledger.get("book_map_sha256") != book_map_sha256:
        return ["ledger.book_map_sha256 does not match the current book-map.json"]

    source_pages = [page for page in book_map["pages"] if isinstance(page, dict)]
    ledger_by_page: dict[int, dict] = {}
    for index, entry in enumerate(ledger["pages"]):
        label = f"ledger.pages[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        logical_page = entry.get("logical_page")
        if not isinstance(logical_page, int) or isinstance(logical_page, bool) or logical_page <= 0:
            errors.append(f"{label}.logical_page must be positive")
            continue
        if logical_page in ledger_by_page:
            errors.append(f"{label}.logical_page is duplicated: {logical_page}")
            continue
        ledger_by_page[logical_page] = entry

    mapped_numbers = {page.get("logical_page") for page in source_pages}
    extra_numbers = sorted(set(ledger_by_page) - mapped_numbers)
    if extra_numbers:
        errors.append(f"ledger contains unmapped logical pages: {extra_numbers}")

    for page in source_pages:
        logical_page = page.get("logical_page")
        entry = ledger_by_page.get(logical_page)
        if entry is None:
            errors.append(f"logical page {logical_page} is missing from the ledger")
            continue
        status = entry.get("status")
        if status not in LEDGER_STATES:
            errors.append(f"logical page {logical_page} has invalid status: {status!r}")
            continue

        needs_text = page_requires_text(page)
        if needs_text and status != "verified":
            errors.append(f"logical page {logical_page} requires verified source text")
            continue
        if not needs_text and status == "verified":
            errors.append(f"logical page {logical_page} is blank/excluded but is marked verified")
            continue
        if status != "verified":
            if not str(entry.get("notes", "")).strip():
                errors.append(f"logical page {logical_page} needs notes for status {status}")
            continue

        relative_path = entry.get("source_file")
        if not isinstance(relative_path, str) or not relative_path.strip():
            errors.append(f"logical page {logical_page} needs source_file")
            continue
        source_file = resolve_under(text_root, relative_path)
        if source_file is None:
            errors.append(f"logical page {logical_page} source path escapes text root: {relative_path}")
            continue
        if not source_file.is_file():
            errors.append(f"logical page {logical_page} source file is missing: {relative_path}")
            continue
        if not source_file.read_text(encoding="utf-8").strip():
            errors.append(f"logical page {logical_page} source file is empty: {relative_path}")
            continue
        actual_hash = sha256_file(source_file)
        if entry.get("source_sha256") != actual_hash:
            errors.append(f"logical page {logical_page} source SHA-256 does not match")
        if not str(entry.get("verified_by", "")).strip():
            errors.append(f"logical page {logical_page} is verified without verified_by")

        if require_locutor:
            locutor_path = entry.get("locutor_file")
            if not isinstance(locutor_path, str) or not locutor_path.strip():
                errors.append(f"logical page {logical_page} needs locutor_file")
                continue
            locutor_file = resolve_under(text_root, locutor_path)
            if locutor_file is None:
                errors.append(f"logical page {logical_page} locutor path escapes text root: {locutor_path}")
                continue
            if not locutor_file.is_file() or not locutor_file.read_text(encoding="utf-8").strip():
                errors.append(f"logical page {logical_page} locutor file is missing or empty")
                continue
            if entry.get("locutor_sha256") != sha256_file(locutor_file):
                errors.append(f"logical page {logical_page} locutor SHA-256 does not match")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify source text coverage against an Audiobook Codex page ledger.")
    parser.add_argument("--book-map", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--text-root", required=True, type=Path)
    parser.add_argument("--require-locutor", action="store_true")
    args = parser.parse_args()

    try:
        map_path = args.book_map.expanduser().resolve()
        errors = verify(
            load_json(map_path),
            sha256_file(map_path),
            load_json(args.ledger.expanduser().resolve()),
            args.text_root.expanduser().resolve(),
            args.require_locutor,
        )
    except RuntimeError as error:
        print(f"INVALID: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if errors:
        print("INVALID text ledger:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID text ledger")


if __name__ == "__main__":
    main()
