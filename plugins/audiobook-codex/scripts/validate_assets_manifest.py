from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

from path_safety import resolve_under


ASSET_FORMATS = {"pdf", "epub"}
TEXT_PIXEL_STATES = {"none", "printed", "handwriting", "mixed", "unknown"}
RESTORATION_STATUSES = {"not_requested", "candidate", "approved", "rejected"}
RESTORATION_ELIGIBILITY = {"prohibited", "review_required", "eligible", "manual_exception"}
EPUB_ROLES = {"unresolved", "illustration", "facsimile", "cover"}
EPUB_PLACEMENTS = {"unresolved", "after_title", "end", "source_cover"}
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


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def is_sha256(value: object) -> bool:
    if not isinstance(value, str) or len(value) != 64:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


def require_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def detected_image_media_type(path: Path) -> str | None:
    try:
        from PIL import Image
    except ImportError as error:
        raise RuntimeError(
            "Pillow is required to validate restored image media types. "
            "Run this script with the Codex bundled Python."
        ) from error
    try:
        with Image.open(path) as image:
            return Image.MIME.get(str(image.format or "").upper())
    except OSError:
        return None


def validate_assets_manifest(
    manifest: object,
    book_root: Path,
    book_map: object | None,
    check_files: bool,
) -> list[str]:
    errors: list[str] = []
    if not isinstance(manifest, dict):
        return ["assets manifest must be a JSON object"]
    if manifest.get("schema_version") != "1.0":
        errors.append("schema_version must be '1.0'")
    source_sha256 = manifest.get("source_sha256")
    if not is_sha256(source_sha256):
        errors.append("source_sha256 must be a SHA-256 hex string")
    if isinstance(book_map, dict):
        map_source = book_map.get("source")
        map_sha256 = map_source.get("sha256") if isinstance(map_source, dict) else None
        if source_sha256 != map_sha256:
            errors.append("source_sha256 does not match book-map.json source.sha256")

    assets = manifest.get("assets")
    if not isinstance(assets, list):
        return errors + ["assets must be an array"]

    ids: set[str] = set()
    for index, asset in enumerate(assets):
        label = f"assets[{index}]"
        if not isinstance(asset, dict):
            errors.append(f"{label} must be an object")
            continue
        asset_id = asset.get("id")
        if not require_text(asset_id):
            errors.append(f"{label}.id must be non-empty")
        elif asset_id in ids:
            errors.append(f"{label}.id is duplicated: {asset_id}")
        else:
            ids.add(asset_id)

        source = asset.get("source")
        if not isinstance(source, dict):
            errors.append(f"{label}.source must be an object")
        else:
            source_format = source.get("format")
            if source_format not in ASSET_FORMATS:
                errors.append(f"{label}.source.format must be pdf or epub")
            if source_format == "pdf" and not isinstance(source.get("source_page"), int):
                errors.append(f"{label}.source.source_page must be an integer for PDF assets")
            if source_format == "epub" and not require_text(source.get("source_locator")):
                errors.append(f"{label}.source.source_locator must be non-empty for EPUB assets")
            logical_pages = source.get("logical_pages", [])
            if not isinstance(logical_pages, list) or any(
                not isinstance(page, int) or page <= 0 for page in logical_pages
            ):
                errors.append(f"{label}.source.logical_pages must be positive integers")

        original = asset.get("original")
        original_sha256 = None
        if not isinstance(original, dict):
            errors.append(f"{label}.original must be an object")
        else:
            original_path = original.get("path")
            target = resolve_under(
                book_root,
                original_path,
                (Path("assets") / "images" / "original",),
            )
            if target is None:
                errors.append(
                    f"{label}.original.path must resolve under assets/images/original/: {original_path}"
                )
            elif check_files:
                if not target.is_file():
                    errors.append(f"{label}.original.path is missing: {original_path}")
                elif original.get("sha256") != sha256_file(target):
                    errors.append(f"{label}.original.sha256 does not match original.path")
            original_sha256 = original.get("sha256")
            if not is_sha256(original_sha256):
                errors.append(f"{label}.original.sha256 must be a SHA-256 hex string")
            if not require_text(original.get("media_type")) or not str(original.get("media_type")).startswith("image/"):
                errors.append(f"{label}.original.media_type must be an image media type")
            for dimension in ("width", "height"):
                value = original.get(dimension)
                if value is not None and (not isinstance(value, int) or value <= 0):
                    errors.append(f"{label}.original.{dimension} must be a positive integer or null")

        classification = asset.get("classification")
        if not isinstance(classification, dict):
            errors.append(f"{label}.classification must be an object")
        else:
            if not require_text(classification.get("content")):
                errors.append(f"{label}.classification.content must be non-empty")
            if classification.get("text_pixels") not in TEXT_PIXEL_STATES:
                errors.append(f"{label}.classification.text_pixels is invalid")
            if classification.get("restoration_eligibility") not in RESTORATION_ELIGIBILITY:
                errors.append(f"{label}.classification.restoration_eligibility is invalid")
            if not isinstance(classification.get("evidence"), list):
                errors.append(f"{label}.classification.evidence must be an array")

        epub = asset.get("epub")
        if not isinstance(epub, dict):
            errors.append(f"{label}.epub must be an object")
        else:
            role = epub.get("role")
            placement = epub.get("placement")
            if role not in EPUB_ROLES:
                errors.append(f"{label}.epub.role is invalid")
            if placement not in EPUB_PLACEMENTS:
                errors.append(f"{label}.epub.placement is invalid")
            if epub.get("document_id") is not None and not require_text(epub.get("document_id")):
                errors.append(f"{label}.epub.document_id must be null or non-empty")
            if not isinstance(epub.get("alt_text"), str):
                errors.append(f"{label}.epub.alt_text must be a string")
            if role == "unresolved" and (
                placement != "unresolved" or epub.get("document_id") is not None
            ):
                errors.append(
                    f"{label}.epub unresolved assets must use placement unresolved and document_id null"
                )
            if role == "cover" and placement != "source_cover":
                errors.append(f"{label}.epub cover assets must use placement source_cover")
            if role in {"illustration", "facsimile"} and placement == "source_cover":
                errors.append(f"{label}.epub figures cannot use placement source_cover")

        restoration = asset.get("restoration")
        if not isinstance(restoration, dict):
            errors.append(f"{label}.restoration must be an object")
            continue
        status = restoration.get("status")
        approved = restoration.get("approved")
        if status not in RESTORATION_STATUSES:
            errors.append(f"{label}.restoration.status is invalid")
        if status != "approved" and approved is not None:
            errors.append(f"{label}.restoration.approved must be null unless status is approved")
        if status != "approved":
            continue
        if not isinstance(approved, dict):
            errors.append(f"{label}.restoration.approved must be an object when status is approved")
            continue
        approved_path = approved.get("path")
        target = resolve_under(
            book_root,
            approved_path,
            (Path("restoration") / "approved",),
        )
        if target is None:
            errors.append(
                f"{label}.restoration.approved.path must resolve under restoration/approved/: {approved_path}"
            )
        elif check_files:
            if not target.is_file():
                errors.append(f"{label}.restoration.approved.path is missing: {approved_path}")
            elif approved.get("sha256") != sha256_file(target):
                errors.append(f"{label}.restoration.approved.sha256 does not match approved.path")
        if not is_sha256(approved.get("sha256")):
            errors.append(f"{label}.restoration.approved.sha256 must be a SHA-256 hex string")
        approved_media_type = approved.get("media_type")
        if not require_text(approved_media_type) or not str(approved_media_type).startswith("image/"):
            errors.append(f"{label}.restoration.approved.media_type must be an image media type")
        elif isinstance(target, Path):
            expected_media_type = IMAGE_MEDIA_TYPES.get(target.suffix.lower())
            if expected_media_type and approved_media_type != expected_media_type:
                errors.append(
                    f"{label}.restoration.approved.media_type does not match approved.path suffix"
                )
            if check_files and target.is_file():
                actual_media_type = detected_image_media_type(target)
                if actual_media_type is None:
                    errors.append(f"{label}.restoration.approved.path is not a readable image")
                elif approved_media_type != actual_media_type:
                    errors.append(
                        f"{label}.restoration.approved.media_type does not match approved.path bytes"
                    )
        if approved.get("original_sha256") != original_sha256:
            errors.append(f"{label}.restoration.approved.original_sha256 must match original.sha256")
        for key in ("tool", "prompt", "reviewed_by", "approved_at"):
            if not require_text(approved.get(key)):
                errors.append(f"{label}.restoration.approved.{key} must be non-empty")
        if isinstance(classification, dict):
            text_pixels = classification.get("text_pixels")
            eligibility = classification.get("restoration_eligibility")
            if eligibility == "prohibited":
                errors.append(f"{label}.restoration.approved is prohibited by classification")
            elif text_pixels == "none":
                if eligibility != "eligible":
                    errors.append(
                        f"{label}.restoration.approved requires eligible restoration for non-text pixels"
                    )
            elif text_pixels in {"printed", "handwriting", "mixed"}:
                if eligibility != "manual_exception":
                    errors.append(
                        f"{label}.restoration.approved requires manual_exception for text-bearing pixels"
                    )
                elif not require_text(approved.get("exception_reason")):
                    errors.append(
                        f"{label}.restoration.approved.exception_reason is required for manual_exception"
                    )
            else:
                errors.append(
                    f"{label}.restoration.approved requires a reviewed text_pixels classification"
                )

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate an Audiobook Codex assets-manifest.json file.")
    parser.add_argument("--assets-manifest", required=True, type=Path)
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--book-map", type=Path)
    parser.add_argument("--check-files", action="store_true")
    args = parser.parse_args()

    try:
        book_root = args.book_root.expanduser().resolve()
        manifest_path = args.assets_manifest.expanduser().resolve()
        manifest = load_json(manifest_path)
        book_map = load_json(args.book_map.expanduser().resolve()) if args.book_map else None
        errors = validate_assets_manifest(manifest, book_root, book_map, args.check_files)
    except RuntimeError as error:
        print(f"INVALID assets manifest: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    if errors:
        print("INVALID assets manifest:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"VALID: {manifest_path}")


if __name__ == "__main__":
    main()
