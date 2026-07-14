from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from path_safety import resolve_under


READY_STATES = {"ready", "approved"}
PAGE_STATES = {"needs_analysis", "mapped", "verified", "blank", "excluded"}
SIDES = {"single", "left", "right", "reflow"}
FORMATS = {"pdf", "epub"}


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def is_positive_integer(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def require_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def validate_book_map(book_map: object, root: Path, require_ready: bool, check_files: bool) -> list[str]:
    errors: list[str] = []
    if not isinstance(book_map, dict):
        return ["book map must be a JSON object"]
    if book_map.get("schema_version") != "1.0":
        errors.append("schema_version must be '1.0'")

    source = book_map.get("source")
    if not isinstance(source, dict):
        errors.append("source must be an object")
        source = {}
    source_format = source.get("format")
    valid_source_format = isinstance(source_format, str) and source_format in FORMATS
    if not valid_source_format:
        errors.append("source.format must be pdf or epub")
    if not is_sha256(source.get("sha256")):
        errors.append("source.sha256 must be a SHA-256 hex string")
    source_path = source.get("path")
    stored_source = resolve_under(root, source_path, (Path("source"),))
    expected_source = (
        (root / "source" / f"original.{source_format}").resolve()
        if valid_source_format
        else None
    )
    if stored_source is None or expected_source is None or stored_source != expected_source:
        expected_source_label = (
            f"source/original.{source_format}"
            if valid_source_format
            else "source/original.pdf or source/original.epub"
        )
        errors.append(
            f"source.path must resolve to the immutable stored source: {expected_source_label}"
        )
    elif check_files:
        if not stored_source.is_file():
            errors.append(f"source.path is missing: {source_path}")
        elif source.get("sha256") != sha256_file(stored_source):
            errors.append("source.sha256 does not match source.path")
    logical_count = source.get("page_count_logical")
    if not is_positive_integer(logical_count):
        errors.append("source.page_count_logical must be a positive integer")
        logical_count = 0

    analysis = book_map.get("analysis")
    if not isinstance(analysis, dict):
        errors.append("analysis must be an object")
        analysis = {}
    if analysis.get("status") not in {"needs_analysis", "draft", "ready", "approved"}:
        errors.append("analysis.status is invalid")
    if analysis.get("layout") not in {"single", "spread", "reflow"}:
        errors.append("analysis.layout is invalid")
    if analysis.get("rotation") not in {"normal", "cw90", "ccw90", "180"}:
        errors.append("analysis.rotation is invalid")
    if not require_text(analysis.get("narration_language")):
        errors.append("analysis.narration_language must be non-empty")
    if require_ready and analysis.get("status") not in READY_STATES:
        errors.append("analysis.status must be ready or approved")

    pages = book_map.get("pages")
    if not isinstance(pages, list) or not pages:
        errors.append("pages must be a non-empty array")
        pages = []
    seen_pages: set[int] = set()
    for index, page in enumerate(pages, start=1):
        label = f"pages[{index - 1}]"
        if not isinstance(page, dict):
            errors.append(f"{label} must be an object")
            continue
        logical_page = page.get("logical_page")
        if not is_positive_integer(logical_page):
            errors.append(f"{label}.logical_page must be a positive integer")
        elif logical_page in seen_pages:
            errors.append(f"{label}.logical_page is duplicated: {logical_page}")
        else:
            seen_pages.add(logical_page)
        if page.get("side") not in SIDES:
            errors.append(f"{label}.side is invalid")
        if not is_positive_integer(page.get("source_page")) and not require_text(page.get("source_locator")):
            errors.append(f"{label} needs source_page or source_locator")
        if page.get("status") not in PAGE_STATES:
            errors.append(f"{label}.status is invalid")
        if not isinstance(page.get("blank"), (bool, type(None))):
            errors.append(f"{label}.blank must be true, false, or null")
        if not isinstance(page.get("evidence"), list):
            errors.append(f"{label}.evidence must be an array")
        render_path = page.get("render_path", "")
        if render_path:
            target = resolve_under(root, render_path)
            if target is None:
                errors.append(f"{label}.render_path escapes the book root: {render_path}")
            elif check_files and not target.is_file():
                errors.append(f"{label}.render_path is missing: {render_path}")

    if logical_count and seen_pages != set(range(1, logical_count + 1)):
        errors.append("pages must cover each logical_page from 1 through source.page_count_logical exactly once")
    if require_ready and any(page.get("status") == "needs_analysis" for page in pages if isinstance(page, dict)):
        errors.append("ready map contains pages still marked needs_analysis")

    alignment = book_map.get("page_number_alignment")
    if not isinstance(alignment, dict) or not isinstance(alignment.get("segments"), list):
        errors.append("page_number_alignment.segments must be an array")
        segments = []
    else:
        segments = alignment["segments"]
    previous_end = 0
    for index, segment in enumerate(sorted(segments, key=lambda item: item.get("logical_start_page", 0) if isinstance(item, dict) else 0)):
        label = f"page_number_alignment.segments[{index}]"
        if not isinstance(segment, dict):
            errors.append(f"{label} must be an object")
            continue
        start = segment.get("logical_start_page")
        end = segment.get("logical_end_page")
        offset = segment.get("pdf_to_printed_page_offset")
        if not is_positive_integer(start) or not is_positive_integer(end) or start > end:
            errors.append(f"{label} has an invalid logical page range")
            continue
        if logical_count and end > logical_count:
            errors.append(f"{label} exceeds source.page_count_logical")
        if start <= previous_end:
            errors.append(f"{label} overlaps a previous alignment segment")
        previous_end = end
        if not isinstance(offset, int) or isinstance(offset, bool):
            errors.append(f"{label}.pdf_to_printed_page_offset must be an integer")
        if require_ready and not segment.get("evidence"):
            errors.append(f"{label} needs evidence before a map is ready")

    chapters = book_map.get("chapters")
    if not isinstance(chapters, list):
        errors.append("chapters must be an array")
        chapters = []
    previous_end = 0
    chapter_ids: set[str] = set()
    for index, chapter in enumerate(sorted(chapters, key=lambda item: item.get("start_logical_page", 0) if isinstance(item, dict) else 0)):
        label = f"chapters[{index}]"
        if not isinstance(chapter, dict):
            errors.append(f"{label} must be an object")
            continue
        chapter_id = chapter.get("id")
        if not require_text(chapter_id):
            errors.append(f"{label}.id must be non-empty")
        elif chapter_id in chapter_ids:
            errors.append(f"{label}.id is duplicated: {chapter_id}")
        else:
            chapter_ids.add(chapter_id)
        if not is_positive_integer(chapter.get("number")):
            errors.append(f"{label}.number must be positive")
        if not require_text(chapter.get("title")):
            errors.append(f"{label}.title must be non-empty")
        start = chapter.get("start_logical_page")
        end = chapter.get("end_logical_page")
        if not is_positive_integer(start) or not is_positive_integer(end) or start > end:
            errors.append(f"{label} has an invalid logical page range")
            continue
        if logical_count and end > logical_count:
            errors.append(f"{label} exceeds source.page_count_logical")
        if start <= previous_end:
            errors.append(f"{label} overlaps a previous chapter")
        previous_end = end

    if require_ready and not chapters:
        errors.append("ready map must contain at least one chapter")
    ranges = book_map.get("ranges")
    if not isinstance(ranges, dict):
        errors.append("ranges must be an object")
    elif not isinstance(ranges.get("ignored", []), list) or not isinstance(ranges.get("narration_excluded", []), list):
        errors.append("ranges.ignored and ranges.narration_excluded must be arrays")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an Audiobook Codex book-map.json file.")
    parser.add_argument("--book-map", required=True, type=Path)
    parser.add_argument("--require-ready", action="store_true")
    parser.add_argument("--check-files", action="store_true")
    args = parser.parse_args()

    map_path = args.book_map.expanduser().resolve()
    try:
        book_map = load_json(map_path)
        errors = validate_book_map(book_map, map_path.parent.parent, args.require_ready, args.check_files)
    except RuntimeError as error:
        print(f"INVALID: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if errors:
        print("INVALID book map:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"VALID: {map_path}")


if __name__ == "__main__":
    main()
