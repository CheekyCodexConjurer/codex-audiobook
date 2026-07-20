from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path, PurePosixPath
import re
import sys
import unicodedata
import zipfile

from book_layout import resolve_book_paths
from epub_layout import layout_document_index
from epub_layout import lines_for_block
from epub_layout import load_json as load_layout_json
from epub_layout import validate_layout
from epub_presentation import (
    COVER_DOCUMENT_PATH,
    COVER_IMAGE_PATH,
    cover_alt_text,
    cover_image,
    normalize_visual_profile,
    profile_resources,
    profile_stylesheet,
    sha256_bytes,
)
from reader_export_contract import exporter_code_paths as contract_exporter_code_paths
from reader_export_contract import sidecar_contract_matches
from reader_export_contract import sidecar_identity_matches
from validate_assets_manifest import load_json as load_assets_json
from validate_assets_manifest import resolve_under, validate_assets_manifest
from validate_book_map import load_json as load_book_map_json
from validate_book_map import validate_book_map
from verify_text_ledger import chapter_output_records
from verify_text_ledger import expected_chapter_outputs
from verify_text_ledger import verify as verify_text_ledger
from verify_translation_ledger import TARGET_LANGUAGE
from verify_translation_ledger import translation_chapter_output_records
from verify_translation_ledger import verify as verify_translation_ledger
from verify_revision_ledger import TARGET_LANGUAGE as REVISION_LANGUAGE
from verify_revision_ledger import revision_changes_by_output
from verify_revision_ledger import revision_chapter_output_records
from verify_revision_ledger import verify as verify_revision_ledger
from verify_fluid_edition_ledger import FLUID_PROFILE
from verify_fluid_edition_ledger import TARGET_LANGUAGE as FLUID_LANGUAGE
from verify_fluid_edition_ledger import fluid_chapter_output_records
from verify_fluid_edition_ledger import verify as verify_fluid_edition_ledger
from publication_selection import uses_unsuffixed_fluid_export_name


IMAGE_EDITIONS = {"original", "approved-restored"}
TEXT_EDITIONS = {
    "fluid-pt-br",
    "original",
    "revised-pt-br",
    "translated-pt-br",
}
_SUPPLEMENTARY_FLUID_TITLES = {
    "bibliografia", "bibliography", "referencias", "references", "glossario",
    "glossary", "indice", "index", "leituras complementares", "further reading",
    "fontes", "source list", "colofao", "colophon",
}
ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
EPUB_MODIFIED = "2000-01-01T00:00:00Z"
EXPORT_RENDER_CONTRACT_REVISION = "reader-export-render-v2"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_json_bytes(value: object) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def atomic_replace_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staged = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    if staged.exists():
        raise RuntimeError(f"Temporary export file already exists: {staged}")
    try:
        staged.write_bytes(data)
        os.replace(staged, path)
    except Exception:
        if staged.exists():
            staged.unlink()
        raise


def write_json(path: Path, value: object) -> None:
    atomic_replace_bytes(
        path,
        (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
    )


def export_input_fingerprint(payload: object) -> dict:
    return {
        "algorithm": "sha256",
        "canonicalization": "json-sort-keys-v1",
        "value": hashlib.sha256(canonical_json_bytes(payload)).hexdigest(),
    }


def cached_export_is_current(
    output: Path,
    sidecar: Path,
    book_root: Path,
    path_key: str,
    hash_key: str,
    fingerprint: dict,
    expected_sidecar: dict | None = None,
) -> bool:
    if not output.is_file():
        return False
    if not sidecar.is_file():
        return False
    try:
        data = json.loads(sidecar.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    if not isinstance(data, dict):
        return False
    identity_matches = (
        data.get(path_key) == relative_to_book(book_root, output)
        and data.get(hash_key) == sha256_file(output)
        and data.get("input_fingerprint") == fingerprint
    )
    if expected_sidecar is None:
        return identity_matches
    if sidecar_identity_matches(data, expected_sidecar, path_key, hash_key):
        if sidecar_contract_matches(data, expected_sidecar):
            return True
        write_json(sidecar, expected_sidecar)
        return True
    return False


def export_fingerprint_payload(
    kind: str,
    book_root: Path,
    epub_manifest_path: Path,
    assets_manifest_path: Path,
    map_path: Path,
    ledger_path: Path,
    epub_manifest: dict,
    book: dict,
    language: str,
    text_edition: str,
    image_edition: str,
    documents: list[dict],
    selected_assets_by_document: dict[str, list[dict]],
    visual_profile: dict | None,
    renderer: dict | None = None,
) -> dict:
    source_files = [
        ("book_map", map_path),
        ("text_ledger", ledger_path),
        ("assets_manifest", assets_manifest_path),
        ("epub_manifest", epub_manifest_path),
    ]
    layout = epub_manifest.get("layout")
    if isinstance(layout, dict) and isinstance(layout.get("path"), str):
        layout_path = (book_root / layout["path"]).resolve()
        if layout_path.is_file():
            source_files.append(("layout", layout_path))

    canonical_documents = []
    for document in documents:
        text_path = document.get("_text_path")
        canonical_document = {
            "id": document.get("id"),
            "kind": document.get("kind"),
            "title": document.get("title"),
            "asset_ids": document.get("asset_ids"),
            "source_sha256": document.get("source_sha256"),
            "translation_sha256": document.get("translation_sha256"),
            "revised_sha256": document.get("revised_sha256"),
            "fluid_sha256": document.get("fluid_sha256"),
            "layout_blocks": document.get("_layout_blocks"),
            "revision_changes": document.get("_revision_changes"),
        }
        if isinstance(text_path, Path):
            canonical_document["text_path"] = fingerprint_path(book_root, text_path)
            canonical_document["text_sha256"] = sha256_file(text_path)
        canonical_documents.append(canonical_document)

    selected_assets = {}
    for document_id, assets in selected_assets_by_document.items():
        selected_assets[document_id] = [
            {
                "id": asset.get("id"),
                "path": fingerprint_path(book_root, asset["path"])
                if isinstance(asset.get("path"), Path)
                else asset.get("path"),
                "sha256": asset.get("sha256"),
                "media_type": asset.get("media_type"),
                "role": asset.get("role"),
                "placement": asset.get("placement"),
            }
            for asset in assets
        ]

    payload = {
        "schema_version": "1.0",
        "kind": kind,
        "options": {
            "image_edition": image_edition,
            "text_edition": text_edition,
            "language": language,
        },
        "book": book,
        "manifest": epub_manifest,
        "source_files": [
            {
                "role": role,
                "path": fingerprint_path(book_root, path),
                "sha256": sha256_file(path),
            }
            for role, path in source_files
        ],
        "documents": canonical_documents,
        "selected_assets_by_document": selected_assets,
        "visual_profile": visual_profile,
        "export_contract": export_contract_payload(kind, visual_profile),
    }
    if renderer is not None:
        payload["renderer"] = renderer
    return payload


def require_text(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def safe_segment(value: str, fallback: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", value.encode("ascii", "ignore").decode("ascii"))
    return normalized.strip(".-")[:100] or fallback


def escape(value: object) -> str:
    return html.escape(str(value or ""), quote=True)


def normalize_space(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def is_fluid_supplementary_title(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = unicodedata.normalize("NFD", normalize_space(value).casefold())
    normalized = "".join(
        character for character in normalized if unicodedata.category(character) != "Mn"
    )
    return normalized in _SUPPLEMENTARY_FLUID_TITLES


def is_fluid_supplementary_document(document: dict) -> bool:
    return is_fluid_supplementary_title(document.get("title"))


def published_documents(documents: list[dict], text_edition: str) -> list[dict]:
    """Return the reader-facing documents for the requested text edition."""
    if text_edition != "fluid-pt-br":
        return documents
    return [
        document
        for document in documents
        if not is_fluid_supplementary_document(document)
    ]


def reader_documents(
    documents: list[dict],
    text_edition: str,
    book_root: Path,
) -> list[dict]:
    """Apply reader-only terminal-apparatus exclusions without mutating provenance."""
    published: list[dict] = []
    for document in published_documents(documents, text_edition):
        layout_blocks = document.get("_layout_blocks")
        if text_edition != "fluid-pt-br" or not isinstance(layout_blocks, list):
            published.append(document)
            continue
        visible_blocks: list[dict] = []
        revision_changes = document.get("_revision_changes") or []
        for block in layout_blocks:
            if not isinstance(block, dict):
                continue
            if block.get("kind") == "heading":
                values = _layout_text_values(block, book_root, revision_changes)
                if any(is_fluid_supplementary_title(value) for value in values):
                    break
            visible_blocks.append(block)
        published.append({**document, "_layout_blocks": visible_blocks})
    return published


def relative_to_book(book_root: Path, path: Path) -> str:
    return path.resolve().relative_to(book_root.resolve()).as_posix()


def fingerprint_path(book_root: Path, path: Path) -> str:
    try:
        return relative_to_book(book_root, path)
    except ValueError:
        return path.resolve().as_posix()


def exporter_code_paths(kind: str) -> list[Path]:
    scripts_root = Path(__file__).resolve().parent
    return contract_exporter_code_paths(kind, scripts_root)


def export_contract_payload(kind: str, visual_profile: dict | None) -> dict:
    payload = {
        "revision": EXPORT_RENDER_CONTRACT_REVISION,
        "code": [
            {
                "path": path.name,
                "sha256": sha256_file(path),
            }
            for path in exporter_code_paths(kind)
        ],
    }
    presentation_resources = profile_resources(visual_profile)
    if presentation_resources:
        payload["presentation_resources"] = [
            {
                "id": resource.identifier,
                "path": resource.epub_path,
                "media_type": resource.media_type,
                "sha256": resource.sha256,
            }
            for resource in presentation_resources
        ]
    return payload


def _layout_contract_edition(text_edition: str) -> str:
    if text_edition in {"fluid-pt-br", "translated-pt-br"}:
        return text_edition
    return "original"


def canonical_layout_relative_path(text_edition: str) -> str:
    if text_edition == "fluid-pt-br":
        return "metadata/epub-layout.fluid.json"
    if text_edition == "translated-pt-br":
        return "metadata/epub-layout.pt-br.json"
    return "metadata/epub-layout.json"


def export_directory(book_root: Path) -> Path:
    return (book_root / "exports" / "epub").resolve()


def resolve_export_output(book_root: Path, raw_output: Path | None, default_name: str) -> Path:
    output = raw_output.expanduser().resolve() if raw_output else export_directory(book_root) / default_name
    try:
        output.relative_to(export_directory(book_root))
    except ValueError as error:
        raise RuntimeError(
            f"EPUB output must remain under {export_directory(book_root)}: {output}"
        ) from error
    if output.suffix.lower() != ".epub":
        raise RuntimeError("EPUB output must use the .epub extension")
    return output


def load_export_context(
    book_root: Path,
    epub_manifest_path: Path,
    assets_manifest_path: Path,
    text_edition: str,
) -> tuple[
    dict,
    dict,
    dict,
    dict,
    Path,
    Path,
    dict | None,
    dict | None,
    dict | None,
    dict | None,
    dict | None,
]:
    map_path = book_root / "metadata" / "book-map.json"
    ledger_path = book_root / "metadata" / "text-ledger.json"
    text_root = book_root / "text"
    book_map = load_book_map_json(map_path)
    ledger = load_assets_json(ledger_path)
    assets_manifest = load_assets_json(assets_manifest_path)
    epub_manifest = load_assets_json(epub_manifest_path)
    if not all(isinstance(value, dict) for value in (book_map, ledger, assets_manifest, epub_manifest)):
        raise RuntimeError("Book map, ledger, assets manifest, and EPUB manifest must be JSON objects.")
    errors = validate_book_map(book_map, book_root, True, True)
    errors += verify_text_ledger(book_map, sha256_file(map_path), ledger, text_root, False, True)
    expected_outputs, expected_output_errors = expected_chapter_outputs(book_map, text_root)
    errors += expected_output_errors
    expected_document_ids = list(expected_outputs)
    errors += validate_assets_manifest(assets_manifest, book_root, book_map, True)
    if epub_manifest.get("schema_version") != "1.0":
        errors.append("epub manifest schema_version must be '1.0'")
    expected_hashes = {
        "book_map_sha256": sha256_file(map_path),
        "text_ledger_sha256": sha256_file(ledger_path),
        "assets_manifest_sha256": sha256_file(assets_manifest_path),
    }
    for key, expected in expected_hashes.items():
        if epub_manifest.get(key) != expected:
            errors.append(f"epub manifest {key} does not match current input")
    if not require_text(epub_manifest.get("language")):
        errors.append("epub manifest language must be non-empty")
    manifest_documents = epub_manifest.get("documents")
    if isinstance(manifest_documents, list):
        source_cover_indexes = [
            index
            for index, document in enumerate(manifest_documents)
            if isinstance(document, dict) and document.get("kind") == "source_cover"
        ]
        if len(source_cover_indexes) > 1:
            errors.append("epub manifest may declare at most one source_cover document")
        elif source_cover_indexes and source_cover_indexes[0] != 0:
            errors.append("epub manifest source_cover document must be first")
        manifest_document_ids = [
            document["id"]
            for document in manifest_documents
            if isinstance(document, dict)
            and isinstance(document.get("id"), str)
            and document.get("kind") != "source_cover"
        ]
        if manifest_document_ids != expected_document_ids:
            errors.append(
                "epub manifest documents must preserve the validated source document order"
            )
    manifest_text_edition = epub_manifest.get("text_edition", "original")
    if manifest_text_edition not in TEXT_EDITIONS:
        errors.append("epub manifest text_edition is invalid")
    elif manifest_text_edition != text_edition:
        errors.append("epub manifest text_edition does not match requested text edition")
    translation_ledger = None
    revision_ledger = None
    fluid_style = None
    fluid_ledger = None
    if text_edition == "fluid-pt-br":
        fluid_style_path = book_root / "metadata" / "fluid-style.json"
        fluid_ledger_path = book_root / "metadata" / "fluid-edition-ledger.json"
        fluid_style = load_assets_json(fluid_style_path)
        fluid_ledger = load_assets_json(fluid_ledger_path)
        if not isinstance(fluid_style, dict):
            errors.append("fluid style must be a JSON object")
        if not isinstance(fluid_ledger, dict):
            errors.append("fluid edition ledger must be a JSON object")
        if isinstance(fluid_ledger, dict) and fluid_ledger.get(
            "base_edition"
        ) == "translated-pt-br":
            translation_path = book_root / "metadata" / "translation-ledger.json"
            translation_ledger = load_assets_json(translation_path)
            if not isinstance(translation_ledger, dict):
                errors.append("translation ledger must be a JSON object")
        else:
            translation_path = None
        if isinstance(fluid_style, dict) and isinstance(fluid_ledger, dict):
            errors += verify_fluid_edition_ledger(
                book_map,
                sha256_file(map_path),
                ledger,
                sha256_file(ledger_path),
                translation_ledger,
                sha256_file(translation_path)
                if translation_path is not None
                else None,
                fluid_style,
                sha256_file(fluid_style_path),
                fluid_ledger,
                text_root,
            )
            fluid_hashes = {
                "fluid_style_sha256": sha256_file(fluid_style_path),
                "fluid_edition_ledger_sha256": sha256_file(fluid_ledger_path),
                "base_ledger_sha256": fluid_ledger.get("base_ledger_sha256"),
            }
            for key, expected in fluid_hashes.items():
                if epub_manifest.get(key) != expected:
                    errors.append(
                        f"epub manifest {key} does not match current fluid input"
                    )
            if epub_manifest.get("base_edition") != fluid_ledger.get(
                "base_edition"
            ):
                errors.append(
                    "epub manifest base_edition does not match fluid edition ledger"
                )
            if epub_manifest.get("profile") != FLUID_PROFILE:
                errors.append(
                    f"fluid EPUB manifest profile must be {FLUID_PROFILE}"
                )
            if fluid_ledger.get("base_edition") == "translated-pt-br":
                if not isinstance(translation_ledger, dict):
                    errors.append(
                        "translated fluid EPUB requires a translation ledger"
                    )
                else:
                    if epub_manifest.get(
                        "translation_ledger_sha256"
                    ) != sha256_file(translation_path):
                        errors.append(
                            "epub manifest translation_ledger_sha256 does not "
                            "match the fluid base"
                        )
                    if epub_manifest.get(
                        "source_language"
                    ) != translation_ledger.get("source_language"):
                        errors.append(
                            "epub manifest source_language does not match the "
                            "fluid translated base"
                        )
        if epub_manifest.get("language") != FLUID_LANGUAGE:
            errors.append(
                f"fluid EPUB manifest language must be {FLUID_LANGUAGE}"
            )
    elif text_edition == "translated-pt-br":
        translation_path = book_root / "metadata" / "translation-ledger.json"
        translation_ledger = load_assets_json(translation_path)
        if not isinstance(translation_ledger, dict):
            errors.append("translation ledger must be a JSON object")
        else:
            errors += verify_translation_ledger(
                book_map,
                sha256_file(map_path),
                ledger,
                sha256_file(ledger_path),
                translation_ledger,
                text_root,
            )
            if epub_manifest.get("translation_ledger_sha256") != sha256_file(translation_path):
                errors.append("epub manifest translation_ledger_sha256 does not match current input")
            if epub_manifest.get("source_language") != translation_ledger.get("source_language"):
                errors.append("epub manifest source_language does not match translation ledger")
        if epub_manifest.get("language") != TARGET_LANGUAGE:
            errors.append(f"translated EPUB manifest language must be {TARGET_LANGUAGE}")
    elif text_edition == "revised-pt-br":
        revision_path = book_root / "metadata" / "revision-ledger.json"
        revision_ledger = load_assets_json(revision_path)
        if not isinstance(revision_ledger, dict):
            errors.append("revision ledger must be a JSON object")
        else:
            errors += verify_revision_ledger(
                book_map,
                sha256_file(map_path),
                ledger,
                sha256_file(ledger_path),
                revision_ledger,
                text_root,
            )
            if epub_manifest.get("revision_ledger_sha256") != sha256_file(revision_path):
                errors.append("epub manifest revision_ledger_sha256 does not match current input")
        if epub_manifest.get("language") != REVISION_LANGUAGE:
            errors.append(f"revised EPUB manifest language must be {REVISION_LANGUAGE}")
    layout = None
    layout_descriptor = epub_manifest.get("layout")
    if layout_descriptor is None:
        if text_edition == "translated-pt-br":
            errors.append("translated EPUB manifest requires a semantic EPUB layout")
    else:
        expected_layout_relative = canonical_layout_relative_path(text_edition)
        if not isinstance(layout_descriptor, dict):
            errors.append("epub manifest layout must be an object")
        elif layout_descriptor.get("mode") != "semantic":
            errors.append("epub manifest layout mode must be semantic")
        elif layout_descriptor.get("path") != expected_layout_relative:
            errors.append(
                f"epub manifest layout path must be {expected_layout_relative}"
            )
        else:
            layout_path = book_root / expected_layout_relative
            if not layout_path.is_file():
                errors.append(f"epub layout is missing: {layout_path}")
            elif layout_descriptor.get("sha256") != sha256_file(layout_path):
                errors.append("epub manifest layout SHA-256 does not match current EPUB layout")
            else:
                try:
                    layout = load_layout_json(layout_path)
                except RuntimeError as error:
                    errors.append(str(error))
                else:
                    errors += validate_layout(
                        layout,
                        book_root,
                        sha256_file(map_path),
                        sha256_file(ledger_path),
                        ledger,
                        expected_document_ids,
                        text_edition=_layout_contract_edition(text_edition),
                        edition_ledger_sha256=(
                            sha256_file(
                                book_root
                                / "metadata"
                                / "fluid-edition-ledger.json"
                            )
                            if text_edition == "fluid-pt-br"
                            else sha256_file(
                                book_root
                                / "metadata"
                                / "translation-ledger.json"
                            )
                            if text_edition == "translated-pt-br"
                            else None
                        ),
                        edition_outputs=(
                            fluid_chapter_output_records(fluid_ledger)
                            if isinstance(fluid_ledger, dict)
                            else translation_chapter_output_records(translation_ledger)
                            if isinstance(translation_ledger, dict)
                            and text_edition == "translated-pt-br"
                            else None
                        ),
                    )
    try:
        normalize_visual_profile(epub_manifest.get("visual_profile"))
    except RuntimeError as error:
        errors.append(str(error))
    if errors:
        raise RuntimeError("; ".join(errors))
    return (
        book_map,
        ledger,
        assets_manifest,
        epub_manifest,
        map_path,
        ledger_path,
        translation_ledger,
        revision_ledger,
        fluid_style,
        fluid_ledger,
        layout,
    )


def validate_documents(
    book_root: Path,
    epub_manifest: dict,
    assets_manifest: dict,
    ledger: dict,
    text_edition: str,
    translation_ledger: dict | None,
    revision_ledger: dict | None,
    fluid_ledger: dict | None,
    layout: dict | None,
) -> tuple[list[dict], dict[str, dict]]:
    documents = epub_manifest.get("documents")
    if not isinstance(documents, list) or not documents:
        raise RuntimeError("epub manifest documents must be a non-empty array")
    assets = assets_manifest.get("assets")
    asset_by_id = {
        asset.get("id"): asset
        for asset in assets
        if isinstance(asset, dict) and isinstance(asset.get("id"), str)
    } if isinstance(assets, list) else {}
    ledger_outputs = chapter_output_records(ledger)
    translation_outputs = (
        translation_chapter_output_records(translation_ledger)
        if isinstance(translation_ledger, dict)
        else {}
    )
    revision_outputs = (
        revision_chapter_output_records(revision_ledger)
        if isinstance(revision_ledger, dict)
        else {}
    )
    revision_changes = (
        revision_changes_by_output(revision_ledger)
        if isinstance(revision_ledger, dict)
        else {}
    )
    fluid_outputs = (
        fluid_chapter_output_records(fluid_ledger)
        if isinstance(fluid_ledger, dict)
        else {}
    )
    text_root = book_root / "text"
    layout_by_document = layout_document_index(layout) if isinstance(layout, dict) else {}
    ids: set[str] = set()
    validated: list[dict] = []
    for index, document in enumerate(documents):
        label = f"documents[{index}]"
        if not isinstance(document, dict):
            raise RuntimeError(f"{label} must be an object")
        document_id = document.get("id")
        if not require_text(document_id) or document_id in ids:
            raise RuntimeError(f"{label}.id must be unique and non-empty")
        ids.add(document_id)
        if not require_text(document.get("title")):
            raise RuntimeError(f"{label}.title must be non-empty")
        source_file = document.get("source_file")
        source_path = None
        text_path = None
        is_source_cover = document.get("kind") == "source_cover"
        if is_source_cover:
            if source_file is not None or document.get("source_sha256") is not None:
                raise RuntimeError(f"{label} source_cover must not declare source text")
        else:
            source_path = resolve_under(book_root, source_file)
            if source_path is None or not str(source_file).replace("\\", "/").startswith("text/source/"):
                raise RuntimeError(f"{label}.source_file must resolve under text/source/")
            if not source_path.is_file():
                raise RuntimeError(f"{label}.source_file is missing: {source_file}")
            if document.get("source_sha256") != sha256_file(source_path):
                raise RuntimeError(f"{label}.source_sha256 does not match source_file")
            ledger_output = ledger_outputs.get(document_id)
            if not isinstance(ledger_output, dict):
                raise RuntimeError(f"{label} has no verified chapter output in text-ledger.json")
            ledger_source = ledger_output.get("source_file")
            expected_source_path = resolve_under(text_root, ledger_source)
            if expected_source_path is None or expected_source_path != source_path:
                raise RuntimeError(f"{label}.source_file does not match its verified chapter output")
            if document.get("source_sha256") != ledger_output.get("source_sha256"):
                raise RuntimeError(f"{label}.source_sha256 does not match its verified chapter output")
            text_path = source_path
            if text_edition == "translated-pt-br":
                translation_output = translation_outputs.get(document_id)
                if not isinstance(translation_output, dict):
                    raise RuntimeError(f"{label} has no verified PT-BR translation output")
                translation_file = document.get("translation_file")
                translation_path = resolve_under(book_root, translation_file)
                if (
                    translation_path is None
                    or not str(translation_file).replace("\\", "/").startswith("text/translation/pt-BR/")
                ):
                    raise RuntimeError(f"{label}.translation_file must resolve under text/translation/pt-BR/")
                if not translation_path.is_file():
                    raise RuntimeError(f"{label}.translation_file is missing: {translation_file}")
                if document.get("translation_sha256") != sha256_file(translation_path):
                    raise RuntimeError(f"{label}.translation_sha256 does not match translation_file")
                expected_translation_path = resolve_under(
                    text_root,
                    translation_output.get("translation_file"),
                )
                if expected_translation_path is None or translation_path != expected_translation_path:
                    raise RuntimeError(
                        f"{label}.translation_file does not match its verified translation output"
                    )
                if document.get("translation_sha256") != translation_output.get("translation_sha256"):
                    raise RuntimeError(
                        f"{label}.translation_sha256 does not match its verified translation output"
                    )
                text_path = translation_path
            elif text_edition == "revised-pt-br":
                revision_output = revision_outputs.get(document_id)
                if not isinstance(revision_output, dict):
                    raise RuntimeError(f"{label} has no verified revised PT-BR output")
                revised_file = document.get("revised_file")
                revised_path = resolve_under(book_root, revised_file)
                if (
                    revised_path is None
                    or not str(revised_file).replace("\\", "/").startswith(
                        "text/revision/pt-BR/"
                    )
                ):
                    raise RuntimeError(
                        f"{label}.revised_file must resolve under text/revision/pt-BR/"
                    )
                if not revised_path.is_file():
                    raise RuntimeError(f"{label}.revised_file is missing: {revised_file}")
                if document.get("revised_sha256") != sha256_file(revised_path):
                    raise RuntimeError(
                        f"{label}.revised_sha256 does not match revised_file"
                    )
                expected_revised_path = resolve_under(
                    text_root,
                    revision_output.get("revised_file"),
                )
                if expected_revised_path is None or revised_path != expected_revised_path:
                    raise RuntimeError(
                        f"{label}.revised_file does not match its verified revision output"
                    )
                if document.get("revised_sha256") != revision_output.get(
                    "revised_sha256"
                ):
                    raise RuntimeError(
                        f"{label}.revised_sha256 does not match its verified revision output"
                    )
                text_path = revised_path
            elif text_edition == "fluid-pt-br":
                fluid_output = fluid_outputs.get(document_id)
                if not isinstance(fluid_output, dict):
                    raise RuntimeError(
                        f"{label} has no verified fluid PT-BR output"
                    )
                if (
                    isinstance(fluid_ledger, dict)
                    and fluid_ledger.get("base_edition") == "translated-pt-br"
                ):
                    translation_output = translation_outputs.get(document_id)
                    if not isinstance(translation_output, dict):
                        raise RuntimeError(
                            f"{label} has no verified translated base output"
                        )
                    translation_file = document.get("translation_file")
                    translation_path = resolve_under(book_root, translation_file)
                    if (
                        translation_path is None
                        or not str(translation_file)
                        .replace("\\", "/")
                        .startswith("text/translation/pt-BR/")
                    ):
                        raise RuntimeError(
                            f"{label}.translation_file must resolve under "
                            "text/translation/pt-BR/"
                        )
                    if (
                        not translation_path.is_file()
                        or document.get("translation_sha256")
                        != sha256_file(translation_path)
                    ):
                        raise RuntimeError(
                            f"{label}.translation_file or translation_sha256 "
                            "does not match the fluid base"
                        )
                    expected_translation_path = resolve_under(
                        text_root,
                        translation_output.get("translation_file"),
                    )
                    if (
                        expected_translation_path is None
                        or translation_path != expected_translation_path
                        or document.get("translation_sha256")
                        != translation_output.get("translation_sha256")
                    ):
                        raise RuntimeError(
                            f"{label} translated base does not match its "
                            "verified translation output"
                        )
                fluid_file = document.get("fluid_file")
                fluid_path = resolve_under(book_root, fluid_file)
                if (
                    fluid_path is None
                    or not str(fluid_file)
                    .replace("\\", "/")
                    .startswith("text/fluid/pt-BR/")
                ):
                    raise RuntimeError(
                        f"{label}.fluid_file must resolve under text/fluid/pt-BR/"
                    )
                if not fluid_path.is_file():
                    raise RuntimeError(
                        f"{label}.fluid_file is missing: {fluid_file}"
                    )
                if document.get("fluid_sha256") != sha256_file(fluid_path):
                    raise RuntimeError(
                        f"{label}.fluid_sha256 does not match fluid_file"
                    )
                expected_fluid_path = resolve_under(
                    text_root,
                    fluid_output.get("fluid_file"),
                )
                if (
                    expected_fluid_path is None
                    or fluid_path != expected_fluid_path
                    or document.get("fluid_sha256")
                    != fluid_output.get("fluid_sha256")
                ):
                    raise RuntimeError(
                        f"{label}.fluid_file does not match its verified fluid output"
                    )
                text_path = fluid_path
        asset_ids = document.get("asset_ids", [])
        if not isinstance(asset_ids, list) or any(not isinstance(asset_id, str) for asset_id in asset_ids):
            raise RuntimeError(f"{label}.asset_ids must be an array of strings")
        unknown_assets = [asset_id for asset_id in asset_ids if asset_id not in asset_by_id]
        if unknown_assets:
            raise RuntimeError(f"{label}.asset_ids contain unknown assets: {unknown_assets}")
        if is_source_cover and not asset_ids:
            raise RuntimeError(f"{label} source_cover must reference at least one asset")
        layout_document = layout_by_document.get(document_id)
        if not is_source_cover and isinstance(layout, dict) and not isinstance(layout_document, dict):
            raise RuntimeError(f"{label} has no semantic EPUB layout document")
        validated.append(
            {
                **document,
                "_text_path": text_path,
                "_layout_blocks": layout_document.get("blocks") if isinstance(layout_document, dict) else None,
                "_revision_changes": revision_changes.get(document_id, []),
            }
        )
    return validated, asset_by_id


def selected_asset(asset: dict, book_root: Path, image_edition: str) -> dict:
    original = asset.get("original")
    if not isinstance(original, dict):
        raise RuntimeError(f"Asset {asset.get('id')} has no original record")
    selected = original
    if image_edition == "approved-restored":
        restoration = asset.get("restoration")
        approved = restoration.get("approved") if isinstance(restoration, dict) else None
        if not isinstance(restoration, dict) or restoration.get("status") != "approved" or not isinstance(approved, dict):
            raise RuntimeError(f"Asset {asset.get('id')} has no approved restoration")
        if approved.get("original_sha256") != original.get("sha256"):
            raise RuntimeError(f"Asset {asset.get('id')} approved restoration has invalid lineage")
        selected = {**original, **approved}
    selected_path = resolve_under(book_root, selected.get("path"))
    if selected_path is None or not selected_path.is_file():
        raise RuntimeError(f"Asset {asset.get('id')} selected path is missing")
    if selected.get("sha256") != sha256_file(selected_path):
        raise RuntimeError(f"Asset {asset.get('id')} selected file hash does not match")
    if not require_text(selected.get("media_type")) or not str(selected["media_type"]).startswith("image/"):
        raise RuntimeError(f"Asset {asset.get('id')} selected rendition has an invalid media type")
    return {
        "id": asset["id"],
        "path": selected_path,
        "sha256": selected["sha256"],
        "media_type": selected.get("media_type") or original.get("media_type"),
        "alt_text": str((asset.get("epub") or {}).get("alt_text") or ""),
        "role": str((asset.get("epub") or {}).get("role") or "illustration"),
        "placement": str((asset.get("epub") or {}).get("placement") or "after_title"),
    }


_CHAPTER_NUMBER_WORDS = frozenset(
    {
        "one",
        "two",
        "three",
        "four",
        "five",
        "six",
        "seven",
        "eight",
        "nine",
        "ten",
        "eleven",
        "twelve",
        "thirteen",
        "fourteen",
        "fifteen",
        "sixteen",
        "seventeen",
        "eighteen",
        "nineteen",
        "twenty",
        "um",
        "dois",
        "três",
        "tres",
        "quatro",
        "cinco",
        "seis",
        "sete",
        "oito",
        "nove",
        "dez",
        "onze",
        "doze",
        "treze",
        "catorze",
        "quatorze",
        "quinze",
        "dezesseis",
        "dezessete",
        "dezoito",
        "dezenove",
        "vinte",
    }
)
_CHAPTER_LABEL_PREFIX = re.compile(
    r"^(?:chapter|cap[ií]tulo|part|parte|book|livro)\s+(.+)$",
    re.IGNORECASE,
)
_CANONICAL_ROMAN = re.compile(
    r"m{0,3}(?:cm|cd|d?c{0,3})(?:xc|xl|l?x{0,3})(?:ix|iv|v?i{0,3})"
)


def _is_chapter_label(value: str) -> bool:
    normalized = _comparison_text(value).strip(" .:")
    prefixed = _CHAPTER_LABEL_PREFIX.fullmatch(normalized)
    if prefixed:
        normalized = prefixed.group(1).strip(" .:")
    if normalized in _CHAPTER_NUMBER_WORDS:
        return True
    if re.fullmatch(r"[1-9]\d{0,2}", normalized):
        return 1 <= int(normalized) <= 200
    if not normalized or _CANONICAL_ROMAN.fullmatch(normalized) is None:
        return False
    values = {"i": 1, "v": 5, "x": 10, "l": 50, "c": 100, "d": 500, "m": 1000}
    roman_value = sum(
        -values[letter]
        if index + 1 < len(normalized) and values[letter] < values[normalized[index + 1]]
        else values[letter]
        for index, letter in enumerate(normalized)
    )
    return roman_value <= 200


def _comparison_text(value: str) -> str:
    return unicodedata.normalize("NFC", normalize_space(value)).casefold()


def _heading_lines(
    block: str,
    normalized_title: str,
    allow_leading_chapter_label: bool,
) -> list[str] | None:
    lines = [normalize_space(line) for line in block.splitlines() if normalize_space(line)]
    if _comparison_text(" ".join(lines)) == normalized_title:
        return lines
    if (
        allow_leading_chapter_label
        and len(lines) >= 2
        and _is_chapter_label(lines[0])
        and _comparison_text(" ".join(lines[1:])) == normalized_title
    ):
        return lines
    return None


def paragraphs_from_text(
    text: str,
    title: str,
    *,
    allow_leading_chapter_label: bool = False,
) -> tuple[str, list[str]]:
    blocks = [block.strip() for block in re.split(r"\n\s*\n+", text.replace("\r\n", "\n"))]
    blocks = [block for block in blocks if block]
    heading = title
    normalized_title = _comparison_text(title)
    if blocks:
        first_heading_lines = _heading_lines(
            blocks[0],
            normalized_title,
            allow_leading_chapter_label,
        )
        if first_heading_lines:
            heading = "\n".join(first_heading_lines)
            blocks = blocks[1:]
        elif (
            len(blocks) >= 2
            and _comparison_text(blocks[1]) == normalized_title
        ):
            if allow_leading_chapter_label and _is_chapter_label(blocks[0]):
                heading = "\n".join(normalize_space(value) for value in blocks[:2])
                blocks = blocks[2:]
            else:
                blocks = [blocks[0], *blocks[2:]]
    return heading, blocks


def heading_markup(value: str) -> str:
    lines = [normalize_space(line) for line in value.splitlines() if normalize_space(line)]
    return "<br/>".join(html.escape(line) for line in lines)


def preserves_short_line_breaks(block: str) -> bool:
    # Reflow ordinary PDF-wrapped prose, but retain compact verse-like source blocks.
    lines = [line.strip() for line in block.splitlines() if line.strip()]
    if len(lines) < 2 or len(lines) > 12:
        return False
    lengths = [len(line) for line in lines]
    return max(lengths) <= 48 and sum(lengths) / len(lengths) <= 36


def paragraph_markup(block: str) -> str:
    if preserves_short_line_breaks(block):
        content = "<br/>".join(escape(line.strip()) for line in block.splitlines() if line.strip())
    else:
        content = escape(normalize_space(block))
    return f"    <p>{content}</p>"


def figure_markup(asset: dict, href: str) -> str:
    alt_text = escape(asset["alt_text"])
    role = asset["role"] if asset["role"] in {"illustration", "facsimile"} else "illustration"
    return (
        f'    <figure class="illustration {role}">\n'
        f'      <img src="{escape(href)}" alt="{alt_text}"/>\n'
        "    </figure>"
    )


def source_cover_markup(document: dict, language: str, asset_hrefs: list[tuple[dict, str]]) -> str:
    figures = [figure_markup(asset, href) for asset, href in asset_hrefs]
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}" lang="{escape(language)}">',
            "<head>",
            f"  <title>{escape(document['title'])}</title>",
            '  <link rel="stylesheet" type="text/css" href="../styles/book.css"/>',
            "</head>",
            "<body>",
            '  <section epub:type="titlepage" class="source-cover">',
            *figures,
            "  </section>",
            "</body>",
            "</html>",
        ]
    )


def _note_marker_pattern(note_ids: dict[str, str]) -> re.Pattern[str] | None:
    if not note_ids:
        return None
    markers = "|".join(re.escape(marker) for marker in sorted(note_ids, key=len, reverse=True))
    return re.compile(rf"(?P<marker>{markers})")


def _preceding_text_character(text: str, marker_start: int) -> str:
    index = marker_start - 1
    while index >= 0 and text[index] in '"\')]}.,;:!?—–-”’':
        index -= 1
    return text[index] if index >= 0 else ""


def _is_attached_note_marker(text: str, start: int, end: int, marker: str) -> bool:
    preceding = _preceding_text_character(text, start)
    if not preceding or preceding.isspace():
        return False
    following = text[end] if end < len(text) else ""
    if following and following.isalnum():
        return False
    if marker.isdigit():
        if preceding.isalpha():
            return True
        if preceding.isdigit() and len(marker) == 1:
            if re.search(r"""[\)\]}]["'”’.,;:!?—–-]*$""", text[:start]):
                return True
            token_start = start - 1
            while token_start > 0 and (
                text[token_start - 1].isdigit() or text[token_start - 1] in "/-"
            ):
                token_start -= 1
            return (
                re.fullmatch(r"\d{1,2}[/-]\d{1,2}[/-]\d{4}", text[token_start:start])
                is not None
            )
        return False
    return preceding.isalnum()


def _attached_note_matches(text: str, note_ids: dict[str, str]) -> list[tuple[int, int, str, str]]:
    pattern = _note_marker_pattern(note_ids)
    if pattern is None:
        return []
    matches: list[tuple[int, int, str, str]] = []
    for match in pattern.finditer(text):
        marker = match.group("marker")
        if _is_attached_note_marker(text, match.start(), match.end(), marker):
            matches.append((match.start(), match.end(), marker, note_ids[marker]))
    return matches


def _noteref_id(note_id: str, reference_counts: dict[str, int]) -> str:
    reference_counts[note_id] = reference_counts.get(note_id, 0) + 1
    suffix = "" if reference_counts[note_id] == 1 else f"-{reference_counts[note_id]}"
    return f"noteref-{note_id}{suffix}"


def note_reference_markup(
    value: str,
    note_ids: dict[str, str],
    reference_targets: dict[str, str] | None = None,
    reference_counts: dict[str, int] | None = None,
    normalize: bool = True,
    note_hrefs: dict[str, str] | None = None,
) -> str:
    text = normalize_space(value) if normalize else value
    if not note_ids:
        return escape(text)
    targets = reference_targets if reference_targets is not None else {}
    counts = reference_counts if reference_counts is not None else {}
    parts: list[str] = []
    cursor = 0
    for start, end, marker, note_id in _attached_note_matches(text, note_ids):
        parts.append(escape(text[cursor:start]))
        ref_id = _noteref_id(note_id, counts)
        targets.setdefault(note_id, ref_id)
        href = (note_hrefs or {}).get(note_id, f"#{note_id}")
        parts.append(
            f'<sup><a id="{escape(ref_id)}" epub:type="noteref" href="{escape(href)}">'
            f"{escape(marker)}</a></sup>"
        )
        cursor = end
    parts.append(escape(text[cursor:]))
    return "".join(parts)


def _apply_revision_changes(
    value: str,
    changes: list[dict],
    applied: set[str] | None = None,
) -> str:
    revised = value
    for change in changes:
        source_span = str(change.get("source_span") or "")
        revised_span = str(change.get("revised_span") or "")
        change_id = str(change.get("id") or "")
        if not source_span or source_span not in revised:
            continue
        if revised.count(source_span) != 1:
            raise RuntimeError(
                f"Revision change {change_id!r} is ambiguous inside one semantic EPUB block."
            )
        if applied is not None and change_id in applied:
            raise RuntimeError(
                f"Revision change {change_id!r} appears in more than one semantic EPUB block."
            )
        revised = revised.replace(source_span, revised_span, 1)
        if applied is not None:
            applied.add(change_id)
    return revised


def _layout_text_values(
    block: dict,
    book_root: Path,
    revision_changes: list[dict] | None = None,
    applied: set[str] | None = None,
) -> list[str]:
    lines = lines_for_block(block, book_root)
    kind = block["kind"]
    changes = revision_changes or []
    if kind in {"paragraph", "quotation", "dialogue", "note"}:
        return [_apply_revision_changes(" ".join(lines), changes, applied)]
    if kind in {"verse", "heading"}:
        return [_apply_revision_changes(line, changes, applied) for line in lines]
    return lines


def _note_reference_targets(
    blocks: list[dict],
    book_root: Path,
    note_ids: dict[str, str],
    revision_changes: list[dict],
) -> dict[str, str]:
    targets: dict[str, str] = {}
    for block in blocks:
        if not isinstance(block, dict) or block.get("kind") == "note":
            continue
        for value in _layout_text_values(block, book_root, revision_changes):
            for _, _, _, note_id in _attached_note_matches(normalize_space(value), note_ids):
                targets.setdefault(note_id, f"noteref-{note_id}")
    return targets


def semantic_block_groups(
    blocks: list[dict],
) -> list[tuple[int, list[dict]]]:
    groups: list[tuple[int, list[dict]]] = []
    for block_index, block in enumerate(blocks):
        if block.get("join_with_previous") is True:
            previous_group_index = len(groups) - 1
            while (
                previous_group_index >= 0
                and groups[previous_group_index][1]
                and all(
                    candidate.get("kind") == "note"
                    for candidate in groups[previous_group_index][1]
                )
            ):
                previous_group_index -= 1
            if (
                previous_group_index < 0
                or block.get("kind") not in {"paragraph", "quotation"}
                or groups[previous_group_index][1][-1].get("kind") != block.get("kind")
            ):
                raise RuntimeError(
                    "join_with_previous requires a preceding paragraph or quotation block "
                    "separated only by note blocks"
                )
            groups[previous_group_index][1].append(block)
            continue
        groups.append((block_index, [block]))
    return groups


def join_semantic_values(values: list[str]) -> str:
    if not values:
        return ""
    joined = values[0]
    for value in values[1:]:
        joined += ("" if joined.endswith("-") else " ") + value
    return joined


def semantic_block_markup(
    block: dict,
    book_root: Path,
    note_ids: dict[str, str],
    reference_targets: dict[str, str],
    reference_counts: dict[str, int],
    revision_changes: list[dict],
    applied_revision_ids: set[str],
    note_hrefs: dict[str, str] | None = None,
) -> str:
    kind = block["kind"]
    lines = _layout_text_values(
        block,
        book_root,
        revision_changes,
        applied_revision_ids,
    )
    if kind == "paragraph":
        return f"    <p>{note_reference_markup(' '.join(lines), note_ids, reference_targets, reference_counts, note_hrefs=note_hrefs)}</p>"
    if kind == "quotation":
        value = note_reference_markup(
            " ".join(lines),
            note_ids,
            reference_targets,
            reference_counts,
            note_hrefs=note_hrefs,
        )
        return f'    <blockquote class="quotation"><p>{value}</p></blockquote>'
    if kind == "dialogue":
        return f'    <p class="dialogue">{note_reference_markup(" ".join(lines), note_ids, reference_targets, reference_counts, note_hrefs=note_hrefs)}</p>'
    if kind == "verse":
        verse_lines = "\n".join(
            f'      <span class="verse-line">{note_reference_markup(line, note_ids, reference_targets, reference_counts, normalize=False, note_hrefs=note_hrefs)}</span>'
            for line in lines
        )
        return f'    <div class="verse">\n{verse_lines}\n    </div>'
    if kind == "heading":
        level = block["level"]
        heading_lines = "\n".join(
            f'      <span class="heading-line">{note_reference_markup(line, note_ids, reference_targets, reference_counts, normalize=False, note_hrefs=note_hrefs)}</span>'
            for line in lines
        )
        return f'    <h{level} class="source-heading">\n{heading_lines}\n    </h{level}>'
    if kind == "note":
        marker = block["marker"]
        note_id = str(block["id"])
        first_line = lines[0]
        content = re.sub(rf"^\s*{re.escape(marker)}\s+", "", first_line)
        note_lines = [content, *lines[1:]]
        marker_markup = escape(marker)
        if note_id in reference_targets:
            backlink = str(reference_targets[note_id])
            if "#" not in backlink:
                backlink = f"#{backlink}"
            marker_markup = (
                f'<a epub:type="backlink" href="{escape(backlink)}">'
                f"{escape(marker)}</a>"
            )
        return (
            f'    <aside id="{escape(note_id)}" epub:type="footnote" class="footnote">\n'
            f"      <p><sup>{marker_markup}</sup> {escape(normalize_space(' '.join(note_lines)))}</p>\n"
            "    </aside>"
        )
    raise RuntimeError(f"Unsupported EPUB layout block kind: {kind}")


def semantic_block_group_markup(
    block_group: list[dict],
    book_root: Path,
    note_ids: dict[str, str],
    reference_targets: dict[str, str],
    reference_counts: dict[str, int],
    revision_changes: list[dict],
    applied_revision_ids: set[str],
    note_hrefs: dict[str, str] | None = None,
) -> str:
    if len(block_group) == 1:
        return semantic_block_markup(
            block_group[0],
            book_root,
            note_ids,
            reference_targets,
            reference_counts,
            revision_changes,
            applied_revision_ids,
            note_hrefs,
        )
    kind = block_group[0].get("kind")
    if (
        kind not in {"paragraph", "quotation"}
        or any(block.get("kind") != kind for block in block_group)
    ):
        raise RuntimeError(
            "Only matching paragraph or quotation blocks may be joined in a semantic "
            "EPUB layout."
        )
    values: list[str] = []
    for block in block_group:
        values.extend(
            _layout_text_values(
                block,
                book_root,
                revision_changes,
                applied_revision_ids,
            )
        )
    value = note_reference_markup(
        join_semantic_values(values),
        note_ids,
        reference_targets,
        reference_counts,
        note_hrefs=note_hrefs,
    )
    if kind == "quotation":
        return f'    <blockquote class="quotation"><p>{value}</p></blockquote>'
    return f"    <p>{value}</p>"


def semantic_body_parts(
    blocks: list[dict],
    book_root: Path,
    asset_hrefs: list[tuple[dict, str]],
    revision_changes: list[dict] | None = None,
    global_note_ids: dict[str, str] | None = None,
    note_hrefs: dict[str, str] | None = None,
    global_reference_targets: dict[str, str] | None = None,
    assets_after_content: bool = False,
) -> list[str]:
    before = [figure_markup(asset, href) for asset, href in asset_hrefs if asset["placement"] != "end"]
    after = [figure_markup(asset, href) for asset, href in asset_hrefs if asset["placement"] == "end"]
    parts: list[str] = []
    inserted_before = False
    local_note_ids = {
        str(block["marker"]): str(block["id"])
        for block in blocks
        if block.get("kind") == "note"
    }
    note_ids = global_note_ids or local_note_ids
    changes = revision_changes or []
    reference_targets = (
        dict(global_reference_targets)
        if global_reference_targets is not None
        else _note_reference_targets(blocks, book_root, note_ids, changes)
    )
    reference_counts: dict[str, int] = {}
    applied_revision_ids: set[str] = set()
    for _block_index, block_group in semantic_block_groups(blocks):
        block = block_group[0]
        parts.append(
            semantic_block_group_markup(
                block_group,
                book_root,
                note_ids,
                reference_targets,
                reference_counts,
                changes,
                applied_revision_ids,
                note_hrefs,
            )
        )
        if (
            before
            and not inserted_before
            and not assets_after_content
            and block["kind"] == "heading"
        ):
            parts.extend(before)
            inserted_before = True
    if before and not inserted_before:
        if assets_after_content:
            parts.extend(before)
        else:
            parts = [*before, *parts]
    parts.extend(after)
    expected_revision_ids = {
        str(change.get("id"))
        for change in changes
        if isinstance(change, dict) and isinstance(change.get("id"), str)
    }
    if applied_revision_ids != expected_revision_ids:
        missing = sorted(expected_revision_ids - applied_revision_ids)
        raise RuntimeError(
            "Approved revision changes are not represented by the semantic EPUB layout: "
            f"{missing}"
        )
    return parts


def document_markup(
    document: dict,
    language: str,
    asset_hrefs: list[tuple[dict, str]],
    book_root: Path,
    global_note_ids: dict[str, str] | None = None,
    note_hrefs: dict[str, str] | None = None,
    global_reference_targets: dict[str, str] | None = None,
) -> str:
    if document.get("kind") == "source_cover":
        return source_cover_markup(document, language, asset_hrefs)
    layout_blocks = document.get("_layout_blocks")
    if isinstance(layout_blocks, list):
        is_title_page = document.get("kind") == "cover"
        body_parts = semantic_body_parts(
            layout_blocks,
            book_root,
            asset_hrefs,
            document.get("_revision_changes") or [],
            global_note_ids,
            note_hrefs,
            global_reference_targets,
            assets_after_content=is_title_page,
        )
        section_class = (
            ' class="semantic-layout title-page"'
            if is_title_page
            else ' class="semantic-layout"'
        )
    else:
        text = document["_text_path"].read_text(encoding="utf-8")
        heading, paragraphs = paragraphs_from_text(
            text,
            str(document["title"]),
            allow_leading_chapter_label=document.get("kind") == "chapter",
        )
        before = [figure_markup(asset, href) for asset, href in asset_hrefs if asset["placement"] != "end"]
        after = [figure_markup(asset, href) for asset, href in asset_hrefs if asset["placement"] == "end"]
        body_parts = [f"    <h1>{heading_markup(heading)}</h1>", *before]
        body_parts.extend(paragraph_markup(paragraph) for paragraph in paragraphs)
        body_parts.extend(after)
        section_class = ' class="legacy-layout"'
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}" lang="{escape(language)}">',
            "<head>",
            f"  <title>{escape(document['title'])}</title>",
            '  <link rel="stylesheet" type="text/css" href="../styles/book.css"/>',
            "</head>",
            "<body>",
            f"  <section{section_class}>",
            *body_parts,
            "  </section>",
            "</body>",
            "</html>",
        ]
    )


def cover_markup(book: dict, language: str) -> str:
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}" lang="{escape(language)}">',
            "<head>",
            f"  <title>{escape(book['title'])}</title>",
            '  <link rel="stylesheet" type="text/css" href="../styles/book.css"/>',
            "</head>",
            '<body class="cover-page">',
            '  <section epub:type="cover" class="editorial-cover">',
            f'    <img src="../{COVER_IMAGE_PATH}" alt="{escape(cover_alt_text(book))}"/>',
            "  </section>",
            "</body>",
            "</html>",
        ]
    )


def nav_markup(
    title: str,
    language: str,
    document_hrefs: list[tuple[dict, str]],
    cover_href: str | None,
) -> str:
    items = [
        f'      <li><a href="{escape(href)}">{escape(document["title"])}</a></li>'
        for document, href in document_hrefs
        if document.get("kind") not in {"cover", "source_cover"}
    ]
    body_document = next(
        (
            (document, href)
            for document, href in document_hrefs
            if document.get("kind") not in {"cover", "source_cover"}
        ),
        document_hrefs[0] if document_hrefs else None,
    )
    landmarks: list[str] = []
    if cover_href:
        landmarks.append(f'      <li><a epub:type="cover" href="{escape(cover_href)}">Cover</a></li>')
    if body_document:
        landmarks.append(
            f'      <li><a epub:type="bodymatter" href="{escape(body_document[1])}">Body</a></li>'
        )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            f'<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="{escape(language)}" lang="{escape(language)}">',
            "<head>",
            f"  <title>{escape(title)}</title>",
            '  <link rel="stylesheet" type="text/css" href="styles/book.css"/>',
            "</head>",
            "<body>",
            '  <nav epub:type="toc" id="toc">',
            "    <h1>Sumário</h1>",
            "    <ol>",
            *items,
            "    </ol>",
            "  </nav>",
            '  <nav epub:type="landmarks" hidden="hidden">',
            "    <h2>Landmarks</h2>",
            "    <ol>",
            *landmarks,
            "    </ol>",
            "  </nav>",
            "</body>",
            "</html>",
        ]
    )


def stylesheet(visual_profile: dict | None) -> str:
    return profile_stylesheet(visual_profile)


def container_xml() -> str:
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">',
            "  <rootfiles>",
            '    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>',
            "  </rootfiles>",
            "</container>",
        ]
    )


def opf_markup(
    book: dict,
    language: str,
    book_id: str,
    document_hrefs: list[tuple[dict, str]],
    image_hrefs: list[tuple[dict, str]],
    visual_profile: dict | None,
    presentation_resources: list[object],
) -> str:
    title = str(book.get("title") or "Untitled")
    author = str(book.get("author") or "").strip()
    manifest = [
        '    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
        '    <item id="style" href="styles/book.css" media-type="text/css"/>',
    ]
    if visual_profile:
        manifest.extend(
            [
                f'    <item id="cover-page" href="{COVER_DOCUMENT_PATH}" media-type="application/xhtml+xml"/>',
                f'    <item id="editorial-cover" href="{COVER_IMAGE_PATH}" media-type="image/jpeg" properties="cover-image"/>',
            ]
        )
        for resource in presentation_resources:
            manifest.append(
                f'    <item id="{escape(resource.identifier)}" href="{escape(resource.epub_path)}" media-type="{escape(resource.media_type)}"/>'
            )
    for index, (_, href) in enumerate(document_hrefs, start=1):
        manifest.append(f'    <item id="doc-{index}" href="{escape(href)}" media-type="application/xhtml+xml"/>')
    cover_asset = (
        None
        if visual_profile
        else next((asset for asset, _ in image_hrefs if asset["role"] in {"cover", "cover_candidate"}), None)
    )
    for index, (asset, href) in enumerate(image_hrefs, start=1):
        cover_property = ' properties="cover-image"' if asset is cover_asset else ""
        manifest.append(
            f'    <item id="image-{index}" href="{escape(href)}" media-type="{escape(asset["media_type"])}"{cover_property}/>'
        )
    spine = []
    if visual_profile:
        spine.append('    <itemref idref="cover-page"/>')
    spine.extend(f'    <itemref idref="doc-{index}"/>' for index, _ in enumerate(document_hrefs, start=1))
    metadata = [
        '  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
        f'    <dc:identifier id="bookid">{escape(book_id)}</dc:identifier>',
        f"    <dc:title>{escape(title)}</dc:title>",
        f"    <dc:language>{escape(language)}</dc:language>",
    ]
    if author:
        metadata.append(f"    <dc:creator>{escape(author)}</dc:creator>")
    if isinstance(book.get("publication_year"), int):
        metadata.append(f"    <dc:date>{book['publication_year']}</dc:date>")
    if visual_profile:
        metadata.append('    <meta name="cover" content="editorial-cover"/>')
    metadata.extend(
        [
            f'    <meta property="dcterms:modified">{EPUB_MODIFIED}</meta>',
            "  </metadata>",
        ]
    )
    return "\n".join(
        [
            '<?xml version="1.0" encoding="utf-8"?>',
            '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">',
            *metadata,
            "  <manifest>",
            *manifest,
            "  </manifest>",
            "  <spine>",
            *spine,
            "  </spine>",
            "</package>",
        ]
    )


def zip_info(name: str, compress_type: int) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
    info.compress_type = compress_type
    info.create_system = 0
    info.external_attr = 0
    return info


def zip_writestr(
    archive: zipfile.ZipFile,
    name: str,
    data: str | bytes,
    compress_type: int = zipfile.ZIP_DEFLATED,
) -> None:
    archive.writestr(zip_info(name, compress_type), data)


def temporary_output_path(output: Path, suffix: str) -> Path:
    return output.with_name(f".{output.name}.{os.getpid()}.tmp{suffix}")


def write_epub(
    output: Path,
    book_root: Path,
    book: dict,
    language: str,
    text_edition: str,
    documents: list[dict],
    selected_assets_by_document: dict[str, list[dict]],
    visual_profile: dict | None,
) -> tuple[list[dict], dict | None]:
    documents = reader_documents(documents, text_edition, book_root)
    document_hrefs: list[tuple[dict, str]] = []
    for index, document in enumerate(documents, start=1):
        href = f"text/{index:03d}-{safe_segment(str(document['id']), f'document-{index:03d}')}.xhtml"
        document_hrefs.append((document, href))
    document_href_by_id = {
        str(document["id"]): href for document, href in document_hrefs
    }
    global_note_ids: dict[str, str] = {}
    note_document_hrefs: dict[str, str] = {}
    for document, href in document_hrefs:
        blocks = document.get("_layout_blocks")
        if not isinstance(blocks, list):
            continue
        for block in blocks:
            if not isinstance(block, dict) or block.get("kind") != "note":
                continue
            marker = str(block["marker"])
            note_id = str(block["id"])
            if marker in global_note_ids or note_id in note_document_hrefs:
                raise RuntimeError("Semantic EPUB note markers and identifiers must be globally unique.")
            global_note_ids[marker] = note_id
            note_document_hrefs[note_id] = href
    global_reference_targets: dict[str, str] = {}
    for document, href in document_hrefs:
        blocks = document.get("_layout_blocks")
        if not isinstance(blocks, list):
            continue
        changes = document.get("_revision_changes") or []
        for block in blocks:
            if not isinstance(block, dict) or block.get("kind") == "note":
                continue
            for value in _layout_text_values(block, book_root, changes):
                for _, _, _, note_id in _attached_note_matches(
                    normalize_space(value),
                    global_note_ids,
                ):
                    global_reference_targets.setdefault(
                        note_id,
                        f"{PurePosixPath(href).name}#noteref-{note_id}",
                    )
    image_hrefs: list[tuple[dict, str]] = []
    seen_asset_ids: set[str] = set()
    for document in documents:
        for asset in selected_assets_by_document[document["id"]]:
            if asset["id"] in seen_asset_ids:
                continue
            seen_asset_ids.add(asset["id"])
            suffix = asset["path"].suffix.lower() or ".img"
            image_hrefs.append((asset, f"images/{safe_segment(asset['id'], 'asset')}{suffix}"))
    image_href_by_id = {asset["id"]: href for asset, href in image_hrefs}
    book_id = (
        "urn:uuid:"
        f"{hashlib.sha256(json.dumps({'book': book, 'text_edition': text_edition}, sort_keys=True).encode('utf-8')).hexdigest()[:32]}"
    )
    presentation_resources = profile_resources(visual_profile)
    cover_bytes = cover_image(book) if visual_profile else None
    output.parent.mkdir(parents=True, exist_ok=True)
    staged = temporary_output_path(output, ".epub")
    if staged.exists():
        raise RuntimeError(f"Temporary EPUB export already exists: {staged}")
    try:
        with zipfile.ZipFile(staged, "w") as archive:
            zip_writestr(
                archive,
                "mimetype",
                "application/epub+zip",
                zipfile.ZIP_STORED,
            )
            zip_writestr(
                archive,
                "META-INF/container.xml",
                container_xml(),
            )
            zip_writestr(
                archive,
                "OEBPS/styles/book.css",
                stylesheet(visual_profile),
            )
            if cover_bytes is not None:
                zip_writestr(
                    archive,
                    f"OEBPS/{COVER_DOCUMENT_PATH}",
                    cover_markup(book, language),
                )
                zip_writestr(
                    archive,
                    f"OEBPS/{COVER_IMAGE_PATH}",
                    cover_bytes,
                )
            for resource in presentation_resources:
                zip_writestr(
                    archive,
                    f"OEBPS/{resource.epub_path}",
                    resource.source_path.read_bytes(),
                )
            for document, href in document_hrefs:
                assets = selected_assets_by_document[document["id"]]
                references = [(asset, f"../{image_href_by_id[asset['id']]}") for asset in assets]
                current_name = PurePosixPath(href).name
                note_hrefs = {
                    note_id: (
                        f"#{note_id}"
                        if PurePosixPath(target_href).name == current_name
                        else f"{PurePosixPath(target_href).name}#{note_id}"
                    )
                    for note_id, target_href in note_document_hrefs.items()
                }
                reference_targets = {
                    note_id: (
                        target.split("#", 1)[1]
                        if target.startswith(f"{current_name}#")
                        else target
                    )
                    for note_id, target in global_reference_targets.items()
                }
                zip_writestr(
                    archive,
                    f"OEBPS/{href}",
                    document_markup(
                        document,
                        language,
                        references,
                        book_root,
                        global_note_ids,
                        note_hrefs,
                        reference_targets,
                    ),
                )
            zip_writestr(
                archive,
                "OEBPS/nav.xhtml",
                nav_markup(
                    str(book.get("title") or "Untitled"),
                    language,
                    document_hrefs,
                    COVER_DOCUMENT_PATH if visual_profile else None,
                ),
            )
            zip_writestr(
                archive,
                "OEBPS/content.opf",
                opf_markup(
                    book,
                    language,
                    book_id,
                    document_hrefs,
                    image_hrefs,
                    visual_profile,
                    presentation_resources,
                ),
            )
            for asset, href in image_hrefs:
                zip_writestr(archive, f"OEBPS/{href}", asset["path"].read_bytes())
        os.replace(staged, output)
    except Exception:
        if staged.exists():
            staged.unlink()
        raise
    assets = [
        {
            "id": asset["id"],
            "sha256": asset["sha256"],
            "media_type": asset["media_type"],
            "epub_path": f"OEBPS/{href}",
        }
        for asset, href in image_hrefs
    ]
    presentation = None
    if cover_bytes is not None:
        presentation = {
            "name": visual_profile["name"],
            "cover": {
                "epub_path": f"OEBPS/{COVER_IMAGE_PATH}",
                "sha256": sha256_bytes(cover_bytes),
                "media_type": "image/jpeg",
            },
            "resources": [
                {
                    "id": resource.identifier,
                    "epub_path": f"OEBPS/{resource.epub_path}",
                    "sha256": resource.sha256,
                    "media_type": resource.media_type,
                }
                for resource in presentation_resources
            ],
        }
    return assets, presentation


def epub_sidecar_assets(
    documents: list[dict],
    selected_assets_by_document: dict[str, list[dict]],
) -> list[dict]:
    image_hrefs: list[tuple[dict, str]] = []
    seen_asset_ids: set[str] = set()
    for document in documents:
        for asset in selected_assets_by_document[document["id"]]:
            if asset["id"] in seen_asset_ids:
                continue
            seen_asset_ids.add(asset["id"])
            suffix = asset["path"].suffix.lower() or ".img"
            image_hrefs.append((asset, f"images/{safe_segment(asset['id'], 'asset')}{suffix}"))
    return [
        {
            "id": asset["id"],
            "sha256": asset["sha256"],
            "media_type": asset["media_type"],
            "epub_path": f"OEBPS/{href}",
        }
        for asset, href in image_hrefs
    ]


def epub_sidecar_presentation(book: dict, visual_profile: dict | None) -> dict | None:
    if visual_profile is None:
        return None
    presentation_resources = profile_resources(visual_profile)
    cover_bytes = cover_image(book)
    return {
        "name": visual_profile["name"],
        "cover": {
            "epub_path": f"OEBPS/{COVER_IMAGE_PATH}",
            "sha256": sha256_bytes(cover_bytes),
            "media_type": "image/jpeg",
        },
        "resources": [
            {
                "id": resource.identifier,
                "epub_path": f"OEBPS/{resource.epub_path}",
                "sha256": resource.sha256,
                "media_type": resource.media_type,
            }
            for resource in presentation_resources
        ],
    }


def epub_sidecar_data(
    output: Path,
    book_root: Path,
    fingerprint: dict,
    image_edition: str,
    text_edition: str,
    epub_manifest: dict,
    map_path: Path,
    ledger_path: Path,
    assets_manifest_path: Path,
    documents: list[dict],
    selected_assets_by_document: dict[str, list[dict]],
    layout: dict | None,
    presentation: dict | None,
) -> dict:
    documents = published_documents(documents, text_edition)
    sidecar_data = {
        "schema_version": "1.0",
        "epub_path": relative_to_book(book_root, output),
        "epub_sha256": sha256_file(output),
        "input_fingerprint": fingerprint,
        "image_edition": image_edition,
        "text_edition": text_edition,
        "language": epub_manifest["language"],
        "book_map_sha256": sha256_file(map_path),
        "text_ledger_sha256": sha256_file(ledger_path),
        "assets_manifest_sha256": sha256_file(assets_manifest_path),
        "assets": epub_sidecar_assets(documents, selected_assets_by_document),
    }
    if text_edition == "translated-pt-br":
        sidecar_data["source_language"] = epub_manifest["source_language"]
        sidecar_data["translation_ledger_sha256"] = epub_manifest["translation_ledger_sha256"]
    elif text_edition == "revised-pt-br":
        sidecar_data["revision_ledger_sha256"] = epub_manifest[
            "revision_ledger_sha256"
        ]
    elif text_edition == "fluid-pt-br":
        for key in (
            "base_edition",
            "base_ledger_sha256",
            "fluid_style_sha256",
            "fluid_edition_ledger_sha256",
            "profile",
        ):
            sidecar_data[key] = epub_manifest[key]
        if epub_manifest["base_edition"] == "translated-pt-br":
            sidecar_data["source_language"] = epub_manifest["source_language"]
            sidecar_data["translation_ledger_sha256"] = epub_manifest[
                "translation_ledger_sha256"
            ]
    if isinstance(layout, dict):
        sidecar_data["layout"] = epub_manifest["layout"]
    if presentation:
        sidecar_data["visual_profile"] = presentation
    return sidecar_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a semantic EPUB from verified Audiobook Codex source artifacts.")
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--epub-manifest", type=Path)
    parser.add_argument("--assets-manifest", type=Path)
    parser.add_argument("--image-edition", choices=sorted(IMAGE_EDITIONS), default="original")
    parser.add_argument("--text-edition", choices=sorted(TEXT_EDITIONS), default="original")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        book_root = resolve_book_paths(args.book_root).assembly_root
        epub_manifest_path = (
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
            book_map,
            ledger,
            assets_manifest,
            epub_manifest,
            map_path,
            ledger_path,
            translation_ledger,
            revision_ledger,
            _fluid_style,
            fluid_ledger,
            layout,
        ) = load_export_context(
            book_root,
            epub_manifest_path,
            assets_manifest_path,
            args.text_edition,
        )
        documents, asset_by_id = validate_documents(
            book_root,
            epub_manifest,
            assets_manifest,
            ledger,
            args.text_edition,
            translation_ledger,
            revision_ledger,
            fluid_ledger,
            layout,
        )
        selected_assets_by_document = {
            document["id"]: [
                selected_asset(asset_by_id[asset_id], book_root, args.image_edition)
                for asset_id in document["asset_ids"]
            ]
            for document in documents
        }
        visual_profile = normalize_visual_profile(epub_manifest.get("visual_profile"))
        book = epub_manifest.get("book") if isinstance(epub_manifest.get("book"), dict) else {}
        if not require_text(book.get("title")):
            source_book = book_map.get("book") if isinstance(book_map.get("book"), dict) else {}
            book = {**source_book, **book}
        book = {
            "title": str(book.get("title") or "Untitled"),
            "subtitle": str(book.get("subtitle") or ""),
            "author": str(book.get("author") or ""),
            "publication_year": book.get("publication_year"),
            "publication_place": str(book.get("publication_place") or ""),
        }
        if args.text_edition == "original":
            edition_label = "fiel" if args.image_edition == "original" else "restaurada"
        elif args.text_edition == "revised-pt-br":
            edition_label = "revisada" if args.image_edition == "original" else "revisada-restaurada"
        elif args.text_edition == "fluid-pt-br":
            edition_label = "fluida" if args.image_edition == "original" else "fluida-restaurada"
        else:
            edition_label = "pt-br" if args.image_edition == "original" else "pt-br-restaurada"
        if visual_profile:
            edition_label = f"{edition_label}-classico"
        default_name = (
            f"{safe_segment(book['title'], 'book')}.epub"
            if uses_unsuffixed_fluid_export_name(book_root, args.text_edition)
            else f"{safe_segment(book['title'], 'book')}-{edition_label}.epub"
        )
        output = resolve_export_output(
            book_root,
            args.output,
            default_name,
        )
        fingerprint = export_input_fingerprint(
            export_fingerprint_payload(
                "epub",
                book_root,
                epub_manifest_path,
                assets_manifest_path,
                map_path,
                ledger_path,
                epub_manifest,
                book,
                str(epub_manifest["language"]),
                args.text_edition,
                args.image_edition,
                documents,
                selected_assets_by_document,
                visual_profile,
            )
        )
        sidecar = output.with_suffix(".epub.json")
        expected_sidecar = (
            epub_sidecar_data(
                output,
                book_root,
                fingerprint,
                args.image_edition,
                args.text_edition,
                epub_manifest,
                map_path,
                ledger_path,
                assets_manifest_path,
                documents,
                selected_assets_by_document,
                layout,
                epub_sidecar_presentation(book, visual_profile),
            )
            if output.is_file()
            else None
        )
        if cached_export_is_current(
            output,
            sidecar,
            book_root,
            "epub_path",
            "epub_sha256",
            fingerprint,
            expected_sidecar,
        ):
            print(f"Up to date {output}")
            print(f"Up to date {sidecar}")
            return
        _assets, presentation = write_epub(
            output,
            book_root,
            book,
            str(epub_manifest["language"]),
            args.text_edition,
            documents,
            selected_assets_by_document,
            visual_profile,
        )
        sidecar_data = epub_sidecar_data(
            output,
            book_root,
            fingerprint,
            args.image_edition,
            args.text_edition,
            epub_manifest,
            map_path,
            ledger_path,
            assets_manifest_path,
            documents,
            selected_assets_by_document,
            layout,
            presentation,
        )
        write_json(sidecar, sidecar_data)
    except RuntimeError as error:
        print(f"Cannot export EPUB: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Created {output}")
    print(f"Created {sidecar}")


if __name__ == "__main__":
    main()
