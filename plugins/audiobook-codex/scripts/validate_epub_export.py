from __future__ import annotations

import argparse
import posixpath
from pathlib import Path, PurePosixPath
import sys
import xml.etree.ElementTree as ET
import json
import zipfile

from book_layout import resolve_book_paths
from epub_presentation import (
    COVER_DOCUMENT_PATH,
    COVER_IMAGE_PATH,
    FONT_FAMILY,
    PAGE_BACKGROUND,
    normalize_visual_profile,
    profile_resources,
    sha256_bytes,
)
from export_epub import (
    IMAGE_EDITIONS,
    TEXT_EDITIONS,
    _layout_text_values,
    join_semantic_values,
    load_export_context,
    paragraphs_from_text,
    published_documents,
    reader_documents,
    safe_segment,
    semantic_block_groups,
    sha256_file,
    validate_documents,
)


class EpubArchiveCache:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._archive = zipfile.ZipFile(path)
        self._entries: list[zipfile.ZipInfo] | None = None
        self._names: set[str] | None = None
        self._bytes: dict[str, bytes] = {}
        self._xml: dict[str, ET.Element] = {}

    def close(self) -> None:
        self._archive.close()

    def __enter__(self) -> "EpubArchiveCache":
        return self

    def __exit__(self, _exc_type: object, _exc: object, _traceback: object) -> None:
        self.close()

    def infolist(self) -> list[zipfile.ZipInfo]:
        if self._entries is None:
            self._entries = self._archive.infolist()
        return self._entries

    def namelist(self) -> list[str]:
        return [entry.filename for entry in self.infolist()]

    def has_entry(self, name: str) -> bool:
        if self._names is None:
            self._names = set(self.namelist())
        return name in self._names

    def read(self, name: str) -> bytes:
        if name not in self._bytes:
            self._bytes[name] = self._archive.read(name)
        return self._bytes[name]

    def read_xml(self, name: str) -> ET.Element:
        if name not in self._xml:
            self._xml[name] = ET.fromstring(self.read(name))
        return self._xml[name]


def normalized_archive_path(base: str, href: str) -> str | None:
    candidate = posixpath.normpath(posixpath.join(base, href))
    path = PurePosixPath(candidate)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def element_text(element: ET.Element) -> str:
    parts = [element.text or ""]
    for child in element:
        if local_name(child.tag) == "br":
            parts.append(" ")
        else:
            parts.append(element_text(child))
        parts.append(child.tail or "")
    return "".join(parts)


def normalized_text(value: str) -> str:
    return " ".join(value.split())


def validate_epub_archive(
    path: Path,
    visual_profile: dict | None,
    expected_language: str | None,
    semantic_layout: bool,
    expected_spine_ids: list[str],
    archive: EpubArchiveCache | None = None,
) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"EPUB is missing: {path}"]
    if archive is None:
        try:
            with EpubArchiveCache(path) as opened_archive:
                return validate_epub_archive(
                    path,
                    visual_profile,
                    expected_language,
                    semantic_layout,
                    expected_spine_ids,
                    opened_archive,
                )
        except zipfile.BadZipFile as error:
            return [f"EPUB is not a ZIP archive: {error}"]
    try:
        entries = archive.infolist()
        if not entries:
            return ["EPUB archive is empty"]
        if entries[0].filename != "mimetype":
            errors.append("EPUB mimetype must be the first archive entry")
        elif entries[0].compress_type != zipfile.ZIP_STORED:
            errors.append("EPUB mimetype must be stored without compression")
        try:
            if archive.read("mimetype") != b"application/epub+zip":
                errors.append("EPUB mimetype is invalid")
            container = archive.read_xml("META-INF/container.xml")
            rootfile = next(element for element in container.iter() if local_name(element.tag) == "rootfile")
            opf_path = rootfile.attrib.get("full-path", "")
            if not opf_path or not archive.has_entry(opf_path):
                errors.append("EPUB rootfile is missing")
                return errors
            package = archive.read_xml(opf_path)
        except (KeyError, StopIteration, ET.ParseError) as error:
            return errors + [f"EPUB package metadata is invalid: {error}"]
        if expected_language is not None:
            languages = [
                (element.text or "").strip()
                for element in package.iter()
                if local_name(element.tag) == "language"
            ]
            if languages != [expected_language]:
                errors.append("EPUB package language does not match the requested text edition")

        opf_parent = PurePosixPath(opf_path).parent.as_posix()
        manifest_by_id: dict[str, tuple[str, ET.Element]] = {}
        nav_found = False
        for item in package.iter():
            if local_name(item.tag) != "item":
                continue
            item_id = item.attrib.get("id", "")
            href = item.attrib.get("href", "")
            archive_path = normalized_archive_path(opf_parent, href)
            if not item_id or archive_path is None:
                errors.append("EPUB manifest has an invalid item")
                continue
            if item_id in manifest_by_id:
                errors.append(f"EPUB manifest contains duplicate id: {item_id}")
                continue
            if not archive.has_entry(archive_path):
                errors.append(f"EPUB manifest path is missing: {archive_path}")
            manifest_by_id[item_id] = (archive_path, item)
            if "nav" in item.attrib.get("properties", "").split():
                nav_found = True
                try:
                    archive.read_xml(archive_path)
                except (KeyError, ET.ParseError):
                    errors.append(f"EPUB nav document is invalid: {archive_path}")
        if not nav_found:
            errors.append("EPUB manifest is missing a nav document")

        spine_ids: list[str] = []
        semantic_documents = 0
        for itemref in package.iter():
            if local_name(itemref.tag) != "itemref":
                continue
            item_id = itemref.attrib.get("idref", "")
            spine_ids.append(item_id)
            manifest_entry = manifest_by_id.get(item_id)
            if manifest_entry is None:
                errors.append(f"EPUB spine references unknown manifest item: {item_id}")
                continue
            archive_path, item = manifest_entry
            if item.attrib.get("media-type") == "application/xhtml+xml":
                try:
                    root = archive.read_xml(archive_path)
                    if semantic_layout and item_id != "cover-page":
                        sections = [
                            element
                            for element in root.iter()
                            if local_name(element.tag) == "section"
                            and "semantic-layout" in element.attrib.get("class", "").split()
                        ]
                        if sections:
                            semantic_documents += 1
                except (KeyError, ET.ParseError):
                    errors.append(f"EPUB spine XHTML is invalid: {archive_path}")
        if spine_ids != expected_spine_ids:
            errors.append("EPUB spine does not match the validated document order")
        if visual_profile:
            cover_items = [
                (item_id, archive_path, item)
                for item_id, (archive_path, item) in manifest_by_id.items()
                if "cover-image" in item.attrib.get("properties", "").split()
            ]
            if len(cover_items) != 1:
                errors.append("antique-paper EPUB must contain exactly one cover-image")
            else:
                item_id, archive_path, item = cover_items[0]
                if item_id != "editorial-cover":
                    errors.append("antique-paper cover-image must be editorial-cover")
                if archive_path != f"OEBPS/{COVER_IMAGE_PATH}":
                    errors.append("antique-paper cover image path is invalid")
                if item.attrib.get("media-type") != "image/jpeg":
                    errors.append("antique-paper cover image media type is invalid")
                elif archive.has_entry(archive_path) and not archive.read(archive_path):
                    errors.append("antique-paper cover image is empty")

            cover_page = manifest_by_id.get("cover-page")
            if cover_page is None:
                errors.append("antique-paper EPUB is missing the cover page document")
            else:
                archive_path, _ = cover_page
                if archive_path != f"OEBPS/{COVER_DOCUMENT_PATH}":
                    errors.append("antique-paper cover page path is invalid")
                if not spine_ids or spine_ids[0] != "cover-page":
                    errors.append("antique-paper cover page must be first in the spine")
                try:
                    cover_root = archive.read_xml(archive_path)
                    image_sources = [
                        element.attrib.get("src")
                        for element in cover_root.iter()
                        if local_name(element.tag) == "img"
                    ]
                    if "../" + COVER_IMAGE_PATH not in image_sources:
                        errors.append("antique-paper cover page does not reference its cover image")
                except (KeyError, ET.ParseError):
                    errors.append("antique-paper cover page is invalid")

            stylesheet_path = "OEBPS/styles/book.css"
            try:
                stylesheet = archive.read(stylesheet_path).decode("utf-8")
            except (KeyError, UnicodeDecodeError):
                errors.append("antique-paper stylesheet is missing or invalid")
                stylesheet = ""
            for marker in (
                f"--page-background: {PAGE_BACKGROUND};",
                f'font-family: "{FONT_FAMILY}";',
                '../fonts/IMFeENrm28P.ttf',
                '../fonts/IMFeENit28P.ttf',
            ):
                if marker not in stylesheet:
                    errors.append(f"antique-paper stylesheet is missing {marker}")
            if semantic_layout:
                for marker in (
                    "--text-primary: #000000;",
                    ".semantic-layout .dialogue",
                    ".verse {",
                    "max-width: 32rem;",
                ):
                    if marker not in stylesheet:
                        errors.append(f"semantic EPUB stylesheet is missing {marker}")
                if semantic_documents == 0:
                    errors.append("semantic EPUB contains no semantic-layout document")

            for resource in profile_resources(visual_profile):
                manifest_entry = manifest_by_id.get(resource.identifier)
                if manifest_entry is None:
                    errors.append(f"antique-paper EPUB is missing resource: {resource.identifier}")
                    continue
                archive_path, item = manifest_entry
                if archive_path != f"OEBPS/{resource.epub_path}":
                    errors.append(f"antique-paper resource path is invalid: {resource.identifier}")
                if item.attrib.get("media-type") != resource.media_type:
                    errors.append(f"antique-paper resource media type is invalid: {resource.identifier}")
                if archive.has_entry(archive_path):
                    if sha256_bytes(archive.read(archive_path)) != resource.sha256:
                        errors.append(f"antique-paper resource hash does not match: {resource.identifier}")
    except zipfile.BadZipFile as error:
        return [f"EPUB is not a ZIP archive: {error}"]
    return errors


def validate_epub_document_texts(
    epub_path: Path,
    book_root: Path,
    documents: list[dict],
    text_edition: str | EpubArchiveCache = "original",
    archive: EpubArchiveCache | None = None,
) -> list[str]:
    # Preserve the former fourth-positional ``archive`` call shape for direct
    # validation users while allowing the requested text edition to filter fluid
    # supplementary documents.
    if not isinstance(text_edition, str):
        if archive is not None:
            raise RuntimeError("EPUB archive was supplied twice")
        archive = text_edition
        text_edition = "original"
    if not epub_path.is_file():
        return []
    if archive is None:
        try:
            with EpubArchiveCache(epub_path) as opened_archive:
                return validate_epub_document_texts(
                    epub_path,
                    book_root,
                    documents,
                    text_edition,
                    opened_archive,
                )
        except zipfile.BadZipFile as error:
            return [f"EPUB is not a ZIP archive: {error}"]
    errors: list[str] = []
    try:
        for index, document in enumerate(
            reader_documents(documents, text_edition, book_root),
            start=1,
        ):
            if document.get("kind") == "source_cover":
                continue
            archive_path = (
                "OEBPS/text/"
                f"{index:03d}-{safe_segment(str(document['id']), f'document-{index:03d}')}.xhtml"
            )
            try:
                root = archive.read_xml(archive_path)
            except (KeyError, ET.ParseError) as error:
                errors.append(f"EPUB document is unreadable: {archive_path}: {error}")
                continue
            section = next(
                (element for element in root.iter() if local_name(element.tag) == "section"),
                None,
            )
            if section is None:
                errors.append(f"EPUB document is missing its semantic section: {archive_path}")
                continue
            layout_blocks = document.get("_layout_blocks")
            if isinstance(layout_blocks, list):
                revision_changes = document.get("_revision_changes")
                if not isinstance(revision_changes, list):
                    revision_changes = []
                expected = normalized_text(
                    " ".join(
                        join_semantic_values(
                            [
                                value
                                for block in block_group
                                for value in _layout_text_values(
                                    block,
                                    book_root,
                                    revision_changes,
                                )
                            ]
                        )
                        for _block_index, block_group in semantic_block_groups(
                            [
                                block
                                for block in layout_blocks
                                if isinstance(block, dict)
                            ]
                        )
                    )
                )
            else:
                text_path = document.get("_text_path")
                if not isinstance(text_path, Path) or not text_path.is_file():
                    errors.append(f"EPUB document has no validated text input: {archive_path}")
                    continue
                heading, paragraphs = paragraphs_from_text(
                    text_path.read_text(encoding="utf-8"),
                    str(document["title"]),
                    allow_leading_chapter_label=document.get("kind") == "chapter",
                )
                expected = normalized_text(" ".join([heading, *paragraphs]))
            actual = normalized_text(
                " ".join([section.text or "", *(element_text(child) for child in section)])
            )
            if actual != expected:
                errors.append(
                    f"EPUB document text does not match its validated input: {archive_path}"
                )
    except zipfile.BadZipFile as error:
        return [f"EPUB is not a ZIP archive: {error}"]
    return errors


def validate_export_inputs(
    book_root: Path,
    epub_manifest_path: Path,
    assets_manifest_path: Path,
    image_edition: str,
    text_edition: str,
) -> tuple[list[str], dict | None, dict | None, list[dict] | None]:
    try:
        (
            _,
            ledger,
            assets_manifest,
            epub_manifest,
            _,
            _,
            translation_ledger,
            revision_ledger,
            _fluid_style,
            fluid_ledger,
            layout,
        ) = load_export_context(
            book_root,
            epub_manifest_path,
            assets_manifest_path,
            text_edition,
        )
        visual_profile = normalize_visual_profile(epub_manifest.get("visual_profile"))
        documents, asset_by_id = validate_documents(
            book_root,
            epub_manifest,
            assets_manifest,
            ledger,
            text_edition,
            translation_ledger,
            revision_ledger,
            fluid_ledger,
            layout,
        )
        if image_edition == "approved-restored":
            for document in documents:
                for asset_id in document["asset_ids"]:
                    asset = asset_by_id[asset_id]
                    restoration = asset.get("restoration") if isinstance(asset.get("restoration"), dict) else {}
                    if restoration.get("status") != "approved":
                        return (
                            [f"Asset {asset_id} is not approved for restored EPUB export"],
                            visual_profile,
                            epub_manifest,
                            documents,
                        )
    except RuntimeError as error:
        return [str(error)], None, None, None
    return [], visual_profile, epub_manifest, documents


def validate_export_sidecar(
    book_root: Path,
    epub_path: Path,
    image_edition: str,
    text_edition: str,
    epub_manifest: dict,
    visual_profile: dict | None,
    archive: EpubArchiveCache | None = None,
) -> list[str]:
    sidecar = epub_path.with_suffix(".epub.json")
    if not sidecar.is_file():
        return [f"EPUB export sidecar is missing: {sidecar}"]
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return [f"EPUB export sidecar is invalid: {error}"]
    if not isinstance(data, dict):
        return ["EPUB export sidecar must be a JSON object"]
    errors: list[str] = []
    try:
        expected_path = epub_path.resolve().relative_to(book_root.resolve()).as_posix()
    except ValueError:
        expected_path = ""
    if data.get("epub_path") != expected_path:
        errors.append("EPUB export sidecar path does not match EPUB")
    if data.get("epub_sha256") != sha256_file(epub_path):
        errors.append("EPUB export sidecar SHA-256 does not match EPUB")
    if data.get("image_edition") != image_edition:
        errors.append("EPUB export sidecar image edition does not match requested edition")
    if data.get("text_edition", "original") != text_edition:
        errors.append("EPUB export sidecar text edition does not match requested edition")
    if (
        data.get("language") is not None
        and data.get("language") != epub_manifest.get("language")
    ):
        errors.append("EPUB export sidecar language does not match EPUB manifest")
    if text_edition == "translated-pt-br":
        if data.get("language") != epub_manifest.get("language"):
            errors.append("translated EPUB export sidecar language is missing or invalid")
        if data.get("source_language") != epub_manifest.get("source_language"):
            errors.append("EPUB export sidecar source language does not match translated manifest")
        if data.get("translation_ledger_sha256") != epub_manifest.get("translation_ledger_sha256"):
            errors.append("EPUB export sidecar translation ledger hash does not match manifest")
    elif text_edition == "revised-pt-br":
        if data.get("revision_ledger_sha256") != epub_manifest.get(
            "revision_ledger_sha256"
        ):
            errors.append("EPUB export sidecar revision ledger hash does not match manifest")
    elif text_edition == "fluid-pt-br":
        for key in (
            "base_edition",
            "base_ledger_sha256",
            "fluid_style_sha256",
            "fluid_edition_ledger_sha256",
            "profile",
        ):
            if data.get(key) != epub_manifest.get(key):
                errors.append(
                    f"EPUB export sidecar {key} does not match fluid manifest"
                )
        if epub_manifest.get("base_edition") == "translated-pt-br":
            if data.get("source_language") != epub_manifest.get("source_language"):
                errors.append(
                    "EPUB export sidecar source language does not match fluid manifest"
                )
            if data.get("translation_ledger_sha256") != epub_manifest.get(
                "translation_ledger_sha256"
            ):
                errors.append(
                    "EPUB export sidecar translation ledger hash does not match "
                    "the fluid base"
                )
    if epub_manifest.get("layout") is not None:
        if data.get("layout") != epub_manifest.get("layout"):
            errors.append("EPUB export sidecar layout does not match manifest")
    elif data.get("layout") is not None:
        errors.append("EPUB export sidecar layout exists without manifest layout")
    if not isinstance(data.get("assets"), list):
        errors.append("EPUB export sidecar assets must be an array")
    if visual_profile:
        presentation = data.get("visual_profile")
        if not isinstance(presentation, dict):
            return errors + ["EPUB export sidecar is missing antique-paper visual_profile"]
        if presentation.get("name") != visual_profile["name"]:
            errors.append("EPUB export sidecar visual profile does not match export")
        cover = presentation.get("cover")
        if not isinstance(cover, dict):
            errors.append("EPUB export sidecar visual cover must be an object")
        resources = presentation.get("resources")
        if not isinstance(resources, list):
            errors.append("EPUB export sidecar visual resources must be an array")
        close_archive = False
        try:
            if archive is None:
                archive = EpubArchiveCache(epub_path)
                close_archive = True
            if isinstance(cover, dict):
                cover_path = cover.get("epub_path")
                if cover_path != f"OEBPS/{COVER_IMAGE_PATH}":
                    errors.append("EPUB export sidecar visual cover path is invalid")
                elif not archive.has_entry(cover_path):
                    errors.append("EPUB export sidecar visual cover is missing from EPUB")
                elif cover.get("sha256") != sha256_bytes(archive.read(cover_path)):
                    errors.append("EPUB export sidecar visual cover hash does not match EPUB")
            entries_by_id = {
                entry.get("id"): entry
                for entry in resources
                if isinstance(entry, dict) and isinstance(entry.get("id"), str)
            } if isinstance(resources, list) else {}
            for resource in profile_resources(visual_profile):
                entry = entries_by_id.get(resource.identifier)
                if not isinstance(entry, dict):
                    errors.append(f"EPUB export sidecar is missing resource: {resource.identifier}")
                    continue
                expected_path = f"OEBPS/{resource.epub_path}"
                if entry.get("epub_path") != expected_path:
                    errors.append(f"EPUB export sidecar resource path is invalid: {resource.identifier}")
                if entry.get("media_type") != resource.media_type:
                    errors.append(f"EPUB export sidecar resource media type is invalid: {resource.identifier}")
                if entry.get("sha256") != resource.sha256:
                    errors.append(f"EPUB export sidecar resource hash is invalid: {resource.identifier}")
        except zipfile.BadZipFile as error:
            errors.append(f"EPUB export sidecar cannot read EPUB: {error}")
        finally:
            if close_archive and archive is not None:
                archive.close()
    elif data.get("visual_profile") is not None:
        errors.append("EPUB export sidecar visual profile exists without manifest visual_profile")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an Audiobook Codex EPUB export.")
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--epub", required=True, type=Path)
    parser.add_argument("--epub-manifest", type=Path)
    parser.add_argument("--assets-manifest", type=Path)
    parser.add_argument("--image-edition", choices=sorted(IMAGE_EDITIONS), default="original")
    parser.add_argument("--text-edition", choices=sorted(TEXT_EDITIONS), default="original")
    args = parser.parse_args()

    book_root = resolve_book_paths(args.book_root).assembly_root
    epub_manifest = (
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
    assets_manifest = (
        args.assets_manifest.expanduser().resolve()
        if args.assets_manifest
        else book_root / "metadata" / "assets-manifest.json"
    )
    errors, visual_profile, manifest_data, documents = validate_export_inputs(
        book_root,
        epub_manifest,
        assets_manifest,
        args.image_edition,
        args.text_edition,
    )
    epub_path = args.epub.expanduser().resolve()
    expected_language = (
        str(manifest_data.get("language"))
        if isinstance(manifest_data, dict) and isinstance(manifest_data.get("language"), str)
        else None
    )
    semantic_layout = isinstance(manifest_data, dict) and isinstance(manifest_data.get("layout"), dict)
    expected_spine_ids = ["cover-page"] if visual_profile else []
    if isinstance(documents, list):
        expected_spine_ids.extend(
            f"doc-{index}"
            for index in range(
                1,
                len(published_documents(documents, args.text_edition)) + 1,
            )
        )
    if epub_path.is_file():
        try:
            with EpubArchiveCache(epub_path) as archive:
                errors += validate_epub_archive(
                    epub_path,
                    visual_profile,
                    expected_language,
                    semantic_layout,
                    expected_spine_ids,
                    archive,
                )
                if isinstance(documents, list):
                    errors += validate_epub_document_texts(
                        epub_path,
                        book_root,
                        documents,
                        args.text_edition,
                        archive,
                    )
                if isinstance(manifest_data, dict):
                    errors += validate_export_sidecar(
                        book_root,
                        epub_path,
                        args.image_edition,
                        args.text_edition,
                        manifest_data,
                        visual_profile,
                        archive,
                    )
        except zipfile.BadZipFile as error:
            errors.append(f"EPUB is not a ZIP archive: {error}")
    else:
        errors += validate_epub_archive(
            epub_path,
            visual_profile,
            expected_language,
            semantic_layout,
            expected_spine_ids,
        )
    if errors:
        print("INVALID EPUB export:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"VALID EPUB: {epub_path}")
    print(f"SHA-256: {sha256_file(epub_path)}")


if __name__ == "__main__":
    main()
