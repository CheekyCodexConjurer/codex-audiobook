from __future__ import annotations

import argparse
import json
from pathlib import Path

from book_layout import resolve_book_paths
from verify_translation_ledger import is_portuguese_language


SCHEMA_VERSION = "1.0"
SELECTION_FILENAME = "publication-selection.json"
TARGETS = ("complete", "fluid", "both")
COMPLETE_TEXT_EDITIONS = {"original", "revised-pt-br", "translated-pt-br"}


def selection_path(book_root: Path) -> Path:
    return book_root / "metadata" / SELECTION_FILENAME


def default_selection() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "target": "complete",
        "updated_by": "",
        "reason": "",
    }


def legacy_selection() -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "target": "both",
        "updated_by": "legacy-default",
        "reason": "No publication selection exists; preserve legacy edition access.",
    }


def load_selection(book_root: Path, *, required: bool = False) -> dict:
    path = selection_path(book_root)
    if not path.is_file():
        if required:
            raise RuntimeError(f"Publication selection is required: {path}")
        return legacy_selection()
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read publication selection {path}: {error}") from error
    validate_selection(value)
    return value


def _has_explicit_selection(book_root: Path) -> bool:
    return selection_path(book_root).is_file()


def _source_language(book_root: Path) -> str:
    path = book_root / "metadata" / "book-map.json"
    try:
        book_map = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read book map {path}: {error}") from error
    analysis = book_map.get("analysis") if isinstance(book_map, dict) else None
    language = analysis.get("source_language") if isinstance(analysis, dict) else None
    if not isinstance(language, str) or not language.strip():
        raise RuntimeError(
            "Publication selection requires book-map analysis.source_language."
        )
    return language.strip().casefold()


def _require_complete_edition(
    book_root: Path,
    target: str,
    complete_edition: bool,
) -> None:
    if target not in {"complete", "both"} or not complete_edition:
        return
    if not _has_explicit_selection(book_root):
        return
    language = _source_language(book_root)
    if is_portuguese_language(language):
        return
    raise RuntimeError(
        "A whole non-Portuguese book must publish translated-pt-br as its complete "
        "edition; original/source is not allowed by the explicit publication selection."
    )


def validate_selection(value: object) -> None:
    if not isinstance(value, dict):
        raise RuntimeError("Publication selection must be a JSON object.")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise RuntimeError(
            f"Publication selection schema_version must be {SCHEMA_VERSION!r}."
        )
    if value.get("target") not in TARGETS:
        raise RuntimeError(
            "Publication selection target must be complete, fluid, or both."
        )
    for field in ("updated_by", "reason"):
        if not isinstance(value.get(field), str):
            raise RuntimeError(f"Publication selection {field} must be a string.")


def supports_text_edition(target: str, text_edition: str) -> bool:
    if target == "both":
        return text_edition in COMPLETE_TEXT_EDITIONS | {"fluid-pt-br"}
    if target == "complete":
        return text_edition in COMPLETE_TEXT_EDITIONS
    return text_edition == "fluid-pt-br"


def supports_narrator_base(target: str, base_edition: str) -> bool:
    if target == "both":
        return base_edition in {"source", "translated-pt-br", "fluid-pt-br"}
    if target == "complete":
        return base_edition in {"source", "translated-pt-br"}
    return base_edition == "fluid-pt-br"


def uses_unsuffixed_fluid_export_name(book_root: Path, text_edition: str) -> bool:
    """Whether the selected fluid edition is the only public reader edition."""
    return (
        text_edition == "fluid-pt-br"
        and load_selection(book_root)["target"] == "fluid"
    )


def require_text_edition(book_root: Path, text_edition: str) -> dict:
    selection = load_selection(book_root)
    target = selection["target"]
    if not supports_text_edition(target, text_edition):
        raise RuntimeError(
            f"Publication target {target!r} does not allow text edition "
            f"{text_edition!r}. Update metadata/{SELECTION_FILENAME} first."
        )
    _require_complete_edition(
        book_root,
        target,
        text_edition in {"original", "revised-pt-br"},
    )
    return selection


def require_narrator_base(book_root: Path, base_edition: str) -> dict:
    selection = load_selection(book_root)
    target = selection["target"]
    if not supports_narrator_base(target, base_edition):
        raise RuntimeError(
            f"Publication target {target!r} does not allow narrator base edition "
            f"{base_edition!r}. Update metadata/{SELECTION_FILENAME} first."
        )
    _require_complete_edition(
        book_root,
        target,
        base_edition == "source",
    )
    return selection


def write_selection(path: Path, target: str, updated_by: str, reason: str) -> None:
    value = {
        "schema_version": SCHEMA_VERSION,
        "target": target,
        "updated_by": updated_by,
        "reason": reason,
    }
    validate_selection(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read or update the per-book complete/fluid publication target."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--target", choices=TARGETS)
    parser.add_argument("--updated-by", default="")
    parser.add_argument("--reason", default="")
    args = parser.parse_args()

    book_root = resolve_book_paths(args.book_root).assembly_root
    path = selection_path(book_root)
    try:
        if args.target is None:
            print(json.dumps(load_selection(book_root), ensure_ascii=False, indent=2))
            return
        write_selection(path, args.target, args.updated_by, args.reason)
        print(f"Updated {path}")
    except RuntimeError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    main()
