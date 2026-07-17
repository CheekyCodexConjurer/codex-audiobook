from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

from book_layout import BookPaths, resolve_book_paths
from path_safety import resolve_under
from validate_narrator_lineage import validate_lineage


CHATTERBOX_ENGINE = "chatterbox-multilingual-v3-pt-br"
EPUB_TEXT_EDITIONS = {"original", "revised-pt-br", "translated-pt-br"}
EPUB_IMAGE_EDITIONS = {"original", "approved-restored"}
PDF_TEXT_EDITIONS = EPUB_TEXT_EDITIONS
PDF_IMAGE_EDITIONS = EPUB_IMAGE_EDITIONS


@dataclass(frozen=True)
class Publication:
    kind: str
    source: Path
    destination: Path
    record: dict
    edition_key: str | None = None


@dataclass(frozen=True)
class StagedReplacement:
    destination: Path
    staged: Path


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
    destination = paths.public_root / f"{paths.public_root.name}.mp3"
    publication = Publication(
        "audio",
        source,
        destination,
        publication_record(paths, source, destination),
    )
    manifest["publication"] = publication.record
    return publication, manifest_path, manifest


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
    destination = paths.public_root / f"{paths.public_root.name}.epub"
    record = publication_record(paths, source, destination)
    record["text_edition"] = text_edition
    record["image_edition"] = image_edition
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
    destination = paths.public_root / f"{paths.public_root.name}.pdf"
    record = publication_record(paths, source, destination)
    record["text_edition"] = text_edition
    record["image_edition"] = image_edition
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


def validate_public_entries(paths: BookPaths) -> None:
    if paths.layout_kind != "new":
        return
    allowed = {
        "assembly",
        *(f"{paths.public_root.name}{suffix}" for suffix in (".epub", ".pdf", ".mp3")),
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


def stage_json(destination: Path, value: object, index: int) -> StagedReplacement:
    staged = temporary_path(destination, index, "stage")
    if staged.exists():
        raise RuntimeError(f"Publication temporary file already exists: {staged}")
    staged.parent.mkdir(parents=True, exist_ok=True)
    try:
        staged.write_bytes(json_bytes(value))
    except Exception:
        if staged.exists():
            staged.unlink()
        raise
    return StagedReplacement(destination, staged)


def commit_transaction(replacements: list[StagedReplacement]) -> None:
    committed: list[tuple[StagedReplacement, Path | None]] = []
    try:
        for index, replacement in enumerate(replacements):
            destination = replacement.destination
            backup = None
            if destination.exists():
                backup = temporary_path(destination, index, "backup")
                if backup.exists():
                    raise RuntimeError(f"Publication backup file already exists: {backup}")
                os.replace(destination, backup)
            committed.append((replacement, backup))
            os.replace(replacement.staged, destination)
    except Exception:
        for replacement, backup in reversed(committed):
            if replacement.destination.exists():
                replacement.destination.unlink()
            if backup is not None and backup.exists():
                os.replace(backup, replacement.destination)
        raise
    finally:
        for replacement in replacements:
            if replacement.staged.exists():
                replacement.staged.unlink()
    for _, backup in committed:
        if backup is not None and backup.exists():
            backup.unlink()


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

        publications: list[Publication] = []
        metadata_updates: list[tuple[Path, dict]] = []
        if args.audio is not None:
            source = args.audio.expanduser().resolve()
            if not source.is_file():
                raise RuntimeError(f"Audio source is missing: {source}")
            publication, manifest_path, manifest = prepare_audio_publication(paths, source)
            publications.append(publication)
            metadata_updates.append((manifest_path, manifest))
        if args.epub is not None:
            source = args.epub.expanduser().resolve()
            if not source.is_file():
                raise RuntimeError(f"EPUB source is missing: {source}")
            publication, sidecar_path, sidecar = prepare_epub_publication(paths, source)
            publications.append(publication)
            metadata_updates.append((sidecar_path, sidecar))
        if args.pdf is not None:
            source = args.pdf.expanduser().resolve()
            if not source.is_file():
                raise RuntimeError(f"PDF source is missing: {source}")
            publication, sidecar_path, sidecar = prepare_pdf_publication(paths, source)
            publications.append(publication)
            metadata_updates.append((sidecar_path, sidecar))

        validate_public_entries(paths)
        for publication in publications:
            validate_destination(publication, args.overwrite)

        publication_manifest_path = book_root / "metadata" / "publication-manifest.json"
        existing = load_json(publication_manifest_path) if publication_manifest_path.is_file() else {}
        if not isinstance(existing, dict):
            raise RuntimeError("publication-manifest.json must be a JSON object")
        artifacts = existing.get("artifacts")
        if not isinstance(artifacts, dict):
            artifacts = {}
        artifacts.update(
            {
                publication.kind: publication.record
                for publication in publications
                if publication.kind not in {"epub", "pdf"}
            }
        )
        epub_publications = [publication for publication in publications if publication.kind == "epub"]
        if epub_publications:
            editions = artifacts.get("epub_editions")
            if not isinstance(editions, dict):
                editions = {}
            for publication in epub_publications:
                artifacts["epub"] = publication.record
                if publication.edition_key is not None:
                    editions[publication.edition_key] = publication.record
            artifacts["epub_editions"] = editions
        pdf_publications = [publication for publication in publications if publication.kind == "pdf"]
        if pdf_publications:
            editions = artifacts.get("pdf_editions")
            if not isinstance(editions, dict):
                editions = {}
            for publication in pdf_publications:
                artifacts["pdf"] = publication.record
                if publication.edition_key is not None:
                    editions[publication.edition_key] = publication.record
            artifacts["pdf_editions"] = editions
        metadata_updates.append(
            (
                publication_manifest_path,
                {
                    "schema_version": "1.1",
                    "published_at": iso_now(),
                    "artifacts": artifacts,
                },
            )
        )

        replacements: list[StagedReplacement] = []
        try:
            for index, publication in enumerate(publications):
                staged = stage_file(publication, index)
                if staged is not None:
                    replacements.append(staged)
            metadata_offset = len(publications)
            for index, (path, value) in enumerate(metadata_updates, start=metadata_offset):
                replacements.append(stage_json(path, value, index))
            commit_transaction(replacements)
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
