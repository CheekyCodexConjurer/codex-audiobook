from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

from book_layout import resolve_book_paths
from epub_presentation import normalize_visual_profile
from export_epub import (
    IMAGE_EDITIONS,
    TEXT_EDITIONS,
    _comparison_text,
    _layout_text_values,
    is_fluid_supplementary_document,
    is_fluid_supplementary_title,
    load_export_context,
    normalize_space,
    paragraphs_from_text,
    selected_asset,
    sha256_file,
    validate_documents,
)


_URL_FRAGMENT = re.compile(r"^(?:https?://|www\.)\S+$", re.IGNORECASE)


class PdfValidationContext:
    def __init__(self, reader: object) -> None:
        self.reader = reader
        self._page_text: dict[int, str] = {}
        self._page_text_without_numbers: dict[int, str] = {}

    @property
    def page_count(self) -> int:
        return len(self.reader.pages)

    def page_text(self, page_index: int) -> str:
        if page_index not in self._page_text:
            self._page_text[page_index] = (
                self.reader.pages[page_index].extract_text() or ""
            )
        return self._page_text[page_index]

    def page_text_without_number(self, page_index: int) -> str:
        if page_index not in self._page_text_without_numbers:
            self._page_text_without_numbers[page_index] = _without_pdf_page_number(
                self.page_text(page_index),
                page_index + 1,
            )
        return self._page_text_without_numbers[page_index]

    def extracted_text(self) -> str:
        return "\n".join(
            self.page_text_without_number(page_index)
            for page_index in range(self.page_count)
        )


def _find_fragment_ignoring_whitespace(
    value: str,
    fragment: str,
    start: int,
) -> tuple[int, int]:
    pattern = r"\s*".join(re.escape(character) for character in fragment)
    match = re.search(pattern, value[start:])
    if match is None:
        return -1, -1
    return start + match.start(), start + match.end()


def _without_pdf_page_number(value: str, page_number: int) -> str:
    lines = value.splitlines()
    target = str(page_number)
    nonempty_indexes = [
        index for index, line in enumerate(lines) if line.strip()
    ]
    for index in reversed(nonempty_indexes[-1:]):
        if lines[index].strip() == target:
            del lines[index]
            return "\n".join(lines)
    for index in nonempty_indexes[:1]:
        if lines[index].strip() == target:
            del lines[index]
            break
    return "\n".join(lines)


def _expected_fragments(
    book_root: Path,
    documents: list[dict],
    text_edition: str,
) -> tuple[list[str], list[str]]:
    ordered_fragments: list[str] = []
    note_fragments: list[str] = []
    for document in documents:
        if document.get("kind") == "source_cover":
            continue
        if (
            text_edition == "fluid-pt-br"
            and is_fluid_supplementary_document(document)
        ):
            continue
        blocks = document.get("_layout_blocks")
        if not isinstance(blocks, list):
            text = document["_text_path"].read_text(encoding="utf-8")
            heading, paragraphs = paragraphs_from_text(
                text,
                str(document["title"]),
                allow_leading_chapter_label=document.get("kind") == "chapter",
            )
            ordered_fragments.extend([heading, *paragraphs])
            continue
        changes = document.get("_revision_changes") or []
        applied_revision_ids: set[str] = set()
        for block in blocks:
            values = _layout_text_values(
                block,
                book_root,
                changes,
                applied_revision_ids,
            )
            if (
                text_edition == "fluid-pt-br"
                and block.get("kind") == "heading"
                and any(is_fluid_supplementary_title(value) for value in values)
            ):
                break
            target = (
                note_fragments
                if block.get("kind") == "note"
                else ordered_fragments
            )
            target.extend(values)
        expected_revision_ids = {
            str(change.get("id"))
            for change in changes
            if isinstance(change, dict) and isinstance(change.get("id"), str)
        }
        if applied_revision_ids != expected_revision_ids:
            missing = sorted(expected_revision_ids - applied_revision_ids)
            raise RuntimeError(
                "Approved revision changes are not represented by the semantic PDF layout: "
                f"{missing}"
            )
    return (
        [
            normalize_space(fragment)
            for fragment in ordered_fragments
            if normalize_space(fragment)
        ],
        [
            normalize_space(fragment)
            for fragment in note_fragments
            if normalize_space(fragment)
        ],
    )


def validate_pdf_text(
    pdf_path: Path,
    ordered_fragments: list[str],
    note_fragments: list[str],
    context: PdfValidationContext | None = None,
) -> list[str]:
    if context is None:
        try:
            from pypdf import PdfReader
        except ImportError as error:
            return [
                "pypdf is required for PDF validation. Run this script with the Codex bundled Python."
            ]
        try:
            context = PdfValidationContext(PdfReader(str(pdf_path)))
        except Exception as error:
            return [f"Cannot extract PDF text: {error}"]
    try:
        extracted = context.extracted_text()
    except Exception as error:
        return [f"Cannot extract PDF text: {error}"]
    normalized = normalize_space(extracted)
    errors: list[str] = []
    for fragment in note_fragments:
        position = normalized.find(fragment)
        if position < 0:
            errors.append(
                "PDF text does not preserve a validated semantic note: "
                f"{fragment[:120]}"
            )
            if len(errors) >= 20:
                return errors
            continue
        normalized = normalize_space(
            f"{normalized[:position]} {normalized[position + len(fragment):]}"
        )
    cursor = 0
    for fragment in ordered_fragments:
        fragment_end = -1
        if _URL_FRAGMENT.fullmatch(fragment):
            position, fragment_end = _find_fragment_ignoring_whitespace(
                normalized,
                fragment,
                cursor,
            )
        elif len(fragment) < 40:
            position = normalized.find(fragment)
        else:
            position = normalized.find(fragment, cursor)
        if position < 0:
            errors.append(
                "PDF text does not preserve a validated semantic fragment: "
                f"{fragment[:120]}"
            )
            if len(errors) >= 20:
                break
            continue
        if fragment_end >= 0:
            cursor = fragment_end
        elif len(fragment) >= 40:
            cursor = position + len(fragment)
    return errors


def expected_outline_titles(
    _book_root: Path,
    documents: list[dict],
    text_edition: str,
) -> list[str]:
    return [
        normalize_space(str(document["title"]))
        for document in documents
        if document.get("kind") != "source_cover"
        and (
            text_edition != "fluid-pt-br"
            or not is_fluid_supplementary_document(document)
        )
    ]


def validate_outline(reader: object, expected_titles: list[str]) -> list[str]:
    actual_titles = [
        normalize_space(str(getattr(item, "title", "")))
        for item in reader.outline
        if not isinstance(item, list)
    ]
    if actual_titles and actual_titles[0].casefold() == "Sumário".casefold():
        actual_titles = actual_titles[1:]
    if actual_titles != expected_titles:
        return ["PDF outline does not preserve the validated document order"]
    return []


def validate_legacy_heading_uniqueness(
    reader: object,
    documents: list[dict],
    context: PdfValidationContext | None = None,
) -> list[str]:
    outline_items = [
        item for item in reader.outline if not isinstance(item, list)
    ]
    if (
        outline_items
        and normalize_space(str(getattr(outline_items[0], "title", ""))).casefold()
        == "Sumário".casefold()
    ):
        outline_items = outline_items[1:]
    content_documents = [
        document for document in documents if document.get("kind") != "source_cover"
    ]
    if len(outline_items) != len(content_documents):
        return []

    errors: list[str] = []
    for document, outline_item in zip(content_documents, outline_items):
        if (
            document.get("kind") != "chapter"
            or isinstance(document.get("_layout_blocks"), list)
        ):
            continue
        title = normalize_space(str(document["title"]))
        heading, _paragraphs = paragraphs_from_text(
            document["_text_path"].read_text(encoding="utf-8"),
            title,
            allow_leading_chapter_label=True,
        )
        try:
            page_index = reader.get_destination_page_number(outline_item)
            page_text = (
                context.page_text(page_index)
                if context is not None
                else reader.pages[page_index].extract_text() or ""
            )
        except Exception as error:
            errors.append(
                f"Cannot inspect legacy PDF chapter heading {title!r}: {error}"
            )
            continue
        normalized_page = normalize_space(
            _without_pdf_page_number(page_text, page_index + 1)
        )
        if not _comparison_text(normalized_page).startswith(
            _comparison_text(heading)
        ):
            errors.append(
                "PDF legacy chapter heading does not match the content at its "
                f"outline destination: {title}"
            )
    return errors


def validate_sidecar(
    book_root: Path,
    pdf_path: Path,
    manifest: dict,
    image_edition: str,
    text_edition: str,
    page_count: int,
    expected_assets: list[dict],
) -> list[str]:
    sidecar_path = pdf_path.with_suffix(".pdf.json")
    if not sidecar_path.is_file():
        return [f"PDF export sidecar is missing: {sidecar_path}"]
    try:
        sidecar = json.loads(sidecar_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"PDF export sidecar is invalid: {error}"]
    if not isinstance(sidecar, dict):
        return ["PDF export sidecar must be a JSON object"]
    errors: list[str] = []
    expected_path = pdf_path.resolve().relative_to(book_root.resolve()).as_posix()
    checks = {
        "schema_version": "1.0",
        "pdf_path": expected_path,
        "pdf_sha256": sha256_file(pdf_path),
        "page_count": page_count,
        "image_edition": image_edition,
        "text_edition": text_edition,
        "language": manifest.get("language"),
        "book_map_sha256": manifest.get("book_map_sha256"),
        "text_ledger_sha256": manifest.get("text_ledger_sha256"),
        "assets_manifest_sha256": manifest.get("assets_manifest_sha256"),
    }
    for key, expected in checks.items():
        if sidecar.get(key) != expected:
            errors.append(f"PDF export sidecar {key} does not match the validated export")
    renderer = sidecar.get("renderer")
    if (
        not isinstance(renderer, dict)
        or renderer.get("name") != "reportlab"
        or not isinstance(renderer.get("version"), str)
    ):
        errors.append("PDF export sidecar renderer is invalid")
    if text_edition == "translated-pt-br":
        if sidecar.get("source_language") != manifest.get("source_language"):
            errors.append("PDF export sidecar source_language does not match manifest")
        if sidecar.get("translation_ledger_sha256") != manifest.get(
            "translation_ledger_sha256"
        ):
            errors.append(
                "PDF export sidecar translation ledger hash does not match manifest"
            )
    elif text_edition == "revised-pt-br":
        if sidecar.get("revision_ledger_sha256") != manifest.get(
            "revision_ledger_sha256"
        ):
            errors.append(
                "PDF export sidecar revision ledger hash does not match manifest"
            )
    elif text_edition == "fluid-pt-br":
        for key in (
            "base_edition",
            "base_ledger_sha256",
            "fluid_style_sha256",
            "fluid_edition_ledger_sha256",
            "profile",
        ):
            if sidecar.get(key) != manifest.get(key):
                errors.append(
                    f"PDF export sidecar {key} does not match fluid manifest"
                )
        if manifest.get("base_edition") == "translated-pt-br":
            if sidecar.get("source_language") != manifest.get("source_language"):
                errors.append(
                    "PDF export sidecar source_language does not match fluid manifest"
                )
            if sidecar.get("translation_ledger_sha256") != manifest.get(
                "translation_ledger_sha256"
            ):
                errors.append(
                    "PDF export sidecar translation ledger hash does not match "
                    "the fluid base"
                )
    if manifest.get("layout") is not None:
        if sidecar.get("layout") != manifest.get("layout"):
            errors.append("PDF export sidecar layout does not match manifest")
    visual_profile = normalize_visual_profile(manifest.get("visual_profile"))
    sidecar_profile = sidecar.get("visual_profile")
    if visual_profile is None:
        if sidecar_profile is not None:
            errors.append("PDF export sidecar visual profile exists without manifest profile")
    elif (
        not isinstance(sidecar_profile, dict)
        or sidecar_profile.get("name") != visual_profile.get("name")
        or not isinstance(sidecar_profile.get("cover"), dict)
        or sidecar_profile["cover"].get("format_label") != "PDF"
    ):
        errors.append("PDF export sidecar visual profile is invalid")
    if not isinstance(sidecar.get("assets"), list):
        errors.append("PDF export sidecar assets must be an array")
    elif sidecar.get("assets") != expected_assets:
        errors.append("PDF export sidecar assets do not match the selected renditions")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate an Audiobook Codex PDF export."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--pdf", required=True, type=Path)
    parser.add_argument("--epub-manifest", type=Path)
    parser.add_argument("--assets-manifest", type=Path)
    parser.add_argument(
        "--image-edition",
        choices=sorted(IMAGE_EDITIONS),
        default="original",
    )
    parser.add_argument(
        "--text-edition",
        choices=sorted(TEXT_EDITIONS),
        default="original",
    )
    args = parser.parse_args()

    try:
        from pypdf import PdfReader

        book_root = resolve_book_paths(args.book_root).assembly_root
        pdf_path = args.pdf.expanduser().resolve()
        if not pdf_path.is_file():
            raise RuntimeError(f"PDF export is missing: {pdf_path}")
        exports_root = (book_root / "exports" / "pdf").resolve()
        try:
            pdf_path.relative_to(exports_root)
        except ValueError as error:
            raise RuntimeError(
                f"PDF export must remain under {exports_root}: {pdf_path}"
            ) from error
        manifest_path = (
            args.epub_manifest.expanduser().resolve()
            if args.epub_manifest
            else book_root
            / "metadata"
            / (
                "epub-manifest.fluid.json"
                if args.text_edition == "fluid-pt-br"
                else (
                    "epub-manifest.pt-br.json"
                    if args.text_edition == "translated-pt-br"
                    else (
                        "epub-manifest.revised.json"
                        if args.text_edition == "revised-pt-br"
                        else "epub-manifest.json"
                    )
                )
            )
        )
        assets_manifest_path = (
            args.assets_manifest.expanduser().resolve()
            if args.assets_manifest
            else book_root / "metadata" / "assets-manifest.json"
        )
        (
            _book_map,
            ledger,
            assets_manifest,
            manifest,
            _map_path,
            _ledger_path,
            translation_ledger,
            revision_ledger,
            _fluid_style,
            fluid_ledger,
            layout,
        ) = load_export_context(
            book_root,
            manifest_path,
            assets_manifest_path,
            args.text_edition,
        )
        documents, asset_by_id = validate_documents(
            book_root,
            manifest,
            assets_manifest,
            ledger,
            args.text_edition,
            translation_ledger,
            revision_ledger,
            fluid_ledger,
            layout,
        )
        reader = PdfReader(str(pdf_path))
        pdf_context = PdfValidationContext(reader)
        page_count = pdf_context.page_count
        errors = []
        if page_count <= 0:
            errors.append("PDF export must contain at least one page")
        metadata = reader.metadata
        title = str((manifest.get("book") or {}).get("title") or "")
        if title and str(metadata.title or "") != title:
            errors.append("PDF metadata title does not match manifest")
        ordered_fragments, note_fragments = _expected_fragments(
            book_root,
            documents,
            args.text_edition,
        )
        errors += validate_pdf_text(pdf_path, ordered_fragments, note_fragments, pdf_context)
        errors += validate_outline(
            reader,
            expected_outline_titles(book_root, documents, args.text_edition),
        )
        errors += validate_legacy_heading_uniqueness(reader, documents, pdf_context)
        expected_assets: list[dict] = []
        seen_asset_ids: set[str] = set()
        for document in documents:
            for asset_id in document["asset_ids"]:
                if asset_id in seen_asset_ids:
                    continue
                selected = selected_asset(
                    asset_by_id[asset_id],
                    book_root,
                    args.image_edition,
                )
                expected_assets.append(
                    {
                        "id": selected["id"],
                        "sha256": selected["sha256"],
                        "media_type": selected["media_type"],
                    }
                )
                seen_asset_ids.add(asset_id)
        errors += validate_sidecar(
            book_root,
            pdf_path,
            manifest,
            args.image_edition,
            args.text_edition,
            page_count,
            expected_assets,
        )
    except (OSError, RuntimeError, ValueError) as error:
        print(f"INVALID PDF export: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if errors:
        print("INVALID PDF export:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"VALID PDF: {pdf_path}")
    print(f"SHA-256: {sha256_file(pdf_path)}")


if __name__ == "__main__":
    main()
