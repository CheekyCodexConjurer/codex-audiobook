from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from swarm_claims import (
    SwarmValidationError,
    atomic_write_text,
    load_json,
    normalized_relative_path,
    resolve_relative,
    sha256_bytes,
    sha256_file,
)


@dataclass(frozen=True)
class PlannedWrite:
    relative_path: str
    content: str


def _with_single_trailing_newline(text: str) -> str:
    return text.rstrip("\n") + "\n"


def join_text_units(parts: list[str]) -> str:
    if not parts:
        return ""
    return "\n\n".join(part.rstrip("\n") for part in parts).rstrip("\n") + "\n"


def _hash_text(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


def _read_text_with_hash(root: Path, relative_path: Any, expected_hash: Any, label: str) -> str:
    path = resolve_relative(root, relative_path, label=f"{label}.path")
    if not path.is_file():
        raise SwarmValidationError(f"{label}.path is missing: {relative_path}")
    if not isinstance(expected_hash, str) or not expected_hash:
        raise SwarmValidationError(f"{label}.sha256 must be non-empty")
    actual_hash = sha256_file(path)
    if actual_hash != expected_hash:
        raise SwarmValidationError(f"{label}.sha256 does not match {relative_path}")
    return path.read_text(encoding="utf-8")


def _pages_by_logical(ledger: dict[str, Any]) -> dict[int, dict[str, Any]]:
    pages = ledger.get("pages")
    if not isinstance(pages, list):
        raise SwarmValidationError("ledger.pages must be an array")
    result: dict[int, dict[str, Any]] = {}
    for index, page in enumerate(pages):
        if not isinstance(page, dict):
            raise SwarmValidationError(f"ledger.pages[{index}] must be an object")
        logical_page = page.get("logical_page")
        if not isinstance(logical_page, int) or isinstance(logical_page, bool) or logical_page <= 0:
            raise SwarmValidationError(f"ledger.pages[{index}].logical_page must be positive")
        if logical_page in result:
            raise SwarmValidationError(f"ledger.pages[{index}].logical_page is duplicated: {logical_page}")
        result[logical_page] = page
    return result


def _chapter_outputs(ledger: dict[str, Any]) -> list[dict[str, Any]]:
    outputs = ledger.get("chapter_outputs")
    if not isinstance(outputs, list):
        raise SwarmValidationError("ledger.chapter_outputs must be an array")
    typed: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, output in enumerate(outputs):
        if not isinstance(output, dict):
            raise SwarmValidationError(f"ledger.chapter_outputs[{index}] must be an object")
        output_id = output.get("id")
        if not isinstance(output_id, str) or not output_id.strip():
            raise SwarmValidationError(f"ledger.chapter_outputs[{index}].id must be non-empty")
        if output_id in seen:
            raise SwarmValidationError(f"ledger.chapter_outputs[{index}].id is duplicated: {output_id}")
        seen.add(output_id)
        typed.append(output)
    return typed


def _plan_chapters_from_pages(
    ledger: dict[str, Any],
    text_root: Path,
    *,
    file_field: str,
    hash_field: str,
    page_file_field: str,
    page_hash_field: str,
) -> list[PlannedWrite]:
    page_index = _pages_by_logical(ledger)
    plans: list[PlannedWrite] = []
    for output_index, output in enumerate(_chapter_outputs(ledger)):
        label = f"ledger.chapter_outputs[{output_index}]"
        source_pages = output.get("source_pages")
        if not isinstance(source_pages, list) or not source_pages:
            raise SwarmValidationError(f"{label}.source_pages must be a non-empty array")
        pieces: list[str] = []
        seen_pages: set[int] = set()
        for source_index, source_page in enumerate(source_pages):
            source_label = f"{label}.source_pages[{source_index}]"
            if not isinstance(source_page, dict):
                raise SwarmValidationError(f"{source_label} must be an object")
            logical_page = source_page.get("logical_page")
            if not isinstance(logical_page, int) or isinstance(logical_page, bool) or logical_page <= 0:
                raise SwarmValidationError(f"{source_label}.logical_page must be positive")
            if logical_page in seen_pages:
                raise SwarmValidationError(f"{source_label}.logical_page is duplicated: {logical_page}")
            seen_pages.add(logical_page)
            page = page_index.get(logical_page)
            if page is None:
                raise SwarmValidationError(f"{source_label}.logical_page is missing from ledger.pages")
            if page.get("status") != "verified":
                raise SwarmValidationError(f"ledger page {logical_page} is not verified")
            pieces.append(
                _read_text_with_hash(
                    text_root,
                    page.get(page_file_field),
                    page.get(page_hash_field),
                    f"page {logical_page}",
                )
            )
        content = join_text_units(pieces)
        expected_hash = output.get(hash_field)
        if not isinstance(expected_hash, str) or not expected_hash:
            raise SwarmValidationError(f"{label}.{hash_field} must be non-empty")
        if _hash_text(content) != expected_hash:
            raise SwarmValidationError(f"{label}.{hash_field} does not match assembled chapter content")
        plans.append(PlannedWrite(normalized_relative_path(output.get(file_field), label=f"{label}.{file_field}"), content))
    return plans


def _plan_book_from_chapter_plans(chapter_plans: list[PlannedWrite], output_file: str) -> PlannedWrite:
    return PlannedWrite(normalized_relative_path(output_file, label="book output"), join_text_units([plan.content for plan in chapter_plans]))


def plan_source_outputs(ledger: dict[str, Any], text_root: Path, book_output: str | None) -> list[PlannedWrite]:
    chapters = _plan_chapters_from_pages(
        ledger,
        text_root,
        file_field="source_file",
        hash_field="source_sha256",
        page_file_field="source_file",
        page_hash_field="source_sha256",
    )
    if book_output:
        return chapters + [_plan_book_from_chapter_plans(chapters, book_output)]
    return chapters


def plan_translation_outputs(ledger: dict[str, Any], text_root: Path, book_output: str | None) -> list[PlannedWrite]:
    chapters = _plan_chapters_from_pages(
        ledger,
        text_root,
        file_field="translation_file",
        hash_field="translation_sha256",
        page_file_field="translation_file",
        page_hash_field="translation_sha256",
    )
    if book_output:
        return chapters + [_plan_book_from_chapter_plans(chapters, book_output)]
    return chapters


def plan_fluid_book(ledger: dict[str, Any], text_root: Path, book_output: str | None = None) -> PlannedWrite:
    outputs = _chapter_outputs(ledger)
    pieces: list[str] = []
    for index, output in enumerate(outputs):
        label = f"ledger.chapter_outputs[{index}]"
        pieces.append(_read_text_with_hash(text_root, output.get("fluid_file"), output.get("fluid_sha256"), label))
    content = join_text_units(pieces)
    if book_output is None:
        book_record = ledger.get("book_output")
        if not isinstance(book_record, dict):
            raise SwarmValidationError("fluid ledger needs book_output when no output path is provided")
        book_output = book_record.get("fluid_file")
        expected_book_hash = book_record.get("fluid_sha256")
        if isinstance(expected_book_hash, str) and expected_book_hash:
            actual = _hash_text(content)
            if actual != expected_book_hash:
                raise SwarmValidationError("fluid book_output.fluid_sha256 does not match assembled content")
    return PlannedWrite(normalized_relative_path(book_output, label="fluid book output"), content)


def write_plans(text_root: Path, plans: list[PlannedWrite]) -> None:
    seen: set[str] = set()
    resolved: list[tuple[Path, str]] = []
    for plan in plans:
        if plan.relative_path in seen:
            raise SwarmValidationError(f"duplicate planned output path: {plan.relative_path}")
        seen.add(plan.relative_path)
        resolved.append((resolve_relative(text_root, plan.relative_path, label="planned output"), plan.content))
    for path, content in resolved:
        atomic_write_text(path, _with_single_trailing_newline(content))


def assemble_source_outputs(ledger: dict[str, Any], text_root: Path, book_output: str | None = None) -> list[PlannedWrite]:
    plans = plan_source_outputs(ledger, text_root, book_output)
    write_plans(text_root, plans)
    return plans


def assemble_translation_outputs(ledger: dict[str, Any], text_root: Path, book_output: str | None = None) -> list[PlannedWrite]:
    plans = plan_translation_outputs(ledger, text_root, book_output)
    write_plans(text_root, plans)
    return plans


def assemble_fluid_book(ledger: dict[str, Any], text_root: Path, book_output: str | None = None) -> PlannedWrite:
    plan = plan_fluid_book(ledger, text_root, book_output)
    write_plans(text_root, [plan])
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description="Assemble Audiobook Codex text outputs from verified ledgers.")
    parser.add_argument("--text-root", required=True, type=Path)
    parser.add_argument("--text-ledger", type=Path)
    parser.add_argument("--translation-ledger", type=Path)
    parser.add_argument("--fluid-ledger", type=Path)
    parser.add_argument("--source-book-output", help="Relative text-root path for source/book output.")
    parser.add_argument("--translation-book-output", help="Relative text-root path for translation/book output.")
    parser.add_argument("--fluid-book-output", help="Relative text-root path for fluid/book output; defaults to ledger book_output.fluid_file.")
    args = parser.parse_args()

    text_root = args.text_root.expanduser().resolve()
    try:
        if args.text_ledger:
            if not args.source_book_output:
                raise SwarmValidationError("--source-book-output is required with --text-ledger")
            assemble_source_outputs(load_json(args.text_ledger.expanduser().resolve()), text_root, args.source_book_output)
        if args.translation_ledger:
            if not args.translation_book_output:
                raise SwarmValidationError("--translation-book-output is required with --translation-ledger")
            assemble_translation_outputs(load_json(args.translation_ledger.expanduser().resolve()), text_root, args.translation_book_output)
        if args.fluid_ledger:
            assemble_fluid_book(load_json(args.fluid_ledger.expanduser().resolve()), text_root, args.fluid_book_output)
    except RuntimeError as error:
        raise SystemExit(f"INVALID text assembly: {error}") from error
    print("ASSEMBLED text outputs")


if __name__ == "__main__":
    main()
