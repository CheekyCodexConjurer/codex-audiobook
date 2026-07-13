from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sys
import unicodedata


@dataclass(frozen=True)
class Publication:
    kind: str
    source: Path
    destination: Path
    record: dict


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


def safe_segment(value: str, fallback: str) -> str:
    ascii_value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "-", ascii_value)
    return normalized.strip(".-")[:100] or fallback


def relative_to_book(book_root: Path, path: Path) -> str:
    return path.resolve().relative_to(book_root.resolve()).as_posix()


def require_under(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError(f"{label} must remain under {root}: {path}") from error


def title_slug(book_root: Path) -> str:
    for manifest_name in ("epub-manifest.json", "book-map.json"):
        path = book_root / "metadata" / manifest_name
        if not path.is_file():
            continue
        data = load_json(path)
        book = data.get("book") if isinstance(data, dict) else None
        title = book.get("title") if isinstance(book, dict) else None
        if isinstance(title, str) and title.strip():
            return safe_segment(title, "audiobook")
    return safe_segment(book_root.name, "audiobook")


def publication_record(book_root: Path, source: Path, destination: Path) -> dict:
    source_hash = sha256_file(source)
    return {
        "path": relative_to_book(book_root, destination),
        "sha256": source_hash,
        "source_path": relative_to_book(book_root, source),
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
    require_under(source, book_root / "audio", "Audio source")
    try:
        source.relative_to((book_root / "audio" / "mock").resolve())
    except ValueError:
        pass
    else:
        raise RuntimeError("Refusing to publish audio from audio/mock")
    if manifest.get("final_audio") != relative_to_book(book_root, source):
        raise RuntimeError("Audio source does not match metadata/audio-manifest.json final_audio")
    if manifest.get("final_audio_sha256") != sha256_file(source):
        raise RuntimeError("Audio source SHA-256 does not match metadata/audio-manifest.json")
    return manifest_path, manifest


def prepare_audio_publication(book_root: Path, source: Path) -> tuple[Publication, Path, dict]:
    if source.suffix.lower() not in {".m4a", ".mp3", ".wav"}:
        raise RuntimeError("Published audiobook audio must use .m4a, .mp3, or .wav")
    manifest_path, manifest = require_real_audio_manifest(book_root, source)
    destination = book_root / f"{title_slug(book_root)}-audiobook{source.suffix.lower()}"
    publication = Publication(
        "audio",
        source,
        destination,
        publication_record(book_root, source, destination),
    )
    manifest["publication"] = publication.record
    return publication, manifest_path, manifest


def prepare_epub_publication(book_root: Path, source: Path) -> tuple[Publication, Path, dict]:
    if source.suffix.lower() != ".epub":
        raise RuntimeError("Published EPUB must use the .epub extension")
    require_under(source, book_root / "exports" / "epub", "EPUB source")
    sidecar_path = source.with_suffix(".epub.json")
    sidecar = load_json(sidecar_path)
    if not isinstance(sidecar, dict):
        raise RuntimeError(f"EPUB sidecar must be a JSON object: {sidecar_path}")
    if sidecar.get("epub_path") != relative_to_book(book_root, source):
        raise RuntimeError("EPUB sidecar path does not match the source EPUB")
    if sidecar.get("epub_sha256") != sha256_file(source):
        raise RuntimeError("EPUB sidecar SHA-256 does not match the source EPUB")
    destination = book_root / source.name
    publication = Publication(
        "epub",
        source,
        destination,
        publication_record(book_root, source, destination),
    )
    sidecar["publication"] = publication.record
    return publication, sidecar_path, sidecar


def validate_destination(publication: Publication, overwrite: bool) -> None:
    destination = publication.destination
    if not destination.exists():
        return
    if sha256_file(destination) != publication.record["source_sha256"] and not overwrite:
        raise RuntimeError(f"Publication target already exists: {destination}. Use --overwrite.")


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
        description="Copy final Audiobook Codex audio and EPUB artifacts into the book root."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--audio", type=Path)
    parser.add_argument("--epub", type=Path)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.audio is None and args.epub is None:
        raise SystemExit("At least one of --audio or --epub is required.")

    try:
        book_root = args.book_root.expanduser().resolve()
        if not book_root.is_dir():
            raise RuntimeError(f"Book root does not exist: {book_root}")

        publications: list[Publication] = []
        metadata_updates: list[tuple[Path, dict]] = []
        if args.audio is not None:
            source = args.audio.expanduser().resolve()
            if not source.is_file():
                raise RuntimeError(f"Audio source is missing: {source}")
            publication, manifest_path, manifest = prepare_audio_publication(book_root, source)
            publications.append(publication)
            metadata_updates.append((manifest_path, manifest))
        if args.epub is not None:
            source = args.epub.expanduser().resolve()
            if not source.is_file():
                raise RuntimeError(f"EPUB source is missing: {source}")
            publication, sidecar_path, sidecar = prepare_epub_publication(book_root, source)
            publications.append(publication)
            metadata_updates.append((sidecar_path, sidecar))

        for publication in publications:
            validate_destination(publication, args.overwrite)

        publication_manifest_path = book_root / "metadata" / "publication-manifest.json"
        existing = load_json(publication_manifest_path) if publication_manifest_path.is_file() else {}
        if not isinstance(existing, dict):
            raise RuntimeError("publication-manifest.json must be a JSON object")
        artifacts = existing.get("artifacts")
        if not isinstance(artifacts, dict):
            artifacts = {}
        artifacts.update({publication.kind: publication.record for publication in publications})
        metadata_updates.append(
            (
                publication_manifest_path,
                {
                    "schema_version": "1.0",
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
