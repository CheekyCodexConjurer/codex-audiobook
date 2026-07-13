from __future__ import annotations

import argparse
import posixpath
from pathlib import Path, PurePosixPath
import sys
import xml.etree.ElementTree as ET
import json
import zipfile

from epub_presentation import (
    COVER_DOCUMENT_PATH,
    COVER_IMAGE_PATH,
    FONT_FAMILY,
    PAGE_BACKGROUND,
    normalize_visual_profile,
    profile_resources,
    sha256_bytes,
)
from export_epub import IMAGE_EDITIONS, load_export_context, sha256_file, validate_documents


def normalized_archive_path(base: str, href: str) -> str | None:
    candidate = posixpath.normpath(posixpath.join(base, href))
    path = PurePosixPath(candidate)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def validate_epub_archive(path: Path, visual_profile: dict | None) -> list[str]:
    errors: list[str] = []
    if not path.is_file():
        return [f"EPUB is missing: {path}"]
    try:
        with zipfile.ZipFile(path) as archive:
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
                container = ET.fromstring(archive.read("META-INF/container.xml"))
                rootfile = next(element for element in container.iter() if local_name(element.tag) == "rootfile")
                opf_path = rootfile.attrib.get("full-path", "")
                if not opf_path or opf_path not in archive.namelist():
                    errors.append("EPUB rootfile is missing")
                    return errors
                package = ET.fromstring(archive.read(opf_path))
            except (KeyError, StopIteration, ET.ParseError) as error:
                return errors + [f"EPUB package metadata is invalid: {error}"]

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
                if archive_path not in archive.namelist():
                    errors.append(f"EPUB manifest path is missing: {archive_path}")
                manifest_by_id[item_id] = (archive_path, item)
                if "nav" in item.attrib.get("properties", "").split():
                    nav_found = True
                    try:
                        ET.fromstring(archive.read(archive_path))
                    except (KeyError, ET.ParseError):
                        errors.append(f"EPUB nav document is invalid: {archive_path}")
            if not nav_found:
                errors.append("EPUB manifest is missing a nav document")

            spine_ids: list[str] = []
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
                        ET.fromstring(archive.read(archive_path))
                    except (KeyError, ET.ParseError):
                        errors.append(f"EPUB spine XHTML is invalid: {archive_path}")
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
                    elif archive_path in archive.namelist() and not archive.read(archive_path):
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
                        cover_root = ET.fromstring(archive.read(archive_path))
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
                    if archive_path in archive.namelist():
                        if sha256_bytes(archive.read(archive_path)) != resource.sha256:
                            errors.append(f"antique-paper resource hash does not match: {resource.identifier}")
    except zipfile.BadZipFile as error:
        return [f"EPUB is not a ZIP archive: {error}"]
    return errors


def validate_export_inputs(
    book_root: Path,
    epub_manifest_path: Path,
    assets_manifest_path: Path,
    image_edition: str,
) -> tuple[list[str], dict | None]:
    try:
        _, ledger, assets_manifest, epub_manifest, _, _ = load_export_context(
            book_root, epub_manifest_path, assets_manifest_path
        )
        visual_profile = normalize_visual_profile(epub_manifest.get("visual_profile"))
        documents, asset_by_id = validate_documents(book_root, epub_manifest, assets_manifest, ledger)
        if image_edition == "approved-restored":
            for document in documents:
                for asset_id in document["asset_ids"]:
                    asset = asset_by_id[asset_id]
                    restoration = asset.get("restoration") if isinstance(asset.get("restoration"), dict) else {}
                    if restoration.get("status") != "approved":
                        return [f"Asset {asset_id} is not approved for restored EPUB export"], visual_profile
    except RuntimeError as error:
        return [str(error)], None
    return [], visual_profile


def validate_export_sidecar(
    book_root: Path,
    epub_path: Path,
    image_edition: str,
    visual_profile: dict | None,
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
        try:
            with zipfile.ZipFile(epub_path) as archive:
                if isinstance(cover, dict):
                    cover_path = cover.get("epub_path")
                    if cover_path != f"OEBPS/{COVER_IMAGE_PATH}":
                        errors.append("EPUB export sidecar visual cover path is invalid")
                    elif cover_path not in archive.namelist():
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
    args = parser.parse_args()

    book_root = args.book_root.expanduser().resolve()
    epub_manifest = (
        args.epub_manifest.expanduser().resolve()
        if args.epub_manifest
        else book_root / "metadata" / "epub-manifest.json"
    )
    assets_manifest = (
        args.assets_manifest.expanduser().resolve()
        if args.assets_manifest
        else book_root / "metadata" / "assets-manifest.json"
    )
    errors, visual_profile = validate_export_inputs(
        book_root,
        epub_manifest,
        assets_manifest,
        args.image_edition,
    )
    epub_path = args.epub.expanduser().resolve()
    errors += validate_epub_archive(epub_path, visual_profile)
    if epub_path.is_file():
        errors += validate_export_sidecar(book_root, epub_path, args.image_edition, visual_profile)
    if errors:
        print("INVALID EPUB export:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"VALID EPUB: {epub_path}")
    print(f"SHA-256: {sha256_file(epub_path)}")


if __name__ == "__main__":
    main()
