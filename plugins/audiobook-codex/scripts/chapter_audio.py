from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
from typing import Iterable
import wave

from book_layout import resolve_book_paths
from audio_tools import (
    CHANNELS,
    DEFAULT_PUBLICATION_TEMPO,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    apply_publication_tempo,
    join_wavs,
    transcode,
    validate_publication_tempo,
    validate_speech_wav,
)
from narration_plan import read_json, sha256_file
from path_safety import resolve_under


SCHEMA_VERSION = "1.0"
_SAFE_CHAPTER_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class ChapterSpec:
    id: str
    locutor_chapter: str
    logical_pages: tuple[int, ...]
    segments: tuple[dict, ...]


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def sha256_json(value: object) -> str:
    data = json.dumps(value, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def wav_details(path: Path) -> tuple[float, str]:
    try:
        with wave.open(str(path), "rb") as source:
            params = (
                source.getnchannels(),
                source.getsampwidth(),
                source.getframerate(),
            )
            frames = source.getnframes()
    except (OSError, wave.Error) as error:
        raise RuntimeError(f"Cannot read chapter source WAV {path}: {error}") from error
    if params != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE) or frames <= 0:
        raise RuntimeError(f"Invalid chapter source WAV {path}")
    return frames / SAMPLE_RATE, sha256_file(path)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(
            json.dumps(value, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def chapter_specs(plan: dict) -> list[ChapterSpec]:
    entries = plan.get("segments")
    if not isinstance(entries, list) or not entries:
        raise RuntimeError("Narration plan must contain segments.")
    grouped: list[ChapterSpec] = []
    current_id: str | None = None
    current_chapter = ""
    current_pages: tuple[int, ...] = ()
    current_segments: list[dict] = []
    seen: set[str] = set()

    def finish() -> None:
        if current_id is not None:
            grouped.append(
                ChapterSpec(
                    current_id,
                    current_chapter,
                    current_pages,
                    tuple(current_segments),
                )
            )

    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("Narration plan segment must be an object.")
        source = entry.get("source")
        if not isinstance(source, dict):
            raise RuntimeError("Narration plan segment source must be an object.")
        chapter_id = source.get("base_output_id")
        locutor_chapter = source.get("locutor_chapter")
        pages = source.get("logical_pages")
        if (
            not isinstance(chapter_id, str)
            or not chapter_id
            or _SAFE_CHAPTER_ID.fullmatch(chapter_id) is None
            or chapter_id in {".", ".."}
            or not isinstance(locutor_chapter, str)
            or not isinstance(pages, list)
            or any(not isinstance(page, int) or page <= 0 for page in pages)
        ):
            raise RuntimeError("Narration plan chapter provenance is invalid.")
        if chapter_id != current_id:
            finish()
            if chapter_id in seen:
                raise RuntimeError(f"Narration plan chapter {chapter_id!r} is not contiguous.")
            seen.add(chapter_id)
            current_id = chapter_id
            current_chapter = locutor_chapter
            current_pages = tuple(pages)
            current_segments = []
        elif locutor_chapter != current_chapter or tuple(pages) != current_pages:
            raise RuntimeError(f"Narration plan chapter {chapter_id!r} has inconsistent provenance.")
        current_segments.append(entry)
    finish()
    return grouped


def _journal_records(journal: dict) -> dict[int, dict]:
    records = journal.get("segments")
    if not isinstance(records, list):
        raise RuntimeError("Audio render journal must contain a segments array.")
    result: dict[int, dict] = {}
    for record in records:
        index = record.get("index") if isinstance(record, dict) else None
        if not isinstance(index, int) or index <= 0 or index in result:
            raise RuntimeError("Audio render journal has invalid segment indexes.")
        result[index] = record
    return result


def _chapter_records(
    chapter: ChapterSpec,
    records: dict[int, dict],
    output_dir: Path,
) -> tuple[list[dict], list[Path]] | None:
    chapter_records: list[dict] = []
    paths: list[Path] = []
    for entry in chapter.segments:
        index = entry.get("index")
        record = records.get(index) if isinstance(index, int) else None
        if not isinstance(record, dict):
            return None
        if (
            record.get("semantic_id") != entry.get("id")
            or record.get("text_sha256") != entry.get("text_sha256")
            or not isinstance(record.get("path"), str)
            or not isinstance(record.get("audio_sha256"), str)
        ):
            return None
        expected_relative = Path("segments") / f"segment-{index:04d}.wav"
        if Path(record["path"]) != expected_relative:
            return None
        segments_root = (output_dir / "segments").resolve()
        path = (output_dir / expected_relative).resolve()
        try:
            path.relative_to(segments_root)
        except ValueError:
            return None
        if not path.is_file() or sha256_file(path) != record["audio_sha256"]:
            return None
        wav_details(path)
        validate_speech_wav(path)
        chapter_records.append(record)
        paths.append(path)
    return chapter_records, paths


def chapter_identity(chapter: ChapterSpec, records: list[dict], journal: dict) -> dict:
    return {
        "chapter_id": chapter.id,
        "segments": [
            {
                "id": entry["id"],
                "index": entry["index"],
                "text_sha256": entry["text_sha256"],
                "audio_sha256": record["audio_sha256"],
                "pause_after": entry["pause_after"],
            }
            for entry, record in zip(chapter.segments, records, strict=True)
        ],
        "segment_render_identity": journal.get(
            "segment_render_identity",
            journal.get("render_identity"),
        ),
    }


def require_audio_output_dir(book_root: Path, output_dir: Path) -> None:
    audio_root = (book_root / "audio").resolve()
    try:
        output_dir.resolve().relative_to(audio_root)
    except ValueError as error:
        raise RuntimeError(f"Chapter audio output must remain under {audio_root}") from error


def chapter_layout_paths(book_root: Path, output_dir: Path) -> tuple[Path, Path, Path]:
    require_audio_output_dir(book_root, output_dir)
    try:
        relative_output_dir = output_dir.relative_to(book_root)
    except ValueError as error:
        raise RuntimeError(f"Chapter audio output must remain under {book_root / 'audio'}") from error
    required_subtrees = (Path("audio"),)
    paths = tuple(
        resolve_under(
            book_root,
            (relative_output_dir / "chapters" / name).as_posix(),
            required_subtrees,
        )
        for name in ("original", "final", "temp")
    )
    if any(path is None for path in paths):
        raise RuntimeError("Chapter layout must not contain symlinks or junctions.")
    return paths[0], paths[1], paths[2]


def chapter_paths(output_dir: Path, chapter_id: str) -> tuple[Path, Path, Path]:
    if _SAFE_CHAPTER_ID.fullmatch(chapter_id) is None or chapter_id in {".", ".."}:
        raise RuntimeError(f"Unsafe chapter ID: {chapter_id!r}")
    root = (output_dir / "chapters").resolve()
    original_root = (root / "original").resolve()
    final_root = (root / "final").resolve()
    master_wav = (original_root / f"{chapter_id}.wav").resolve()
    wav = (final_root / f"{chapter_id}.wav").resolve()
    mp3 = (final_root / f"{chapter_id}.mp3").resolve()
    try:
        master_wav.relative_to(original_root)
        wav.relative_to(final_root)
        mp3.relative_to(final_root)
    except ValueError as error:
        raise RuntimeError(f"Chapter outputs must remain under {root / 'original'} or {root / 'final'}") from error
    return master_wav, wav, mp3


def _temporary_audio_path(staging_dir: Path, target: Path) -> Path:
    staging_dir.mkdir(parents=True, exist_ok=True)
    return staging_dir / f".{target.stem}.{os.getpid()}.tmp{target.suffix}"


def _atomic_join(
    paths: list[Path],
    target: Path,
    pauses: list[float],
    staging_dir: Path,
) -> float:
    temporary = _temporary_audio_path(staging_dir, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        duration = join_wavs(paths, temporary, boundary_pauses=pauses)
        validate_speech_wav(temporary)
        os.replace(temporary, target)
        return duration
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_transcode(source: Path, target: Path, staging_dir: Path) -> None:
    temporary = _temporary_audio_path(staging_dir, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        transcode(source, temporary, "mp3")
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_apply_publication_tempo(
    source: Path,
    target: Path,
    tempo: float,
    staging_dir: Path,
) -> float:
    temporary = _temporary_audio_path(staging_dir, target)
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        apply_publication_tempo(source, temporary, tempo)
        validate_speech_wav(temporary)
        os.replace(temporary, target)
        duration, _ = wav_details(target)
        return duration
    finally:
        if temporary.exists():
            temporary.unlink()


def publication_identity(tempo: float) -> dict:
    return {
        "processor": "ffmpeg-atempo-v1",
        "tempo": tempo,
        "preserves_pitch": True,
    }


def _existing_complete(
    existing: object,
    identity: dict,
    publication: dict,
    master_wav: Path,
    wav: Path,
    mp3: Path,
) -> bool:
    if not isinstance(existing, dict) or existing.get("assembly_identity") != identity:
        return False
    if existing.get("publication") != publication:
        return False
    audio = existing.get("audio")
    if not isinstance(audio, dict):
        return False
    try:
        validate_speech_wav(master_wav)
        validate_speech_wav(wav)
    except RuntimeError:
        return False
    return (
        master_wav.is_file()
        and wav.is_file()
        and mp3.is_file()
        and audio.get("master_wav_sha256") == sha256_file(master_wav)
        and audio.get("wav_sha256") == sha256_file(wav)
        and audio.get("mp3_sha256") == sha256_file(mp3)
    )


def assemble_chapters(
    book_root: Path,
    output_dir: Path,
    plan: dict,
    journal: dict,
    selected_ids: Iterable[str] | None = None,
    publication_tempo: float = DEFAULT_PUBLICATION_TEMPO,
) -> dict:
    _, _, temp_root = chapter_layout_paths(book_root, output_dir)
    publication_tempo = validate_publication_tempo(publication_tempo)
    publication = publication_identity(publication_tempo)
    selected = set(selected_ids) if selected_ids is not None else None
    all_chapters = chapter_specs(plan)
    known_ids = {chapter.id for chapter in all_chapters}
    if selected is not None and (unknown := selected - known_ids):
        raise RuntimeError(f"Unknown narration plan chapters: {', '.join(sorted(unknown))}")
    temp_root.mkdir(parents=True, exist_ok=True)
    manifest_path = book_root / "metadata" / "audio-chapters-manifest.json"
    previous = (
        read_json(manifest_path, "audio chapters manifest")
        if manifest_path.is_file()
        else {}
    )
    previous_chapters = (
        {
            entry.get("id"): entry
            for entry in previous.get("chapters", [])
            if isinstance(entry, dict) and isinstance(entry.get("id"), str)
        }
        if isinstance(previous, dict)
        else {}
    )
    records = _journal_records(journal)
    manifest_chapters: list[dict] = []
    for chapter in all_chapters:
        existing = previous_chapters.get(chapter.id)
        if selected is not None and chapter.id not in selected:
            resolved = _chapter_records(chapter, records, output_dir)
            if resolved is not None:
                chapter_records, _ = resolved
                identity = chapter_identity(chapter, chapter_records, journal)
                master_wav, wav, mp3 = chapter_paths(output_dir, chapter.id)
                if _existing_complete(
                    existing,
                    identity,
                    publication,
                    master_wav,
                    wav,
                    mp3,
                ):
                    manifest_chapters.append(existing)
                    continue
            manifest_chapters.append(
                {
                    "id": chapter.id,
                    "status": "incomplete",
                    "locutor_chapter": chapter.locutor_chapter,
                    "logical_pages": list(chapter.logical_pages),
                    "segment_indexes": [entry["index"] for entry in chapter.segments],
                }
            )
            continue
        resolved = _chapter_records(chapter, records, output_dir)
        if resolved is None:
            manifest_chapters.append(
                {
                    "id": chapter.id,
                    "status": "incomplete",
                    "locutor_chapter": chapter.locutor_chapter,
                    "logical_pages": list(chapter.logical_pages),
                    "segment_indexes": [entry["index"] for entry in chapter.segments],
                }
            )
            continue
        chapter_records, paths = resolved
        identity = chapter_identity(chapter, chapter_records, journal)
        master_wav, wav, mp3 = chapter_paths(output_dir, chapter.id)
        if _existing_complete(
            existing,
            identity,
            publication,
            master_wav,
            wav,
            mp3,
        ):
            master_duration, _ = wav_details(master_wav)
            duration, _ = wav_details(wav)
        else:
            pauses = [
                float(entry["pause_after"]["seconds"])
                for entry in chapter.segments[:-1]
            ]
            master_duration = _atomic_join(paths, master_wav, pauses, temp_root)
            duration = _atomic_apply_publication_tempo(
                master_wav,
                wav,
                publication_tempo,
                temp_root,
            )
            _atomic_transcode(wav, mp3, temp_root)
        manifest_chapters.append(
            {
                "id": chapter.id,
                "status": "complete",
                "locutor_chapter": chapter.locutor_chapter,
                "logical_pages": list(chapter.logical_pages),
                "segment_indexes": [entry["index"] for entry in chapter.segments],
                "assembly_identity": identity,
                "publication": publication,
                "audio": {
                    "master_wav": master_wav.relative_to(book_root).as_posix(),
                    "master_wav_sha256": sha256_file(master_wav),
                    "master_duration_seconds": round(master_duration, 3),
                    "wav": wav.relative_to(book_root).as_posix(),
                    "wav_sha256": sha256_file(wav),
                    "mp3": mp3.relative_to(book_root).as_posix(),
                    "mp3_sha256": sha256_file(mp3),
                    "duration_seconds": round(duration, 3),
                },
            }
        )
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": iso_now(),
        "narration_plan": {
            "path": "metadata/narration-plan.json",
            "sha256": sha256_json(plan),
        },
        "journal_schema_version": journal.get("schema_version"),
        "output_dir": output_dir.relative_to(book_root).as_posix(),
        "publication": publication,
        "status": (
            "complete"
            if all(entry.get("status") == "complete" for entry in manifest_chapters)
            else "incomplete"
        ),
        "chapters": manifest_chapters,
    }
    write_json(manifest_path, manifest)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Assemble completed audiobook chapter WAV and MP3 artifacts from a render journal."
    )
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--narration-plan", type=Path)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--chapters")
    parser.add_argument(
        "--publication-tempo",
        type=float,
        default=DEFAULT_PUBLICATION_TEMPO,
        help="Pitch-preserving delivery cadence applied after the immutable 1.0x WAV master.",
    )
    args = parser.parse_args()
    try:
        book_root = resolve_book_paths(args.book_root).assembly_root
        output_dir = args.output_dir.expanduser().resolve()
        plan_path = (
            args.narration_plan.expanduser().resolve()
            if args.narration_plan
            else book_root / "metadata" / "narration-plan.json"
        )
        journal_path = (
            args.journal.expanduser().resolve()
            if args.journal
            else book_root / "metadata" / "audio-render-journal.json"
        )
        plan = read_json(plan_path, "narration plan")
        journal = read_json(journal_path, "audio render journal")
        selected = (
            [chapter.strip() for chapter in args.chapters.split(",") if chapter.strip()]
            if args.chapters
            else None
        )
        manifest = assemble_chapters(
            book_root,
            output_dir,
            plan,
            journal,
            selected,
            args.publication_tempo,
        )
    except RuntimeError as error:
        raise SystemExit(f"Cannot assemble chapter audio: {error}") from error
    completed = sum(entry["status"] == "complete" for entry in manifest["chapters"])
    print(f"Assembled {completed} chapter(s): {book_root / 'metadata' / 'audio-chapters-manifest.json'}")


if __name__ == "__main__":
    main()
