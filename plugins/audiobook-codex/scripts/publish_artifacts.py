from __future__ import annotations

import argparse
import copy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from book_transaction_lock import BookTransactionLock, file_generation
from book_layout import BookPaths, resolve_book_paths
from epub_layout import layout_descriptor
from path_safety import resolve_under
from publication_selection import (
    require_narrator_base,
    require_text_edition,
    uses_unsuffixed_fluid_export_name,
)
from recoverable_file_transaction import StagedReplacement
from recoverable_file_transaction import commit_recoverable_transaction
from recoverable_file_transaction import recover_pending_transactions
from validate_narrator_lineage import validate_lineage
from verify_fluid_edition_ledger import FLUID_PROFILE
from verify_fluid_edition_ledger import TARGET_LANGUAGE as FLUID_LANGUAGE
from verify_fluid_edition_ledger import verify as verify_fluid_edition_ledger
from verify_revision_ledger import TARGET_LANGUAGE as REVISION_LANGUAGE
from verify_translation_ledger import TARGET_LANGUAGE as TRANSLATION_LANGUAGE


CHATTERBOX_ENGINE = "chatterbox-multilingual-v3-pt-br"
EPUB_TEXT_EDITIONS = {
    "fluid-pt-br",
    "original",
    "revised-pt-br",
    "translated-pt-br",
}
EPUB_IMAGE_EDITIONS = {"original", "approved-restored"}
PDF_TEXT_EDITIONS = EPUB_TEXT_EDITIONS
PDF_IMAGE_EDITIONS = EPUB_IMAGE_EDITIONS
SUPPLEMENTAL_TEXT_EDITIONS = {"fluid-pt-br"}
CANONICAL_PT_BR_LANGUAGES = {
    "fluid-pt-br": FLUID_LANGUAGE,
    "revised-pt-br": REVISION_LANGUAGE,
    "translated-pt-br": TRANSLATION_LANGUAGE,
}


@dataclass(frozen=True)
class Publication:
    kind: str
    source: Path
    destination: Path
    record: dict
    edition_key: str | None = None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def require_under(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError(f"{label} must remain under {root}: {path}") from error


def validate_fluid_publication_sidecar(book_root: Path, sidecar: dict) -> None:
    metadata_root = book_root / "metadata"
    map_path = metadata_root / "book-map.json"
    source_ledger_path = metadata_root / "text-ledger.json"
    fluid_style_path = metadata_root / "fluid-style.json"
    fluid_ledger_path = metadata_root / "fluid-edition-ledger.json"
    book_map = load_json(map_path)
    source_ledger = load_json(source_ledger_path)
    fluid_style = load_json(fluid_style_path)
    fluid_ledger = load_json(fluid_ledger_path)
    if not all(
        isinstance(value, dict)
        for value in (book_map, source_ledger, fluid_style, fluid_ledger)
    ):
        raise RuntimeError(
            "Fluid publication metadata must be JSON objects"
        )

    translation_ledger = None
    translation_path = None
    if fluid_ledger.get("base_edition") == "translated-pt-br":
        translation_path = metadata_root / "translation-ledger.json"
        translation_ledger = load_json(translation_path)
        if not isinstance(translation_ledger, dict):
            raise RuntimeError(
                "Translated fluid publication requires translation-ledger.json"
            )
    errors = verify_fluid_edition_ledger(
        book_map,
        sha256_file(map_path),
        source_ledger,
        sha256_file(source_ledger_path),
        translation_ledger,
        sha256_file(translation_path) if translation_path is not None else None,
        fluid_style,
        sha256_file(fluid_style_path),
        fluid_ledger,
        book_root / "text",
    )
    if errors:
        raise RuntimeError("; ".join(errors))

    expected = {
        "profile": FLUID_PROFILE,
        "base_edition": fluid_ledger["base_edition"],
        "base_ledger_sha256": fluid_ledger["base_ledger_sha256"],
        "fluid_style_sha256": sha256_file(fluid_style_path),
        "fluid_edition_ledger_sha256": sha256_file(fluid_ledger_path),
    }
    for key, value in expected.items():
        if sidecar.get(key) != value:
            raise RuntimeError(
                f"Fluid publication sidecar {key} does not match canonical metadata"
            )
    if fluid_ledger["base_edition"] == "translated-pt-br":
        if sidecar.get("translation_ledger_sha256") != sha256_file(
            translation_path
        ):
            raise RuntimeError(
                "Fluid publication sidecar translation ledger hash is invalid"
            )
        if sidecar.get("source_language") != translation_ledger.get(
            "source_language"
        ):
            raise RuntimeError(
                "Fluid publication sidecar source language is invalid"
            )


def publication_record(paths: BookPaths, source: Path, destination: Path) -> dict:
    source_hash = sha256_file(source)
    return {
        "path": paths.relative_to_public(destination),
        "path_root": "book",
        "sha256": source_hash,
        "source_path": paths.relative_to_assembly(source),
        "source_path_root": "assembly",
        "source_sha256": source_hash,
        "published_at": iso_now(),
    }


def same_publication_identity(current: dict, existing: object) -> bool:
    if not isinstance(existing, dict):
        return False
    current_identity = dict(current)
    existing_identity = dict(existing)
    current_identity.pop("published_at", None)
    existing_identity.pop("published_at", None)
    return current_identity == existing_identity


def preserve_existing_publication_timestamp(
    publication: Publication,
    existing: object,
) -> None:
    if (
        same_publication_identity(publication.record, existing)
        and isinstance(existing, dict)
        and isinstance(existing.get("published_at"), str)
    ):
        publication.record["published_at"] = existing["published_at"]


def existing_publication_record(
    artifacts: dict,
    publication: Publication,
) -> object:
    if publication.kind in {"audio", "epub", "pdf"}:
        if publication.kind == "audio":
            editions = artifacts.get("audio_editions")
            if isinstance(editions, dict) and publication.edition_key is not None:
                edition_record = editions.get(publication.edition_key)
                if same_publication_identity(publication.record, edition_record):
                    return edition_record
            if publication.record.get("text_edition") in SUPPLEMENTAL_TEXT_EDITIONS:
                return None
            return artifacts.get("audio")
        editions = artifacts.get(f"{publication.kind}_editions")
        if isinstance(editions, dict) and publication.edition_key is not None:
            edition_record = editions.get(publication.edition_key)
            if same_publication_identity(publication.record, edition_record):
                return edition_record
        if publication.record.get("text_edition") not in SUPPLEMENTAL_TEXT_EDITIONS:
            return artifacts.get(publication.kind)
        return None
    return artifacts.get(publication.kind)


def publication_destination(
    paths: BookPaths,
    source: Path,
    kind: str,
    text_edition: str | None = None,
) -> Path:
    suffix = f".{kind}"
    canonical = paths.public_root / f"{paths.public_root.name}{suffix}"
    if text_edition not in SUPPLEMENTAL_TEXT_EDITIONS:
        return canonical
    destination = paths.public_root / source.name
    if destination.name.casefold() == canonical.name.casefold():
        raise RuntimeError(
            f"Supplemental {kind.upper()} filename must remain distinct from "
            f"the faithful publication: {source.name}"
        )
    return destination


def publication_paths_from_artifacts(artifacts: object) -> set[str]:
    if not isinstance(artifacts, dict):
        return set()
    paths: set[str] = set()
    for map_key, suffix in (
        ("audio_editions", ".mp3"),
        ("epub_editions", ".epub"),
        ("pdf_editions", ".pdf"),
    ):
        editions = artifacts.get(map_key)
        if not isinstance(editions, dict):
            continue
        for edition_key, record in editions.items():
            if (
                not isinstance(edition_key, str)
                or (
                    map_key == "audio_editions"
                    and edition_key != "fluid-pt-br"
                )
                or (
                    map_key != "audio_editions"
                    and not edition_key.startswith("fluid-pt-br:")
                )
                or not isinstance(record, dict)
                or record.get("text_edition") != "fluid-pt-br"
                or (
                    map_key != "audio_editions"
                    and record.get("image_edition") != edition_key.split(":", 1)[1]
                )
                or record.get("path_root") != "book"
            ):
                continue
            raw_path = record.get("path")
            if not isinstance(raw_path, str) or not raw_path:
                continue
            path = Path(raw_path)
            if path.name == raw_path and path.suffix.casefold() == suffix:
                paths.add(raw_path)
    return paths


PAIR_IDENTITY_KEYS = (
    "text_edition",
    "image_edition",
    "language",
    "source_language",
    "book_map_sha256",
    "text_ledger_sha256",
    "assets_manifest_sha256",
    "translation_ledger_sha256",
    "revision_ledger_sha256",
    "base_edition",
    "base_ledger_sha256",
    "fluid_style_sha256",
    "fluid_edition_ledger_sha256",
    "profile",
    "layout",
)


def require_sidecar_value(sidecar: dict, key: str, expected: object, label: str) -> None:
    if sidecar.get(key) != expected:
        raise RuntimeError(f"{label} sidecar {key} does not match current canonical metadata")


def require_manifest_value(manifest: dict, key: str, expected: object, label: str) -> None:
    if manifest.get(key) != expected:
        raise RuntimeError(f"{label} manifest {key} does not match current canonical metadata")


def require_same_optional_value(
    left: dict,
    right: dict,
    key: str,
    left_label: str,
    right_label: str,
) -> None:
    if (key in left) != (key in right) or left.get(key) != right.get(key):
        raise RuntimeError(
            f"{left_label} {key} must exactly match {right_label}, including presence"
        )


def canonical_layout_relative_path(text_edition: str) -> str:
    if text_edition == "fluid-pt-br":
        return "metadata/epub-layout.fluid.json"
    if text_edition == "translated-pt-br":
        return "metadata/epub-layout.pt-br.json"
    return "metadata/epub-layout.json"


def validate_manifest_layout_descriptor(
    book_root: Path,
    manifest: dict,
    text_edition: str,
    label: str,
) -> None:
    layout_required = text_edition in CANONICAL_PT_BR_LANGUAGES
    if "layout" not in manifest:
        if layout_required:
            raise RuntimeError(f"{label} manifest layout is required for {text_edition}")
        return
    layout = manifest["layout"]
    if not isinstance(layout, dict):
        raise RuntimeError(f"{label} manifest layout must be a JSON object")
    if layout.get("mode") != "semantic":
        raise RuntimeError(f"{label} manifest layout mode must be semantic")
    expected_relative = canonical_layout_relative_path(text_edition)
    if layout.get("path") != expected_relative:
        raise RuntimeError(
            f"{label} manifest layout path must be {expected_relative}"
        )
    layout_path = book_root / expected_relative
    if not layout_path.is_file():
        raise RuntimeError(f"{label} manifest layout is missing: {layout_path}")
    expected = layout_descriptor(book_root, layout_path)
    if layout.get("sha256") != expected["sha256"]:
        raise RuntimeError(
            f"{label} manifest layout SHA-256 does not match current canonical metadata"
        )


def canonical_epub_manifest_path(book_root: Path, text_edition: str) -> Path:
    if text_edition == "fluid-pt-br":
        filename = "epub-manifest.fluid.json"
    elif text_edition == "translated-pt-br":
        filename = "epub-manifest.pt-br.json"
    elif text_edition == "revised-pt-br":
        filename = "epub-manifest.revised.json"
    else:
        filename = "epub-manifest.json"
    return book_root / "metadata" / filename


def validate_reader_sidecar_lineage(
    book_root: Path,
    sidecar: dict,
    text_edition: str,
    label: str,
) -> None:
    metadata_root = book_root / "metadata"
    map_path = metadata_root / "book-map.json"
    ledger_path = metadata_root / "text-ledger.json"
    assets_manifest_path = metadata_root / "assets-manifest.json"
    epub_manifest_path = canonical_epub_manifest_path(book_root, text_edition)
    epub_manifest = load_json(epub_manifest_path)
    if not isinstance(epub_manifest, dict):
        raise RuntimeError(f"{epub_manifest_path.name} must be a JSON object")
    for key, path in (
        ("book_map_sha256", map_path),
        ("text_ledger_sha256", ledger_path),
        ("assets_manifest_sha256", assets_manifest_path),
    ):
        expected = sha256_file(path)
        require_manifest_value(epub_manifest, key, expected, label)
        require_sidecar_value(sidecar, key, expected, label)
    if not isinstance(sidecar.get("language"), str) or not sidecar["language"]:
        raise RuntimeError(f"{label} sidecar language is required")
    if not isinstance(epub_manifest.get("language"), str) or not epub_manifest["language"]:
        raise RuntimeError(f"{label} manifest language is required")
    require_same_optional_value(sidecar, epub_manifest, "language", f"{label} sidecar", "manifest")
    expected_language = CANONICAL_PT_BR_LANGUAGES.get(text_edition)
    if expected_language is not None:
        require_manifest_value(epub_manifest, "language", expected_language, label)
        require_sidecar_value(sidecar, "language", expected_language, label)
    manifest_text_edition = epub_manifest.get("text_edition", "original")
    if manifest_text_edition != text_edition:
        raise RuntimeError(
            f"{label} manifest text_edition does not match current canonical metadata"
        )
    require_same_optional_value(sidecar, epub_manifest, "layout", f"{label} sidecar", "manifest")
    validate_manifest_layout_descriptor(book_root, epub_manifest, text_edition, label)

    if text_edition == "translated-pt-br":
        translation_path = metadata_root / "translation-ledger.json"
        translation_ledger = load_json(translation_path)
        if not isinstance(translation_ledger, dict):
            raise RuntimeError("translation-ledger.json must be a JSON object")
        require_manifest_value(
            epub_manifest,
            "translation_ledger_sha256",
            sha256_file(translation_path),
            label,
        )
        require_sidecar_value(
            sidecar,
            "translation_ledger_sha256",
            sha256_file(translation_path),
            label,
        )
        require_manifest_value(
            epub_manifest,
            "source_language",
            translation_ledger.get("source_language"),
            label,
        )
        require_sidecar_value(
            sidecar,
            "source_language",
            translation_ledger.get("source_language"),
            label,
        )
    elif text_edition == "revised-pt-br":
        revision_path = metadata_root / "revision-ledger.json"
        require_manifest_value(
            epub_manifest,
            "revision_ledger_sha256",
            sha256_file(revision_path),
            label,
        )
        require_sidecar_value(
            sidecar,
            "revision_ledger_sha256",
            sha256_file(revision_path),
            label,
        )
    elif text_edition == "fluid-pt-br":
        fluid_style_path = metadata_root / "fluid-style.json"
        fluid_ledger_path = metadata_root / "fluid-edition-ledger.json"
        fluid_ledger = load_json(fluid_ledger_path)
        if not isinstance(fluid_ledger, dict):
            raise RuntimeError("fluid-edition-ledger.json must be a JSON object")
        expected = {
            "profile": FLUID_PROFILE,
            "base_edition": fluid_ledger.get("base_edition"),
            "base_ledger_sha256": fluid_ledger.get("base_ledger_sha256"),
            "fluid_style_sha256": sha256_file(fluid_style_path),
            "fluid_edition_ledger_sha256": sha256_file(fluid_ledger_path),
        }
        for key, value in expected.items():
            require_manifest_value(epub_manifest, key, value, label)
            require_sidecar_value(sidecar, key, value, label)
        if fluid_ledger.get("base_edition") == "translated-pt-br":
            translation_path = metadata_root / "translation-ledger.json"
            translation_ledger = load_json(translation_path)
            if not isinstance(translation_ledger, dict):
                raise RuntimeError("translation-ledger.json must be a JSON object")
            require_manifest_value(
                epub_manifest,
                "translation_ledger_sha256",
                sha256_file(translation_path),
                label,
            )
            require_sidecar_value(
                sidecar,
                "translation_ledger_sha256",
                sha256_file(translation_path),
                label,
            )
            require_manifest_value(
                epub_manifest,
                "source_language",
                translation_ledger.get("source_language"),
                label,
            )
            require_sidecar_value(
                sidecar,
                "source_language",
                translation_ledger.get("source_language"),
                label,
            )


def reader_pair_identity(sidecar: dict) -> dict:
    return {key: sidecar[key] for key in PAIR_IDENTITY_KEYS if key in sidecar}


def reader_record_identity(record: object) -> dict | None:
    if not isinstance(record, dict):
        return None
    identity = record.get("reader_pair_identity")
    if isinstance(identity, dict):
        return copy.deepcopy(identity)
    legacy = {
        key: record[key]
        for key in ("text_edition", "image_edition")
        if key in record
    }
    return legacy or None


def identities_conflict(current: dict, existing: object) -> bool:
    existing_identity = reader_record_identity(existing)
    if existing_identity is None:
        return False
    for key, value in existing_identity.items():
        if current.get(key) != value:
            return True
    if set(PAIR_IDENTITY_KEYS).intersection(existing_identity) - set(current):
        return True
    return "reader_pair_identity" in existing and existing_identity != current


def paired_publication_records_match(left: Publication, right: Publication) -> bool:
    return (
        left.record.get("text_edition") == right.record.get("text_edition")
        and left.record.get("image_edition") == right.record.get("image_edition")
        and left.record.get("reader_pair_identity")
        == right.record.get("reader_pair_identity")
    )


def counterpart_record(
    artifacts: dict,
    publication: Publication,
) -> object:
    counterpart = "pdf" if publication.kind == "epub" else "epub"
    editions = artifacts.get(f"{counterpart}_editions")
    if isinstance(editions, dict) and publication.edition_key is not None:
        record = editions.get(publication.edition_key)
        if isinstance(record, dict):
            return record
    if publication.record.get("text_edition") not in SUPPLEMENTAL_TEXT_EDITIONS:
        return artifacts.get(counterpart)
    return None


def validate_reader_publication_pair(
    paths: BookPaths,
    artifacts: dict,
    publications: list[Publication],
) -> None:
    readers = [
        publication
        for publication in publications
        if publication.kind in {"epub", "pdf"}
    ]
    if not readers:
        return
    current_by_kind = {publication.kind: publication for publication in readers}
    if len(current_by_kind) != len(readers):
        raise RuntimeError("Reader publication accepts at most one EPUB and one PDF.")
    if len(readers) == 2:
        epub = current_by_kind.get("epub")
        pdf = current_by_kind.get("pdf")
        if epub is None or pdf is None or not paired_publication_records_match(epub, pdf):
            if any(
                publication.record.get("text_edition") in SUPPLEMENTAL_TEXT_EDITIONS
                for publication in readers
            ):
                raise RuntimeError(
                    "Fluid publication requires one matching EPUB/PDF pair with the "
                    "same text edition, image edition, and filename stem."
                )
            raise RuntimeError(
                "Reader publication requires matching EPUB/PDF text edition, "
                "image edition, and lineage fingerprint identity."
            )
        if (
            epub.record.get("text_edition") in SUPPLEMENTAL_TEXT_EDITIONS
            and epub.destination.stem != pdf.destination.stem
        ):
            raise RuntimeError(
                "Fluid publication requires one matching EPUB/PDF pair with the "
                "same text edition, image edition, and filename stem."
            )
    elif readers[0].record.get("text_edition") in SUPPLEMENTAL_TEXT_EDITIONS:
        raise RuntimeError(
            "Fluid publication requires one matching EPUB/PDF pair with the "
            "same text edition, image edition, and filename stem."
        )

    if paths.layout_kind != "new" or len(readers) != 1:
        return
    publication = readers[0]
    counterpart = counterpart_record(artifacts, publication)
    if identities_conflict(publication.record["reader_pair_identity"], counterpart):
        raise RuntimeError(
            "Refusing one-sided reader publication because the existing counterpart "
            "has a different text edition, image edition, or lineage fingerprint."
        )


def validate_audio_reader_edition(publications: list[Publication]) -> None:
    audio = [publication for publication in publications if publication.kind == "audio"]
    readers = [
        publication
        for publication in publications
        if publication.kind in {"epub", "pdf"}
    ]
    if not audio or not readers:
        return
    reader_editions = {publication.record.get("text_edition") for publication in readers}
    audio_editions = {publication.record.get("text_edition") for publication in audio}
    if len(reader_editions) != 1 or len(audio_editions) != 1:
        raise RuntimeError(
            "Audio and reader publication must select exactly one matching text edition."
        )
    if reader_editions != audio_editions:
        raise RuntimeError(
            "Audio publication text edition must match the EPUB/PDF reader pair."
        )


def validate_supplemental_publication_pair(
    publications: list[Publication],
) -> None:
    """Compatibility wrapper for callers that still import the old helper."""
    readers = [
        publication
        for publication in publications
        if publication.kind in {"epub", "pdf"}
    ]
    supplemental = [
        publication
        for publication in readers
        if publication.record.get("text_edition") in SUPPLEMENTAL_TEXT_EDITIONS
    ]
    if not supplemental:
        return
    if (
        len(readers) != 2
        or {publication.kind for publication in readers} != {"epub", "pdf"}
        or len(supplemental) != 2
        or len({publication.edition_key for publication in readers}) != 1
        or len({publication.destination.stem for publication in readers}) != 1
    ):
        raise RuntimeError(
            "Fluid publication requires one matching EPUB/PDF pair with the "
            "same text edition, image edition, and filename stem."
        )


def require_primary_publication(
    paths: BookPaths,
    artifacts: dict,
    publication: Publication,
) -> None:
    if uses_unsuffixed_fluid_export_name(
        paths.assembly_root,
        str(publication.record.get("text_edition") or ""),
    ):
        return
    kind = publication.kind
    record = artifacts.get(kind)
    canonical_name = f"{paths.public_root.name}.{kind}"
    canonical_path = paths.public_root / canonical_name
    if (
        not isinstance(record, dict)
        or record.get("path") != canonical_name
        or record.get("path_root") not in {None, "book"}
        or not canonical_path.is_file()
        or record.get("sha256") != sha256_file(canonical_path)
    ):
        raise RuntimeError(
            f"Supplemental fluid {kind.upper()} publication requires the current "
            f"faithful {canonical_name} publication."
        )


def require_real_audio_manifest(book_root: Path, source: Path) -> tuple[Path, dict]:
    manifest_path = book_root / "metadata" / "audio-manifest.json"
    manifest = load_json(manifest_path)
    if not isinstance(manifest, dict):
        raise RuntimeError("audio-manifest.json must be a JSON object")
    if manifest.get("mock") is True or manifest.get("render_mode") != "real":
        raise RuntimeError("Refusing to publish mock or non-final audio")
    if manifest.get("engine") != CHATTERBOX_ENGINE:
        raise RuntimeError("Refusing audio not rendered by Chatterbox PT-BR")
    require_under(source, book_root / "audio", "Audio source")
    try:
        source.relative_to((book_root / "audio" / "mock").resolve())
    except ValueError:
        pass
    else:
        raise RuntimeError("Refusing to publish audio from audio/mock")
    if (
        manifest.get("final_audio")
        != source.resolve().relative_to(book_root.resolve()).as_posix()
    ):
        raise RuntimeError("Audio source does not match metadata/audio-manifest.json final_audio")
    if manifest.get("final_audio_sha256") != sha256_file(source):
        raise RuntimeError("Audio source SHA-256 does not match metadata/audio-manifest.json")
    lineage = manifest.get("narrator_lineage")
    if not isinstance(lineage, dict) or lineage.get("status") in {"legacy-untracked", "standalone"}:
        raise RuntimeError("Refusing audio without validated narrator lineage")
    lineage_path = resolve_under(book_root, lineage.get("path"), (Path("metadata"),))
    input_file = resolve_under(book_root, manifest.get("input_file"), (Path("text") / "locutor",))
    if lineage_path is None or not lineage_path.is_file():
        raise RuntimeError("Audio narrator lineage file is missing or invalid")
    if input_file is None or not input_file.is_file():
        raise RuntimeError("Audio narrator input is missing or invalid")
    if manifest.get("input_sha256") != sha256_file(input_file):
        raise RuntimeError("Audio narrator input SHA-256 does not match metadata/audio-manifest.json")
    lineage_errors, provenance = validate_lineage(book_root, lineage_path, input_file)
    if lineage_errors:
        raise RuntimeError("Audio narrator lineage is invalid: " + "; ".join(lineage_errors))
    for key in (
        "narrator_changes_sha256",
        "mode",
        "base_edition",
        "base_ledger_sha256",
        "output_id",
    ):
        if lineage.get(key) != (provenance or {}).get(key):
            raise RuntimeError(f"Audio narrator lineage {key} does not match current provenance")
    return manifest_path, manifest


def prepare_audio_publication(paths: BookPaths, source: Path) -> tuple[Publication, Path, dict]:
    if source.suffix.lower() != ".mp3":
        raise RuntimeError("Published audiobook audio must use the .mp3 extension")
    manifest_path, manifest = require_real_audio_manifest(paths.assembly_root, source)
    lineage = manifest.get("narrator_lineage")
    base_edition = lineage.get("base_edition") if isinstance(lineage, dict) else None
    if not isinstance(base_edition, str):
        raise RuntimeError("Audio narrator lineage has no valid base edition")
    require_narrator_base(paths.assembly_root, base_edition)
    text_edition = {
        "source": "original",
        "translated-pt-br": "translated-pt-br",
        "fluid-pt-br": "fluid-pt-br",
    }.get(base_edition)
    if text_edition is None:
        raise RuntimeError(f"Audio narrator base edition is invalid: {base_edition}")
    destination = publication_destination(paths, source, "mp3", text_edition)
    record = publication_record(paths, source, destination)
    record["text_edition"] = text_edition
    publication = Publication(
        "audio",
        source,
        destination,
        record,
        text_edition if text_edition in SUPPLEMENTAL_TEXT_EDITIONS else None,
    )
    manifest["publication"] = publication.record
    return publication, manifest_path, manifest


def reader_destination_for_audio(
    paths: BookPaths,
    artifacts: dict,
    publications: list[Publication],
    audio_publication: Publication,
) -> Path | None:
    """Return the selected reader filename whose stem an audiobook must share."""
    text_edition = audio_publication.record.get("text_edition")
    if not isinstance(text_edition, str):
        return None

    candidates = [
        publication.destination
        for publication in publications
        if publication.kind in {"epub", "pdf"}
        and publication.record.get("text_edition") == text_edition
    ]
    if not candidates:
        for kind in ("epub", "pdf"):
            editions = artifacts.get(f"{kind}_editions")
            if not isinstance(editions, dict):
                continue
            for record in editions.values():
                if (
                    not isinstance(record, dict)
                    or record.get("text_edition") != text_edition
                    or record.get("path_root") != "book"
                ):
                    continue
                raw_path = record.get("path")
                if (
                    isinstance(raw_path, str)
                    and Path(raw_path).name == raw_path
                    and Path(raw_path).suffix.lower() == f".{kind}"
                ):
                    candidates.append(paths.public_root / raw_path)

    if not candidates:
        return None
    stems = {candidate.with_suffix("").name.casefold() for candidate in candidates}
    if len(stems) != 1:
        raise RuntimeError(
            "Audiobook publication needs one unambiguous matching EPUB/PDF filename "
            f"for {text_edition}: {sorted(candidate.name for candidate in candidates)}"
        )
    return candidates[0]


def align_audio_publications_with_reader_names(
    paths: BookPaths,
    artifacts: dict,
    publications: list[Publication],
    audio_manifests: dict[Path, dict],
) -> list[Publication]:
    aligned: list[Publication] = []
    for publication in publications:
        if publication.kind != "audio":
            aligned.append(publication)
            continue
        reader_destination = reader_destination_for_audio(
            paths,
            artifacts,
            publications,
            publication,
        )
        if reader_destination is None:
            aligned.append(publication)
            continue
        destination = reader_destination.with_suffix(".mp3")
        if destination == publication.destination:
            aligned.append(publication)
            continue
        record = publication_record(paths, publication.source, destination)
        record["text_edition"] = publication.record["text_edition"]
        replacement = Publication(
            publication.kind,
            publication.source,
            destination,
            record,
            publication.edition_key,
        )
        manifest = audio_manifests.get(publication.source)
        if manifest is None:
            raise RuntimeError("Audio publication metadata is unavailable for renaming")
        manifest["publication"] = replacement.record
        aligned.append(replacement)
    return aligned


def prepare_epub_publication(paths: BookPaths, source: Path) -> tuple[Publication, Path, dict]:
    if source.suffix.lower() != ".epub":
        raise RuntimeError("Published EPUB must use the .epub extension")
    require_under(source, paths.assembly_root / "exports" / "epub", "EPUB source")
    sidecar_path = source.with_suffix(".epub.json")
    sidecar = load_json(sidecar_path)
    if not isinstance(sidecar, dict):
        raise RuntimeError(f"EPUB sidecar must be a JSON object: {sidecar_path}")
    if sidecar.get("epub_path") != paths.relative_to_assembly(source):
        raise RuntimeError("EPUB sidecar path does not match the source EPUB")
    if sidecar.get("epub_sha256") != sha256_file(source):
        raise RuntimeError("EPUB sidecar SHA-256 does not match the source EPUB")
    text_edition = sidecar.get("text_edition", "original")
    image_edition = sidecar.get("image_edition", "original")
    if text_edition not in EPUB_TEXT_EDITIONS:
        raise RuntimeError("EPUB sidecar text edition is invalid")
    if image_edition not in EPUB_IMAGE_EDITIONS:
        raise RuntimeError("EPUB sidecar image edition is invalid")
    require_text_edition(paths.assembly_root, text_edition)
    destination = publication_destination(paths, source, "epub", text_edition)
    record = publication_record(paths, source, destination)
    record["text_edition"] = text_edition
    record["image_edition"] = image_edition
    record["reader_pair_identity"] = reader_pair_identity(sidecar)
    publication = Publication(
        "epub",
        source,
        destination,
        record,
        f"{text_edition}:{image_edition}",
    )
    sidecar["publication"] = publication.record
    return publication, sidecar_path, sidecar


def prepare_pdf_publication(paths: BookPaths, source: Path) -> tuple[Publication, Path, dict]:
    if source.suffix.lower() != ".pdf":
        raise RuntimeError("Published PDF must use the .pdf extension")
    require_under(source, paths.assembly_root / "exports" / "pdf", "PDF source")
    sidecar_path = source.with_suffix(".pdf.json")
    sidecar = load_json(sidecar_path)
    if not isinstance(sidecar, dict):
        raise RuntimeError(f"PDF sidecar must be a JSON object: {sidecar_path}")
    if sidecar.get("pdf_path") != paths.relative_to_assembly(source):
        raise RuntimeError("PDF sidecar path does not match the source PDF")
    if sidecar.get("pdf_sha256") != sha256_file(source):
        raise RuntimeError("PDF sidecar SHA-256 does not match the source PDF")
    text_edition = sidecar.get("text_edition", "original")
    image_edition = sidecar.get("image_edition", "original")
    if text_edition not in PDF_TEXT_EDITIONS:
        raise RuntimeError("PDF sidecar text edition is invalid")
    if image_edition not in PDF_IMAGE_EDITIONS:
        raise RuntimeError("PDF sidecar image edition is invalid")
    require_text_edition(paths.assembly_root, text_edition)
    destination = publication_destination(paths, source, "pdf", text_edition)
    record = publication_record(paths, source, destination)
    record["text_edition"] = text_edition
    record["image_edition"] = image_edition
    record["reader_pair_identity"] = reader_pair_identity(sidecar)
    publication = Publication(
        "pdf",
        source,
        destination,
        record,
        f"{text_edition}:{image_edition}",
    )
    sidecar["publication"] = publication.record
    return publication, sidecar_path, sidecar


def validate_destination(publication: Publication, overwrite: bool) -> None:
    destination = publication.destination
    if not destination.exists():
        return
    if sha256_file(destination) != publication.record["source_sha256"] and not overwrite:
        raise RuntimeError(f"Publication target already exists: {destination}. Use --overwrite.")


def validate_public_entries(
    paths: BookPaths,
    artifacts: object,
    publications: list[Publication],
) -> None:
    if paths.layout_kind != "new":
        return
    allowed = {
        "assembly",
        *(f"{paths.public_root.name}{suffix}" for suffix in (".epub", ".pdf", ".mp3")),
        *publication_paths_from_artifacts(artifacts),
        *(publication.destination.name for publication in publications),
    }
    unsupported = sorted(
        entry.name for entry in paths.public_root.iterdir() if entry.name not in allowed
    )
    if unsupported:
        raise RuntimeError(
            "Book root contains entries outside the selected publication set: "
            + ", ".join(unsupported)
        )


def temporary_path(destination: Path, index: int, kind: str) -> Path:
    return destination.with_name(f".{destination.name}.{os.getpid()}.{index}.{kind}")


def stage_file(publication: Publication, index: int) -> StagedReplacement | None:
    if publication.destination.exists() and sha256_file(publication.destination) == publication.record["source_sha256"]:
        return None
    staged = temporary_path(publication.destination, index, "stage")
    if staged.exists():
        raise RuntimeError(f"Publication temporary file already exists: {staged}")
    try:
        shutil.copy2(publication.source, staged)
        if sha256_file(staged) != publication.record["source_sha256"]:
            raise RuntimeError(f"Copied publication hash does not match source: {publication.destination}")
    except Exception:
        if staged.exists():
            staged.unlink()
        raise
    return StagedReplacement(publication.destination, staged)


def stage_json(destination: Path, value: object, index: int) -> StagedReplacement | None:
    data = json_bytes(value)
    if destination.exists() and destination.read_bytes() == data:
        return None
    staged = temporary_path(destination, index, "stage")
    if staged.exists():
        raise RuntimeError(f"Publication temporary file already exists: {staged}")
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        staged.write_bytes(data)
    except Exception:
        if staged.exists():
            staged.unlink()
        raise
    return StagedReplacement(destination, staged)


def commit_transaction(paths: BookPaths, replacements: list[StagedReplacement]) -> None:
    commit_recoverable_transaction(
        paths.assembly_root,
        "publication",
        replacements,
        allowed_roots=[paths.public_root],
    )


def obsolete_fluid_publication_paths(
    paths: BookPaths,
    artifacts: dict,
    publications: list[Publication],
) -> list[Path]:
    if not any(
        uses_unsuffixed_fluid_export_name(
            paths.assembly_root,
            str(publication.record.get("text_edition") or ""),
        )
        for publication in publications
    ):
        return []
    obsolete: set[Path] = set()
    for publication in publications:
        if publication.record.get("text_edition") != "fluid-pt-br":
            continue
        editions = artifacts.get(f"{publication.kind}_editions")
        previous = editions.get(publication.edition_key) if isinstance(editions, dict) else None
        previous_path = previous.get("path") if isinstance(previous, dict) else None
        if not isinstance(previous_path, str) or not previous_path:
            continue
        candidate = paths.public_root / previous_path
        if (
            candidate.name == previous_path
            and candidate != publication.destination
            and candidate.is_file()
        ):
            obsolete.add(candidate)
    return sorted(obsolete)


def remove_obsolete_publications(paths: list[Path]) -> None:
    for path in paths:
        path.unlink()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Copy final Audiobook Codex audio, EPUB, and PDF artifacts into the book root."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--epub", type=Path)
    parser.add_argument("--pdf", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.audio is None and args.epub is None and args.pdf is None:
        raise SystemExit("At least one of --audio, --epub, or --pdf is required.")

    try:
        paths = resolve_book_paths(args.book_root, allow_legacy=True)
        book_root = paths.assembly_root

        with BookTransactionLock(book_root):
            recover_pending_transactions(book_root, allowed_roots=[paths.public_root])
            publications: list[Publication] = []
            metadata_updates: list[tuple[Path, dict]] = []
            audio_manifests: dict[Path, dict] = {}
            reader_sidecars: list[tuple[Publication, dict]] = []
            if args.audio is not None:
                source = args.audio.expanduser().resolve()
                if not source.is_file():
                    raise RuntimeError(f"Audio source is missing: {source}")
                publication, manifest_path, manifest = prepare_audio_publication(paths, source)
                publications.append(publication)
                metadata_updates.append((manifest_path, manifest))
                audio_manifests[publication.source] = manifest
            if args.epub is not None:
                source = args.epub.expanduser().resolve()
                if not source.is_file():
                    raise RuntimeError(f"EPUB source is missing: {source}")
                publication, sidecar_path, sidecar = prepare_epub_publication(paths, source)
                publications.append(publication)
                metadata_updates.append((sidecar_path, sidecar))
                reader_sidecars.append((publication, sidecar))
            if args.pdf is not None:
                source = args.pdf.expanduser().resolve()
                if not source.is_file():
                    raise RuntimeError(f"PDF source is missing: {source}")
                publication, sidecar_path, sidecar = prepare_pdf_publication(paths, source)
                publications.append(publication)
                metadata_updates.append((sidecar_path, sidecar))
                reader_sidecars.append((publication, sidecar))

            for publication in publications:
                validate_destination(publication, args.overwrite)

            publication_manifest_path = book_root / "metadata" / "publication-manifest.json"
            publication_manifest_generation = file_generation([publication_manifest_path])
            existing = load_json(publication_manifest_path) if publication_manifest_path.is_file() else {}
            if not isinstance(existing, dict):
                raise RuntimeError("publication-manifest.json must be a JSON object")
            artifacts = existing.get("artifacts")
            if not isinstance(artifacts, dict):
                artifacts = {}
            else:
                artifacts = copy.deepcopy(artifacts)
            publications = align_audio_publications_with_reader_names(
                paths,
                artifacts,
                publications,
                audio_manifests,
            )
            validate_reader_publication_pair(paths, artifacts, publications)
            validate_audio_reader_edition(publications)
            for publication, sidecar in reader_sidecars:
                validate_reader_sidecar_lineage(
                    paths.assembly_root,
                    sidecar,
                    str(publication.record["text_edition"]),
                    publication.kind.upper(),
                )
                if publication.record.get("text_edition") == "fluid-pt-br":
                    validate_fluid_publication_sidecar(paths.assembly_root, sidecar)
            validate_public_entries(paths, artifacts, publications)
            obsolete_paths = obsolete_fluid_publication_paths(
                paths,
                artifacts,
                publications,
            )
            for publication in publications:
                if (
                    publication.destination.is_file()
                    and sha256_file(publication.destination)
                    == publication.record["source_sha256"]
                ):
                    preserve_existing_publication_timestamp(
                        publication,
                        existing_publication_record(artifacts, publication),
                    )
            for publication in publications:
                if publication.record.get("text_edition") in SUPPLEMENTAL_TEXT_EDITIONS:
                    require_primary_publication(paths, artifacts, publication)
            artifacts.update(
                {
                    publication.kind: publication.record
                    for publication in publications
                    if publication.kind not in {"audio", "epub", "pdf"}
                }
            )
            audio_publications = [publication for publication in publications if publication.kind == "audio"]
            if audio_publications:
                editions = artifacts.get("audio_editions")
                if not isinstance(editions, dict):
                    editions = {}
                for publication in audio_publications:
                    if publication.record.get("text_edition") not in SUPPLEMENTAL_TEXT_EDITIONS:
                        artifacts["audio"] = publication.record
                    if publication.edition_key is not None:
                        previous = editions.get(publication.edition_key)
                        if (
                            isinstance(previous, dict)
                            and previous.get("path") != publication.record["path"]
                            and not uses_unsuffixed_fluid_export_name(
                                paths.assembly_root,
                                str(publication.record.get("text_edition") or ""),
                            )
                        ):
                            raise RuntimeError(
                                "Refusing to rename an existing audio publication "
                                f"for edition {publication.edition_key}."
                            )
                        editions[publication.edition_key] = publication.record
                artifacts["audio_editions"] = editions
            epub_publications = [publication for publication in publications if publication.kind == "epub"]
            if epub_publications:
                editions = artifacts.get("epub_editions")
                if not isinstance(editions, dict):
                    editions = {}
                for publication in epub_publications:
                    if publication.record.get("text_edition") not in SUPPLEMENTAL_TEXT_EDITIONS:
                        artifacts["epub"] = publication.record
                    if publication.edition_key is not None:
                        previous = editions.get(publication.edition_key)
                        if (
                            isinstance(previous, dict)
                            and previous.get("path") != publication.record["path"]
                            and not uses_unsuffixed_fluid_export_name(
                                paths.assembly_root,
                                str(publication.record.get("text_edition") or ""),
                            )
                        ):
                            raise RuntimeError(
                                "Refusing to rename an existing EPUB publication "
                                f"for edition {publication.edition_key}."
                            )
                        editions[publication.edition_key] = publication.record
                artifacts["epub_editions"] = editions
            pdf_publications = [publication for publication in publications if publication.kind == "pdf"]
            if pdf_publications:
                editions = artifacts.get("pdf_editions")
                if not isinstance(editions, dict):
                    editions = {}
                for publication in pdf_publications:
                    if publication.record.get("text_edition") not in SUPPLEMENTAL_TEXT_EDITIONS:
                        artifacts["pdf"] = publication.record
                    if publication.edition_key is not None:
                        previous = editions.get(publication.edition_key)
                        if (
                            isinstance(previous, dict)
                            and previous.get("path") != publication.record["path"]
                            and not uses_unsuffixed_fluid_export_name(
                                paths.assembly_root,
                                str(publication.record.get("text_edition") or ""),
                            )
                        ):
                            raise RuntimeError(
                                "Refusing to rename an existing PDF publication "
                                f"for edition {publication.edition_key}."
                            )
                        editions[publication.edition_key] = publication.record
                artifacts["pdf_editions"] = editions
            publication_manifest = {
                "schema_version": "1.1",
                "published_at": iso_now(),
                "artifacts": artifacts,
            }
            if (
                existing.get("schema_version") == "1.1"
                and existing.get("artifacts") == artifacts
                and isinstance(existing.get("published_at"), str)
            ):
                publication_manifest["published_at"] = existing["published_at"]
            metadata_updates.append((publication_manifest_path, publication_manifest))
            destination_paths = [
                *(publication.destination for publication in publications),
                *(path for path, _value in metadata_updates),
            ]
            destination_generation = file_generation(destination_paths)

            replacements: list[StagedReplacement] = []
            try:
                for index, publication in enumerate(publications):
                    staged = stage_file(publication, index)
                    if staged is not None:
                        replacements.append(staged)
                metadata_offset = len(publications)
                for index, (path, value) in enumerate(metadata_updates, start=metadata_offset):
                    staged = stage_json(path, value, index)
                    if staged is not None:
                        replacements.append(staged)
                if file_generation([publication_manifest_path]) != publication_manifest_generation:
                    raise RuntimeError("Publication manifest changed before promotion.")
                if file_generation(destination_paths) != destination_generation:
                    raise RuntimeError("Publication destinations changed before promotion.")
                commit_transaction(paths, replacements)
                remove_obsolete_publications(obsolete_paths)
            except Exception:
                for replacement in replacements:
                    if replacement.staged.exists():
                        replacement.staged.unlink()
                raise
    except (OSError, RuntimeError) as error:
        print(f"Cannot publish artifacts: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    for publication in publications:
        print(f"Published {publication.kind}: {publication.record['path']}")
    print(f"Created {publication_manifest_path}")


if __name__ == "__main__":
    main()
