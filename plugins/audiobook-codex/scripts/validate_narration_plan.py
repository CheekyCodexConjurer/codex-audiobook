from __future__ import annotations

import argparse
from pathlib import Path
import sys

from book_layout import resolve_book_paths
from narration_plan import (
    POLICY_NAME,
    SCHEMA_VERSION,
    load_plan_segments,
    read_json,
    sha256_file,
)


def validate_plan(book_root: Path, input_file: Path, plan_path: Path) -> tuple[list[str], dict | None]:
    errors: list[str] = []
    try:
        plan = read_json(plan_path, "narration plan")
    except RuntimeError as error:
        return [str(error)], None
    if plan.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"narration plan schema_version must be {SCHEMA_VERSION!r}")
    policy = plan.get("policy")
    if not isinstance(policy, dict) or policy.get("name") != POLICY_NAME:
        errors.append(f"narration plan policy.name must be {POLICY_NAME!r}")
    if not isinstance(policy, dict) or not isinstance(policy.get("max_chars"), int):
        errors.append("narration plan policy.max_chars must be an integer")
    elif not 80 <= policy["max_chars"] <= 320:
        errors.append("narration plan policy.max_chars must be between 80 and 320")
    expected_hashes = {
        "book_map_sha256": book_root / "metadata" / "book-map.json",
        "text_ledger_sha256": book_root / "metadata" / "text-ledger.json",
        "narrator_changes_sha256": book_root / "metadata" / "narrator-changes.json",
        "narrator_review_sha256": book_root / "metadata" / "narrator-review.json",
    }
    try:
        narrator_changes = read_json(
            book_root / "metadata" / "narrator-changes.json",
            "narrator changes",
        )
    except RuntimeError as error:
        errors.append(str(error))
        narrator_changes = {}
    base_edition = narrator_changes.get("base_edition")
    if base_edition == "source":
        expected_hashes["base_ledger_sha256"] = book_root / "metadata" / "text-ledger.json"
    elif base_edition == "translated-pt-br":
        expected_hashes["base_ledger_sha256"] = (
            book_root / "metadata" / "translation-ledger.json"
        )
    elif base_edition == "fluid-pt-br":
        expected_hashes["base_ledger_sha256"] = (
            book_root / "metadata" / "fluid-edition-ledger.json"
        )
    else:
        errors.append(
            "narrator changes base_edition must be source, translated-pt-br, "
            "or fluid-pt-br"
        )
    for key, path in expected_hashes.items():
        if not path.is_file():
            errors.append(f"narration plan requires {path.name}")
        elif plan.get(key) != sha256_file(path):
            errors.append(f"narration plan {key} does not match {path.name}")
    try:
        segments = load_plan_segments(book_root, input_file, plan)
    except RuntimeError as error:
        errors.append(str(error))
        segments = []
    if segments:
        if segments[-1].pause_after_kind != "end" or segments[-1].pause_after_seconds != 0:
            errors.append("narration plan final segment must end without appended silence")
        for segment in segments[:-1]:
            if segment.pause_after_kind == "end":
                errors.append("only the final narration segment may use an end pause")
                break
            if segment.pause_after_seconds is None or segment.pause_after_seconds < 0:
                errors.append("narration plan pauses must be non-negative")
                break
    if errors:
        return errors, None
    return (
        [],
        {
            "path": plan_path.relative_to(book_root).as_posix(),
            "sha256": sha256_file(plan_path),
            "policy": POLICY_NAME,
        },
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a semantic audiobook narration plan.")
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--input-file", type=Path)
    parser.add_argument("--narration-plan", type=Path)
    args = parser.parse_args()

    book_root = resolve_book_paths(args.book_root).assembly_root
    input_file = (
        args.input_file.expanduser().resolve()
        if args.input_file
        else book_root / "text" / "locutor" / "book.txt"
    )
    plan_path = (
        args.narration_plan.expanduser().resolve()
        if args.narration_plan
        else book_root / "metadata" / "narration-plan.json"
    )
    errors, provenance = validate_plan(book_root, input_file, plan_path)
    if errors:
        print("INVALID narration plan:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID narration plan")
    print(provenance)


if __name__ == "__main__":
    main()
