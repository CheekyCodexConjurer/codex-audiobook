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


def chapter_output_records(ledger: object) -> dict[str, dict]:
    if not isinstance(ledger, dict) or not isinstance(ledger.get("chapter_outputs"), list):
        return {}
    records: dict[str, dict] = {}
    for entry in ledger["chapter_outputs"]:
        if not isinstance(entry, dict):
            continue
        output_id = entry.get("id")
        if isinstance(output_id, str) and output_id.strip():
            records[output_id] = entry
    return records


def expected_chapter_outputs(book_map: dict, text_root: Path) -> tuple[dict[str, str], list[str]]:
    expected: dict[str, str] = {}
    errors: list[str] = []
    chapters_root = text_root / "source" / "chapters"
    if chapters_root.is_dir():
        for front_file in sorted(chapters_root.glob("front-*.txt")):
            parts = front_file.stem.split("-", 2)
            if len(parts) < 2 or not parts[1]:
                errors.append(f"Cannot derive a front-matter output id from {front_file.name}")
                continue
            output_id = f"front-{parts[1]}"
            if output_id in expected:
                errors.append(f"Duplicate chapter output id derived from source files: {output_id}")
                continue
            expected[output_id] = front_file.relative_to(text_root).as_posix()

    for chapter in sorted(book_map.get("chapters", []), key=lambda entry: entry.get("number", 0)):
        if not isinstance(chapter, dict):
            continue
        chapter_id = chapter.get("id")
        number = chapter.get("number")
        if not isinstance(chapter_id, str) or not chapter_id.strip() or not isinstance(number, int):
            errors.append("Each mapped chapter needs a non-empty id and integer number")
            continue
        matches = sorted(chapters_root.glob(f"chapter-{number:02d}-*.txt")) if chapters_root.is_dir() else []
        if len(matches) != 1:
            errors.append(f"Expected exactly one chapter source TXT for {chapter_id}")
            continue
        if chapter_id in expected:
            errors.append(f"Duplicate chapter output id in book map: {chapter_id}")
            continue
        expected[chapter_id] = matches[0].relative_to(text_root).as_posix()

    if expected:
        return expected, errors

    fallback = text_root / "source" / "book.txt"
    if not fallback.is_file():
        errors.append("No front, chapter, or book source TXT files are available for EPUB export")
    else:
        expected["book"] = fallback.relative_to(text_root).as_posix()
    return expected, errors


def verify_chapter_outputs(
    book_map: dict,
    ledger: dict,
    ledger_by_page: dict[int, dict],
    text_root: Path,
) -> list[str]:
    outputs = ledger.get("chapter_outputs")
    if not isinstance(outputs, list) or not outputs:
        return ["ledger must include non-empty chapter_outputs before EPUB export"]

    errors: list[str] = []
    expected, expected_errors = expected_chapter_outputs(book_map, text_root)
    errors += expected_errors
    ids: set[str] = set()
    pages_by_output: dict[str, set[int]] = {}
    all_output_pages: dict[int, str] = {}
    for index, entry in enumerate(outputs):
        label = f"ledger.chapter_outputs[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{label} must be an object")
            continue
        output_id = entry.get("id")
        if not isinstance(output_id, str) or not output_id.strip():
            errors.append(f"{label}.id must be non-empty")
        elif output_id in ids:
            errors.append(f"{label}.id is duplicated: {output_id}")
        else:
            ids.add(output_id)

        relative_path = entry.get("source_file")
        source_file = resolve_under(text_root, relative_path)
        normalized_path = str(relative_path).replace("\\", "/") if isinstance(relative_path, str) else ""
        if source_file is None or not (
            normalized_path.startswith("source/chapters/") or normalized_path == "source/book.txt"
        ):
            errors.append(f"{label}.source_file must resolve under source/chapters or source/book.txt")
        elif not source_file.is_file() or not source_file.read_text(encoding="utf-8").strip():
            errors.append(f"{label}.source_file is missing or empty: {relative_path}")
        elif entry.get("source_sha256") != sha256_file(source_file):
            errors.append(f"{label}.source_sha256 does not match source_file")
        if isinstance(output_id, str) and output_id in expected and normalized_path != expected[output_id]:
            errors.append(
                f"{label}.source_file does not match the expected source TXT for {output_id}"
            )

        source_pages = entry.get("source_pages")
        output_pages: set[int] = set()
        if not isinstance(source_pages, list) or not source_pages:
            errors.append(f"{label}.source_pages must be a non-empty array")
        else:
            seen_pages: set[int] = set()
            for page_index, source_page in enumerate(source_pages):
                page_label = f"{label}.source_pages[{page_index}]"
                if not isinstance(source_page, dict):
                    errors.append(f"{page_label} must be an object")
                    continue
                logical_page = source_page.get("logical_page")
                if not isinstance(logical_page, int) or logical_page <= 0:
                    errors.append(f"{page_label}.logical_page must be positive")
                    continue
                if logical_page in seen_pages:
                    errors.append(f"{page_label}.logical_page is duplicated: {logical_page}")
                    continue
                seen_pages.add(logical_page)
                output_pages.add(logical_page)
                previous_output = all_output_pages.get(logical_page)
                if previous_output is not None and previous_output != output_id:
                    errors.append(
                        f"{page_label}.logical_page is already claimed by chapter output {previous_output}"
                    )
                elif isinstance(output_id, str):
                    all_output_pages[logical_page] = output_id
                page_ledger = ledger_by_page.get(logical_page)
                if not isinstance(page_ledger, dict) or page_ledger.get("status") != "verified":
                    errors.append(f"{page_label} must reference a verified ledger page")
                    continue
                if source_page.get("source_sha256") != page_ledger.get("source_sha256"):
                    errors.append(f"{page_label}.source_sha256 does not match the page ledger")
        if not str(entry.get("verified_by", "")).strip():
            errors.append(f"{label}.verified_by must be non-empty")
        if isinstance(output_id, str) and output_id.strip():
            pages_by_output[output_id] = output_pages

    missing_outputs = sorted(set(expected) - ids)
    if missing_outputs:
        errors.append(f"ledger.chapter_outputs is missing expected records: {missing_outputs}")
    extra_outputs = sorted(ids - set(expected))
    if extra_outputs:
        errors.append(f"ledger.chapter_outputs contains unknown records: {extra_outputs}")

    required_pages = {
        page.get("logical_page")
        for page in book_map.get("pages", [])
        if isinstance(page, dict)
        and isinstance(page.get("logical_page"), int)
        and page_requires_text(page)
    }
    chapters_by_id = {
        chapter.get("id"): chapter
        for chapter in book_map.get("chapters", [])
        if isinstance(chapter, dict) and isinstance(chapter.get("id"), str)
    }
    for output_id in set(expected) & set(pages_by_output):
        expected_pages: set[int] | None = None
        if output_id == "book":
            expected_pages = required_pages
        elif output_id in chapters_by_id:
            expected_pages = {
                page.get("logical_page")
                for page in book_map.get("pages", [])
                if isinstance(page, dict)
                and page.get("chapter_id") == output_id
                and isinstance(page.get("logical_page"), int)
                and page_requires_text(page)
            }
        if expected_pages is not None and pages_by_output[output_id] != expected_pages:
            errors.append(
                f"chapter output {output_id} must reference exactly its mapped verified pages"
            )

    front_outputs = {output_id for output_id in expected if output_id.startswith("front-")}
    if front_outputs:
        expected_front_pages = {
            page.get("logical_page")
            for page in book_map.get("pages", [])
            if isinstance(page, dict)
            and not page.get("chapter_id")
            and isinstance(page.get("logical_page"), int)
            and page_requires_text(page)
        }
        actual_front_pages = set().union(
            *(pages_by_output.get(output_id, set()) for output_id in front_outputs)
        )
        if actual_front_pages != expected_front_pages:
            errors.append("front-matter chapter outputs must reference exactly the unmapped verified pages")
    return errors


def verify(
    book_map: object,
    book_map_sha256: str,
    ledger: object,
    text_root: Path,
    require_locutor: bool,
    require_chapter_outputs: bool = False,
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

    if require_chapter_outputs:
        errors += verify_chapter_outputs(book_map, ledger, ledger_by_page, text_root)

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Verify source text coverage against an Audiobook Codex page ledger.")
    parser.add_argument("--book-map", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--text-root", required=True, type=Path)
    parser.add_argument("--require-locutor", action="store_true")
    parser.add_argument("--require-chapter-outputs", action="store_true")
    args = parser.parse_args()

    try:
        map_path = args.book_map.expanduser().resolve()
        errors = verify(
            load_json(map_path),
            sha256_file(map_path),
            load_json(args.ledger.expanduser().resolve()),
            args.text_root.expanduser().resolve(),
            args.require_locutor,
            args.require_chapter_outputs,
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
