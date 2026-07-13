from __future__ import annotations

import copy
import hashlib
from io import BytesIO
import json
import posixpath
from pathlib import Path, PurePosixPath
import re
import unicodedata
import xml.etree.ElementTree as ET
import zipfile


IMAGE_MEDIA_TYPES = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".jp2": "image/jp2",
    ".jpx": "image/jpx",
    ".png": "image/png",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}

PIL_FORMAT_EXTENSIONS = {
    "AVIF": ".avif",
    "BMP": ".bmp",
    "GIF": ".gif",
    "JPEG": ".jpg",
    "JPEG2000": ".jp2",
    "PNG": ".png",
    "TIFF": ".tiff",
    "WEBP": ".webp",
}


def require_pypdf() -> object:
    try:
        from pypdf import PdfReader
    except ImportError as error:
        raise RuntimeError(
            "pypdf is required for PDF asset inventory. Run this script with the Codex bundled Python."
        ) from error
    return PdfReader


def require_pillow() -> object:
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required for image asset inventory. Run this script with the Codex bundled Python."
        ) from error
    return Image


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def normalize_asset_segment(value: str, fallback: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", normalized).strip(".-")
    return normalized[:100] or fallback


def source_image_details(data: bytes, preferred_suffix: str = "") -> tuple[str, str, int | None, int | None]:
    suffix = preferred_suffix.lower()
    try:
        image_module = require_pillow()
        with image_module.open(BytesIO(data)) as image:
            format_name = str(image.format or "").upper()
            suffix = PIL_FORMAT_EXTENSIONS.get(format_name, suffix)
            media_type = image_module.MIME.get(format_name) or IMAGE_MEDIA_TYPES.get(suffix)
            if media_type:
                return suffix or ".img", media_type, image.width, image.height
    except Exception:
        pass
    return suffix or ".png", IMAGE_MEDIA_TYPES.get(suffix, "image/png"), None, None


def write_immutable_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_bytes() != data:
            raise RuntimeError(f"Refusing to replace an extracted original asset: {path}")
        return
    path.write_bytes(data)


def asset_defaults(
    asset_id: str,
    source: dict,
    original_path: Path,
    output_root: Path,
    data: bytes,
    preferred_suffix: str,
    declared_source_cover: bool = False,
) -> dict:
    suffix, media_type, width, height = source_image_details(data, preferred_suffix)
    if original_path.suffix.lower() != suffix:
        original_path = original_path.with_suffix(suffix)
    write_immutable_bytes(original_path, data)
    classification = {
        "content": "unknown",
        "text_pixels": "unknown",
        "restoration_eligibility": "review_required",
        "evidence": [],
    }
    epub = {
        "role": "unresolved",
        "placement": "unresolved",
        "document_id": None,
        "alt_text": "",
    }
    if declared_source_cover:
        classification = {
            "content": "cover",
            "text_pixels": "unknown",
            "restoration_eligibility": "review_required",
            "evidence": ["Original EPUB package declares this image as its cover."],
        }
        epub = {
            "role": "cover",
            "placement": "source_cover",
            "document_id": None,
            "alt_text": "",
        }
    return {
        "id": asset_id,
        "source": source,
        "original": {
            "path": original_path.relative_to(output_root).as_posix(),
            "sha256": hashlib.sha256(data).hexdigest(),
            "media_type": media_type,
            "width": width,
            "height": height,
        },
        "classification": classification,
        "epub": epub,
        "restoration": {
            "status": "not_requested",
            "approved": None,
        },
    }


def merge_existing_asset_details(existing: object, generated_assets: list[dict]) -> list[dict]:
    if not isinstance(existing, dict) or not isinstance(existing.get("assets"), list):
        return generated_assets
    previous_by_key: dict[tuple[str, str], dict] = {}
    for candidate in existing["assets"]:
        if not isinstance(candidate, dict):
            continue
        original = candidate.get("original")
        asset_id = candidate.get("id")
        if (
            isinstance(asset_id, str)
            and isinstance(original, dict)
            and isinstance(original.get("sha256"), str)
        ):
            previous_by_key[(asset_id, original["sha256"])] = candidate
    for asset in generated_assets:
        original = asset.get("original")
        asset_id = asset.get("id")
        previous = (
            previous_by_key.get((asset_id, original.get("sha256")))
            if isinstance(asset_id, str) and isinstance(original, dict)
            else None
        )
        if not isinstance(previous, dict):
            continue
        for key in ("classification", "epub", "restoration"):
            if isinstance(previous.get(key), dict):
                asset[key] = copy.deepcopy(previous[key])
    return generated_assets


def write_assets_manifest(output_root: Path, source_sha256: str, assets: list[dict]) -> Path:
    path = output_root / "metadata" / "assets-manifest.json"
    existing = load_json(path) if path.is_file() else None
    if isinstance(existing, dict) and existing.get("source_sha256") not in {None, source_sha256}:
        raise RuntimeError("Refusing to replace an assets manifest for a different source.")
    write_json(
        path,
        {
            "schema_version": "1.0",
            "source_sha256": source_sha256,
            "assets": merge_existing_asset_details(existing, assets),
        },
    )
    return path


def pdf_image_assets(source: Path, output_root: Path, pages: list[dict]) -> list[dict]:
    reader_type = require_pypdf()
    reader = reader_type(str(source))
    logical_pages_by_source: dict[int, list[int]] = {}
    for page in pages:
        source_page = page.get("source_page")
        logical_page = page.get("logical_page")
        if isinstance(source_page, int) and isinstance(logical_page, int):
            logical_pages_by_source.setdefault(source_page, []).append(logical_page)

    assets: list[dict] = []
    assets_root = output_root / "assets" / "images" / "original"
    for source_page, page in enumerate(reader.pages, start=1):
        for image_index, image in enumerate(page.images, start=1):
            data = bytes(image.data)
            raw_name = str(image.name or f"image-{image_index:02d}.png")
            suffix = Path(raw_name.replace("\\", "/")).suffix.lower()
            asset_id = f"pdf-page-{source_page:04d}-image-{image_index:02d}"
            assets.append(
                asset_defaults(
                    asset_id,
                    {
                        "format": "pdf",
                        "source_page": source_page,
                        "logical_pages": logical_pages_by_source.get(source_page, []),
                        "object_name": raw_name,
                    },
                    assets_root / f"{asset_id}{suffix or '.png'}",
                    output_root,
                    data,
                    suffix,
                )
            )
    return assets


def normalized_archive_path(base: str, raw_path: str) -> str | None:
    candidate = posixpath.normpath(posixpath.join(base, raw_path.split("#", 1)[0].split("?", 1)[0]))
    path = PurePosixPath(candidate)
    if path.is_absolute() or ".." in path.parts:
        return None
    return path.as_posix()


def local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def epub_image_assets(source: Path, output_root: Path, pages: list[dict]) -> list[dict]:
    with zipfile.ZipFile(source) as archive:
        try:
            container = ET.fromstring(archive.read("META-INF/container.xml"))
            rootfile = next(element for element in container.iter() if element.tag.endswith("rootfile"))
            opf_path = rootfile.attrib["full-path"]
            package = ET.fromstring(archive.read(opf_path))
        except (KeyError, StopIteration, ET.ParseError) as error:
            raise RuntimeError("Could not read the EPUB package manifest.") from error

        opf_parent = PurePosixPath(opf_path).parent.as_posix()
        manifest: dict[str, dict[str, str]] = {}
        cover_item_ids: set[str] = set()
        for item in package.iter():
            if local_name(item.tag) != "item" or not item.attrib.get("id") or not item.attrib.get("href"):
                continue
            archive_path = normalized_archive_path(opf_parent, item.attrib["href"])
            if archive_path is not None:
                item_id = item.attrib["id"]
                properties = item.attrib.get("properties", "")
                manifest[item_id] = {
                    "archive_path": archive_path,
                    "media_type": item.attrib.get("media-type", ""),
                    "properties": properties,
                }
                if "cover-image" in properties.split():
                    cover_item_ids.add(item_id)

        for meta in package.iter():
            if local_name(meta.tag) == "meta" and meta.attrib.get("name") == "cover":
                cover_item_ids.add(meta.attrib.get("content", ""))

        document_locators = [
            item["archive_path"]
            for itemref in package.iter()
            if local_name(itemref.tag) == "itemref"
            for item in [manifest.get(itemref.attrib.get("idref", ""))]
            if item and item["media_type"] in {"application/xhtml+xml", "text/html"}
        ]
        if not document_locators:
            document_locators = [
                item["archive_path"]
                for item in manifest.values()
                if item["media_type"] in {"application/xhtml+xml", "text/html"}
            ]
        cover_document_locators = {
            path
            for reference in package.iter()
            if local_name(reference.tag) == "reference"
            and reference.attrib.get("type", "").casefold() == "cover"
            for path in [normalized_archive_path(opf_parent, reference.attrib.get("href", ""))]
            if path
        }
        cover_image_paths = {
            item["archive_path"]
            for item_id, item in manifest.items()
            if item_id in cover_item_ids and item["media_type"].startswith("image/")
        }

        logical_pages_by_locator = {
            page.get("source_locator"): page.get("logical_page")
            for page in pages
            if isinstance(page.get("source_locator"), str) and isinstance(page.get("logical_page"), int)
        }
        references: dict[str, list[str]] = {}
        for document in document_locators:
            try:
                document_root = ET.fromstring(archive.read(document))
            except (KeyError, ET.ParseError):
                continue
            document_parent = PurePosixPath(document).parent.as_posix()
            for element in document_root.iter():
                if element.tag.endswith("img"):
                    target = normalized_archive_path(document_parent, element.attrib.get("src", ""))
                    if target:
                        references.setdefault(target, []).append(document)
                        if document in cover_document_locators:
                            cover_image_paths.add(target)

        assets: list[dict] = []
        assets_root = output_root / "assets" / "images" / "original"
        image_items = [
            (item_id, item)
            for item_id, item in manifest.items()
            if item["media_type"].startswith("image/")
        ]
        for index, (manifest_id, item) in enumerate(
            sorted(image_items, key=lambda entry: entry[1]["archive_path"]),
            start=1,
        ):
            archive_path = item["archive_path"]
            try:
                data = archive.read(archive_path)
            except KeyError:
                continue
            suffix = PurePosixPath(archive_path).suffix.lower()
            asset_id = f"epub-image-{index:04d}-{normalize_asset_segment(PurePosixPath(archive_path).stem, 'asset')}"
            document_refs = references.get(archive_path, [])
            logical_pages = sorted(
                logical_page
                for locator in document_refs
                for logical_page in [logical_pages_by_locator.get(locator)]
                if isinstance(logical_page, int)
            )
            asset = asset_defaults(
                asset_id,
                {
                    "format": "epub",
                    "source_locator": archive_path,
                    "logical_pages": logical_pages,
                    "document_locators": document_refs,
                    "manifest_id": manifest_id,
                    "declared_cover": archive_path in cover_image_paths,
                },
                assets_root / f"{asset_id}{suffix or '.png'}",
                output_root,
                data,
                suffix,
                declared_source_cover=archive_path in cover_image_paths,
            )
            asset["original"]["media_type"] = item["media_type"] or asset["original"]["media_type"]
            assets.append(asset)
        return assets


def source_image_assets(source: Path, output_root: Path, pages: list[dict]) -> list[dict]:
    if source.suffix.lower() == ".pdf":
        return pdf_image_assets(source, output_root, pages)
    if source.suffix.lower() == ".epub":
        return epub_image_assets(source, output_root, pages)
    raise RuntimeError("Only .pdf and .epub source files are supported.")
