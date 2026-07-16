from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

from epub_layout import layout_descriptor
from epub_layout import load_json as load_layout_json
from epub_layout import validate_layout
from epub_presentation import default_visual_profile
from validate_assets_manifest import load_json as load_assets_json
from validate_assets_manifest import validate_assets_manifest
from validate_book_map import load_json as load_book_map_json
from validate_book_map import validate_book_map
from verify_text_ledger import chapter_output_records
from verify_text_ledger import verify as verify_text_ledger
from verify_translation_ledger import TARGET_LANGUAGE
from verify_translation_ledger import translated_document_titles
from verify_translation_ledger import translation_chapter_output_records
from verify_translation_ledger import verify as verify_translation_ledger
from verify_revision_ledger import TARGET_LANGUAGE as REVISION_LANGUAGE
from verify_revision_ledger import revision_chapter_output_records
from verify_revision_ledger import verify as verify_revision_ledger


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


def build_source_manifest(
    book_root: Path,
    book_map: dict,
    ledger: dict,
    assets_manifest: dict,
    text_root: Path,
    visual_profile: str,
    layout_path: Path | None,
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
        "text_edition": "original",
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
    if layout_path is not None:
        layout = load_layout_json(layout_path)
        errors = validate_layout(
            layout,
            book_root,
            sha256_file(book_root / "metadata" / "book-map.json"),
            sha256_file(book_root / "metadata" / "text-ledger.json"),
            ledger,
            [document["id"] for document in documents if document.get("kind") != "source_cover"],
        )
        if errors:
            raise RuntimeError("; ".join(errors))
        manifest["layout"] = layout_descriptor(book_root, layout_path)
    return manifest


def build_translated_manifest(
    book_root: Path,
    book_map: dict,
    ledger: dict,
    translation_ledger: dict,
    assets_manifest: dict,
    text_root: Path,
    visual_profile: str,
) -> dict:
    source_outputs = chapter_output_records(ledger)
    translated_outputs = translation_chapter_output_records(translation_ledger)
    translated_titles = translated_document_titles(translation_ledger)
    source_chapters_root = text_root / "source" / "chapters"
    documents: list[dict] = []

    for front_file in sorted(source_chapters_root.glob("front-*.txt")):
        prefix = front_file.stem.split("-", 2)
        number = prefix[1] if len(prefix) > 1 else f"{len(documents) + 1:02d}"
        document_id = f"front-{number}"
        source_output = source_outputs.get(document_id)
        translated_output = translated_outputs.get(document_id)
        if not isinstance(source_output, dict) or not isinstance(translated_output, dict):
            raise RuntimeError(f"Missing validated translated output for {document_id}")
        documents.append(
            {
                "id": document_id,
                "kind": document_kind_for_front(front_file),
                "title": translated_titles[document_id],
                "source_file": f"text/{source_output['source_file']}",
                "source_sha256": source_output["source_sha256"],
                "translation_file": f"text/{translated_output['translation_file']}",
                "translation_sha256": translated_output["translation_sha256"],
                "asset_ids": [],
            }
        )

    for chapter in sorted(book_map.get("chapters", []), key=lambda entry: entry.get("number", 0)):
        if not isinstance(chapter, dict):
            continue
        chapter_id = chapter.get("id")
        if not isinstance(chapter_id, str):
            continue
        source_output = source_outputs.get(chapter_id)
        translated_output = translated_outputs.get(chapter_id)
        if not isinstance(source_output, dict) or not isinstance(translated_output, dict):
            raise RuntimeError(f"Missing validated translated output for {chapter_id}")
        documents.append(
            {
                "id": chapter_id,
                "kind": "chapter",
                "title": translated_titles[chapter_id],
                "chapter_id": chapter_id,
                "roman_number": chapter.get("roman_number"),
                "source_file": f"text/{source_output['source_file']}",
                "source_sha256": source_output["source_sha256"],
                "translation_file": f"text/{translated_output['translation_file']}",
                "translation_sha256": translated_output["translation_sha256"],
                "asset_ids": [],
            }
        )

    if not documents:
        source_output = source_outputs.get("book")
        translated_output = translated_outputs.get("book")
        if not isinstance(source_output, dict) or not isinstance(translated_output, dict):
            raise RuntimeError("Missing validated translated book output")
        documents.append(
            {
                "id": "book",
                "kind": "book",
                "title": translated_titles["book"],
                "source_file": f"text/{source_output['source_file']}",
                "source_sha256": source_output["source_sha256"],
                "translation_file": f"text/{translated_output['translation_file']}",
                "translation_sha256": translated_output["translation_sha256"],
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

    source_book = book_map.get("book") if isinstance(book_map.get("book"), dict) else {}
    edition = translation_ledger.get("edition") if isinstance(translation_ledger.get("edition"), dict) else {}
    translated_book = edition.get("book") if isinstance(edition.get("book"), dict) else {}
    manifest = {
        "schema_version": "1.0",
        "text_edition": "translated-pt-br",
        "book_map_sha256": sha256_file(book_root / "metadata" / "book-map.json"),
        "text_ledger_sha256": sha256_file(book_root / "metadata" / "text-ledger.json"),
        "translation_ledger_sha256": sha256_file(book_root / "metadata" / "translation-ledger.json"),
        "assets_manifest_sha256": sha256_file(book_root / "metadata" / "assets-manifest.json"),
        "source_language": translation_ledger["source_language"],
        "language": TARGET_LANGUAGE,
        "book": {
            "title": str(translated_book["title"]),
            "subtitle": str(translated_book.get("subtitle") or ""),
            "author": str(source_book.get("author") or ""),
            "publication_year": source_book.get("original_publication_year"),
            "publication_place": str(source_book.get("original_publication_place") or ""),
        },
        "documents": documents,
    }
    if visual_profile == "antique-paper":
        manifest["visual_profile"] = default_visual_profile()
    return manifest


def build_revised_manifest(
    book_root: Path,
    book_map: dict,
    ledger: dict,
    revision_ledger: dict,
    assets_manifest: dict,
    text_root: Path,
    visual_profile: str,
    layout_path: Path | None,
) -> dict:
    manifest = build_source_manifest(
        book_root,
        book_map,
        ledger,
        assets_manifest,
        text_root,
        visual_profile,
        layout_path,
    )
    revised_outputs = revision_chapter_output_records(revision_ledger)
    edition = revision_ledger.get("edition")
    document_titles = edition.get("document_titles") if isinstance(edition, dict) else []
    titles = {
        entry["id"]: entry["title"]
        for entry in document_titles
        if isinstance(entry, dict)
        and isinstance(entry.get("id"), str)
        and isinstance(entry.get("title"), str)
        and entry["title"].strip()
    } if isinstance(document_titles, list) else {}
    for document in manifest["documents"]:
        if document.get("kind") == "source_cover":
            continue
        output = revised_outputs.get(document["id"])
        if not isinstance(output, dict):
            raise RuntimeError(f"Missing validated revised output for {document['id']!r}")
        document["revised_file"] = f"text/{output['revised_file']}"
        document["revised_sha256"] = output["revised_sha256"]
        if document["id"] in titles:
            document["title"] = titles[document["id"]]
    revised_book = edition.get("book") if isinstance(edition, dict) else None
    if isinstance(revised_book, dict):
        for field in ("title", "subtitle"):
            value = revised_book.get(field)
            if isinstance(value, str) and value.strip():
                manifest["book"][field] = value
    manifest["text_edition"] = "revised-pt-br"
    manifest["revision_ledger_sha256"] = sha256_file(
        book_root / "metadata" / "revision-ledger.json"
    )
    manifest["language"] = REVISION_LANGUAGE
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Build an Audiobook Codex EPUB manifest from verified source text.")
    parser.add_argument("--book-map", required=True, type=Path)
    parser.add_argument("--ledger", required=True, type=Path)
    parser.add_argument("--assets-manifest", required=True, type=Path)
    parser.add_argument("--text-root", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--translation-ledger", type=Path)
    parser.add_argument("--revision-ledger", type=Path)
    parser.add_argument(
        "--text-edition",
        choices=("original", "revised-pt-br", "translated-pt-br"),
        default="original",
    )
    parser.add_argument("--layout", choices=("semantic", "legacy"))
    parser.add_argument("--epub-layout", type=Path)
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
        if args.text_edition == "translated-pt-br":
            translation_path = (
                args.translation_ledger.expanduser().resolve()
                if args.translation_ledger
                else book_root / "metadata" / "translation-ledger.json"
            )
            canonical_paths["translation ledger"] = (
                translation_path,
                book_root / "metadata" / "translation-ledger.json",
            )
        else:
            translation_path = None
        if args.text_edition == "revised-pt-br":
            revision_path = (
                args.revision_ledger.expanduser().resolve()
                if args.revision_ledger
                else book_root / "metadata" / "revision-ledger.json"
            )
            canonical_paths["revision ledger"] = (
                revision_path,
                book_root / "metadata" / "revision-ledger.json",
            )
        else:
            revision_path = None
        layout_mode = args.layout or ("legacy" if args.text_edition == "translated-pt-br" else "semantic")
        if args.text_edition == "translated-pt-br":
            if args.epub_layout is not None:
                raise RuntimeError("translated-pt-br EPUB manifests do not support an original semantic EPUB layout")
            layout_path = None
        elif layout_mode == "semantic":
            layout_path = (
                args.epub_layout.expanduser().resolve()
                if args.epub_layout
                else book_root / "metadata" / "epub-layout.json"
            )
            canonical_paths["EPUB layout"] = (
                layout_path,
                book_root / "metadata" / "epub-layout.json",
            )
        else:
            if args.epub_layout is not None:
                raise RuntimeError("--epub-layout requires --layout semantic")
            layout_path = None
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
        translation_ledger = None
        revision_ledger = None
        if translation_path is not None:
            translation_ledger = load_assets_json(translation_path)
            if not isinstance(translation_ledger, dict):
                raise RuntimeError("Translation ledger must be a JSON object.")
            errors += verify_translation_ledger(
                book_map,
                sha256_file(map_path),
                ledger,
                sha256_file(ledger_path),
                translation_ledger,
                text_root,
            )
        if revision_path is not None:
            revision_ledger = load_assets_json(revision_path)
            if not isinstance(revision_ledger, dict):
                raise RuntimeError("Revision ledger must be a JSON object.")
            errors += verify_revision_ledger(
                book_map,
                sha256_file(map_path),
                ledger,
                sha256_file(ledger_path),
                revision_ledger,
                text_root,
            )
        errors += validate_assets_manifest(assets_manifest, book_root, book_map, True)
        if errors:
            raise RuntimeError("; ".join(errors))
        output = (
            args.output.expanduser().resolve()
            if args.output
            else book_root
            / "metadata"
            / (
                "epub-manifest.pt-br.json"
                if args.text_edition == "translated-pt-br"
                else (
                    "epub-manifest.revised.json"
                    if args.text_edition == "revised-pt-br"
                    else "epub-manifest.json"
                )
            )
        )
        write_json(
            output,
            (
                build_translated_manifest(
                    book_root,
                    book_map,
                    ledger,
                    translation_ledger,
                    assets_manifest,
                    text_root,
                    args.visual_profile,
                )
                if translation_ledger is not None
                else (
                    build_revised_manifest(
                        book_root,
                        book_map,
                        ledger,
                        revision_ledger,
                        assets_manifest,
                        text_root,
                        args.visual_profile,
                        layout_path,
                    )
                    if revision_ledger is not None
                    else build_source_manifest(
                        book_root,
                        book_map,
                        ledger,
                        assets_manifest,
                        text_root,
                        args.visual_profile,
                        layout_path,
                    )
                )
            ),
        )
    except RuntimeError as error:
        print(f"Cannot build EPUB manifest: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Created {output}")


if __name__ == "__main__":
    main()
