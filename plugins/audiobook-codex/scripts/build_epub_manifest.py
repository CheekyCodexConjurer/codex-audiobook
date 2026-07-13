from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from epub_presentation import default_visual_profile
from validate_assets_manifest import load_json as load_assets_json
from validate_assets_manifest import validate_assets_manifest
from validate_book_map import load_json as load_book_map_json
from validate_book_map import validate_book_map
from verify_text_ledger import verify as verify_text_ledger


FIGURE_ROLES = {"illustration", "facsimile"}
FIGURE_PLACEMENTS = {"after_title", "end"}


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_title(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def title_from_file(path: Path, fallback: str) -> str:
    lines = [line.strip() for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not lines:
        return fallback
    return normalize_title(" ".join(lines[:2]))[:160] or fallback


def source_relative(book_root: Path, path: Path) -> str:
    return path.resolve().relative_to(book_root.resolve()).as_posix()


def chapter_file(chapters_root: Path, number: int) -> Path | None:
    matches = sorted(chapters_root.glob(f"chapter-{number:02d}-*.txt"))
    return matches[0] if matches else None


def document_kind_for_front(path: Path) -> str:
    return "cover" if "cover" in path.stem.lower() else "front_matter"


def has_classification_evidence(asset: dict) -> bool:
    classification = asset.get("classification")
    if not isinstance(classification, dict):
        return False
    content = classification.get("content")
    evidence = classification.get("evidence")
    if not isinstance(content, str) or not content.strip() or content.strip().casefold() == "unknown":
        return False
    return isinstance(evidence, list) and any(
        (isinstance(item, str) and item.strip()) or isinstance(item, dict) and bool(item)
        for item in evidence
    )


def has_reviewed_classification(asset: dict) -> bool:
    classification = asset.get("classification")
    return (
        has_classification_evidence(asset)
        and isinstance(classification, dict)
        and classification.get("text_pixels") in {"none", "printed", "handwriting", "mixed"}
    )


def is_declared_source_cover(asset: dict) -> bool:
    source = asset.get("source") if isinstance(asset.get("source"), dict) else {}
    epub = asset.get("epub") if isinstance(asset.get("epub"), dict) else {}
    return (
        source.get("format") == "epub"
        and source.get("declared_cover") is True
        and epub.get("role") == "cover"
        and epub.get("placement") == "source_cover"
        and epub.get("document_id") is None
        and has_classification_evidence(asset)
    )


def asset_document_assignments(assets_manifest: dict, documents: list[dict]) -> dict[str, list[str]]:
    document_ids = {document["id"] for document in documents}
    cover_document_id = next(
        (
            document["id"]
            for document in documents
            if document.get("kind") in {"cover", "source_cover"}
        ),
        None,
    )
    assignments = {document["id"]: [] for document in documents}
    for asset in assets_manifest.get("assets", []):
        if not isinstance(asset, dict) or not isinstance(asset.get("id"), str):
            continue
        epub = asset.get("epub") if isinstance(asset.get("epub"), dict) else {}
        explicit_document_id = epub.get("document_id")
        if explicit_document_id is not None and explicit_document_id not in document_ids:
            raise RuntimeError(
                f"Asset {asset['id']} references an unknown EPUB document: {explicit_document_id}"
            )
        role = epub.get("role")
        placement = epub.get("placement")
        if role == "unresolved" and placement == "unresolved" and explicit_document_id is None:
            continue
        if is_declared_source_cover(asset):
            if cover_document_id is None:
                raise RuntimeError(f"Declared source cover {asset['id']} has no cover document")
            assignments[cover_document_id].append(asset["id"])
            continue
        if role not in FIGURE_ROLES:
            raise RuntimeError(
                f"Asset {asset['id']} must be unresolved or a reviewed illustration/facsimile"
            )
        if placement not in FIGURE_PLACEMENTS:
            raise RuntimeError(
                f"Asset {asset['id']} must use an explicit EPUB placement: after_title or end"
            )
        if explicit_document_id is None:
            raise RuntimeError(f"Asset {asset['id']} needs an explicit EPUB document_id")
        if not has_reviewed_classification(asset):
            raise RuntimeError(
                f"Asset {asset['id']} needs reviewed non-unknown classification evidence before EPUB export"
            )
        assignments[explicit_document_id].append(asset["id"])
    return assignments


def build_manifest(
    book_root: Path,
    book_map: dict,
    ledger: dict,
    assets_manifest: dict,
    text_root: Path,
    visual_profile: str,
) -> dict:
    chapters_root = text_root / "source" / "chapters"
    if not chapters_root.is_dir():
        raise RuntimeError(f"Missing chapter source directory: {chapters_root}")
    documents: list[dict] = []
    for front_file in sorted(chapters_root.glob("front-*.txt")):
        prefix = front_file.stem.split("-", 2)
        number = prefix[1] if len(prefix) > 1 else f"{len(documents) + 1:02d}"
        fallback = prefix[2].replace("-", " ").title() if len(prefix) > 2 else "Front matter"
        documents.append(
            {
                "id": f"front-{number}",
                "kind": document_kind_for_front(front_file),
                "title": title_from_file(front_file, fallback),
                "source_file": source_relative(book_root, front_file),
                "source_sha256": sha256_file(front_file),
                "asset_ids": [],
            }
        )

    for chapter in sorted(book_map.get("chapters", []), key=lambda entry: entry.get("number", 0)):
        if not isinstance(chapter, dict):
            continue
        number = chapter.get("number")
        chapter_id = chapter.get("id")
        if not isinstance(number, int) or not isinstance(chapter_id, str):
            continue
        path = chapter_file(chapters_root, number)
        if path is None:
            raise RuntimeError(f"Missing chapter source file for {chapter_id}")
        documents.append(
            {
                "id": chapter_id,
                "kind": "chapter",
                "title": str(chapter.get("title") or f"Chapter {number}").strip(),
                "chapter_id": chapter_id,
                "roman_number": chapter.get("roman_number"),
                "source_file": source_relative(book_root, path),
                "source_sha256": sha256_file(path),
                "asset_ids": [],
            }
        )

    if not documents:
        fallback = text_root / "source" / "book.txt"
        if not fallback.is_file():
            raise RuntimeError("No front, chapter, or book source TXT files are available for EPUB export.")
        documents.append(
            {
                "id": "book",
                "kind": "book",
                "title": str(book_map.get("book", {}).get("title") or "Book"),
                "source_file": source_relative(book_root, fallback),
                "source_sha256": sha256_file(fallback),
                "asset_ids": [],
            }
        )

    has_source_cover = any(document.get("kind") in {"cover", "source_cover"} for document in documents)
    declared_cover_assets = [
        asset
        for asset in assets_manifest.get("assets", [])
        if isinstance(asset, dict) and is_declared_source_cover(asset)
    ]
    if declared_cover_assets and not has_source_cover:
        documents.insert(
            0,
            {
                "id": "source-cover",
                "kind": "source_cover",
                "title": "Capa da fonte",
                "source_file": None,
                "source_sha256": None,
                "asset_ids": [],
            },
        )

    assignments = asset_document_assignments(assets_manifest, documents)
    for document in documents:
        document["asset_ids"] = assignments[document["id"]]
    analysis = book_map.get("analysis") if isinstance(book_map.get("analysis"), dict) else {}
    book = book_map.get("book") if isinstance(book_map.get("book"), dict) else {}
    manifest = {
        "schema_version": "1.0",
        "book_map_sha256": sha256_file(book_root / "metadata" / "book-map.json"),
        "text_ledger_sha256": sha256_file(book_root / "metadata" / "text-ledger.json"),
        "assets_manifest_sha256": sha256_file(book_root / "metadata" / "assets-manifest.json"),
        "language": str(analysis.get("source_language") or analysis.get("narration_language") or "pt-BR"),
        "book": {
            "title": str(book.get("title") or "Untitled"),
            "subtitle": str(book.get("subtitle") or ""),
            "author": str(book.get("author") or ""),
            "publication_year": book.get("original_publication_year"),
            "publication_place": str(book.get("original_publication_place") or ""),
        },
        "documents": documents,
    }
    if visual_profile == "antique-paper":
        manifest["visual_profile"] = default_visual_profile()
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an Audiobook Codex EPUB manifest from verified source text.")
    parser.add_argument("--book-map", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--assets-manifest", required=True, type=Path)
    parser.add_argument("--text-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--visual-profile", choices=("antique-paper", "none"), default="antique-paper")
    args = parser.parse_args()

    try:
        map_path = args.book_map.expanduser().resolve()
        book_root = map_path.parent.parent
        ledger_path = args.ledger.expanduser().resolve()
        assets_path = args.assets_manifest.expanduser().resolve()
        text_root = args.text_root.expanduser().resolve()
        canonical_paths = {
            "book-map": (map_path, book_root / "metadata" / "book-map.json"),
            "ledger": (ledger_path, book_root / "metadata" / "text-ledger.json"),
            "assets manifest": (assets_path, book_root / "metadata" / "assets-manifest.json"),
            "text root": (text_root, book_root / "text"),
        }
        for label, (actual, expected) in canonical_paths.items():
            if actual != expected:
                raise RuntimeError(f"{label} must use the canonical path: {expected}")
        book_map = load_book_map_json(map_path)
        ledger = load_assets_json(ledger_path)
        assets_manifest = load_assets_json(assets_path)
        if not isinstance(book_map, dict) or not isinstance(ledger, dict) or not isinstance(assets_manifest, dict):
            raise RuntimeError("Book map, ledger, and assets manifest must be JSON objects.")
        errors = validate_book_map(book_map, book_root, True, True)
        errors += verify_text_ledger(book_map, sha256_file(map_path), ledger, text_root, False, True)
        errors += validate_assets_manifest(assets_manifest, book_root, book_map, True)
        if errors:
            raise RuntimeError("; ".join(errors))
        output = args.output.expanduser().resolve() if args.output else book_root / "metadata" / "epub-manifest.json"
        write_json(
            output,
            build_manifest(
                book_root,
                book_map,
                ledger,
                assets_manifest,
                text_root,
                args.visual_profile,
            ),
        )
    except RuntimeError as error:
        print(f"Cannot build EPUB manifest: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Created {output}")


if __name__ == "__main__":
    main()
