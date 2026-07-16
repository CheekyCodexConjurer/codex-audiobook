from __future__ import annotations

import argparse
from pathlib import Path
import sys

from chapter_audio import (
    SCHEMA_VERSION,
    _chapter_records,
    _journal_records,
    chapter_layout_paths,
    chapter_identity,
    chapter_paths,
    chapter_specs,
    publication_identity,
    sha256_json,
    wav_details,
)
from audio_tools import validate_publication_tempo, validate_speech_wav
from narration_plan import read_json, sha256_file


def _validate_journal_wavs(output_dir: Path, records: dict[int, dict]) -> list[str]:
    errors: list[str] = []
    segments_root = (output_dir / "segments").resolve()
    for index, record in sorted(records.items()):
        path_value = record.get("path") if isinstance(record, dict) else None
        if not isinstance(path_value, str):
            errors.append(f"journal segment {index} path is missing or invalid")
            continue
        expected_relative = Path("segments") / f"segment-{index:04d}.wav"
        if Path(path_value) != expected_relative:
            errors.append(f"journal segment {index} path is not canonical")
            continue
        wav_path = (output_dir / expected_relative).resolve()
        try:
            wav_path.relative_to(segments_root)
        except ValueError:
            errors.append(f"journal segment {index} path escapes the segments directory")
            continue
        if not wav_path.is_file():
            errors.append(f"journal segment {index} WAV is missing")
            continue
        if record.get("audio_sha256") != sha256_file(wav_path):
            errors.append(f"journal segment {index} WAV hash does not match")
            continue
        try:
            wav_details(wav_path)
            validate_speech_wav(wav_path)
        except RuntimeError as error:
            errors.append(f"journal segment {index} WAV is invalid: {error}")
    return errors


def validate_chapter_audio(
    book_root: Path,
    output_dir: Path,
    plan: dict,
    journal: dict,
    manifest: dict,
) -> list[str]:
    errors: list[str] = []
    try:
        chapter_layout_paths(book_root, output_dir)
    except RuntimeError as error:
        return [str(error)]
    if manifest.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"audio chapters manifest schema_version must be {SCHEMA_VERSION!r}")
    narration_plan = manifest.get("narration_plan")
    if not isinstance(narration_plan, dict):
        errors.append("audio chapters manifest narration_plan must be an object")
    elif (
        narration_plan.get("path") != "metadata/narration-plan.json"
        or narration_plan.get("sha256") != sha256_json(plan)
    ):
        errors.append("audio chapters manifest narration plan provenance does not match")
    if manifest.get("output_dir") != output_dir.relative_to(book_root).as_posix():
        errors.append("audio chapters manifest output_dir does not match")
    manifest_publication = manifest.get("publication")
    tempo = (
        manifest_publication.get("tempo")
        if isinstance(manifest_publication, dict)
        else None
    )
    try:
        if isinstance(tempo, bool) or not isinstance(tempo, (int, float)):
            raise RuntimeError("Publication tempo is missing or invalid.")
        expected_publication = publication_identity(validate_publication_tempo(float(tempo)))
    except RuntimeError as error:
        errors.append(str(error))
        expected_publication = None
    if expected_publication is not None and manifest_publication != expected_publication:
        errors.append("audio chapters manifest publication settings are invalid")
    try:
        chapters = chapter_specs(plan)
        records = _journal_records(journal)
    except RuntimeError as error:
        return [str(error)]
    errors.extend(_validate_journal_wavs(output_dir, records))
    entries = manifest.get("chapters")
    if not isinstance(entries, list):
        return ["audio chapters manifest chapters must be an array"]
    by_id = {
        entry.get("id"): entry
        for entry in entries
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }
    if len(by_id) != len(entries):
        errors.append("audio chapters manifest must not contain duplicate or invalid chapter entries")
    if set(by_id) != {chapter.id for chapter in chapters}:
        errors.append("audio chapters manifest must cover every narration plan chapter exactly once")
    for chapter in chapters:
        entry = by_id.get(chapter.id)
        if not isinstance(entry, dict):
            continue
        if entry.get("locutor_chapter") != chapter.locutor_chapter:
            errors.append(f"chapter {chapter.id} locutor chapter does not match narration plan")
        if entry.get("logical_pages") != list(chapter.logical_pages):
            errors.append(f"chapter {chapter.id} logical pages do not match narration plan")
        if entry.get("segment_indexes") != [segment["index"] for segment in chapter.segments]:
            errors.append(f"chapter {chapter.id} segment indexes do not match narration plan")
        try:
            resolved = _chapter_records(chapter, records, output_dir)
        except RuntimeError as error:
            errors.append(f"chapter {chapter.id} has invalid segment audio: {error}")
            continue
        if entry.get("status") == "incomplete":
            if resolved is not None:
                errors.append(f"chapter {chapter.id} is complete in the journal but manifest says incomplete")
            continue
        if entry.get("status") != "complete":
            errors.append(f"chapter {chapter.id} status is invalid")
            continue
        if resolved is None:
            errors.append(f"chapter {chapter.id} is complete in manifest but not in the journal")
            continue
        chapter_records, _ = resolved
        expected_identity = chapter_identity(chapter, chapter_records, journal)
        if entry.get("assembly_identity") != expected_identity:
            errors.append(f"chapter {chapter.id} assembly identity does not match journal")
            continue
        if expected_publication is not None and entry.get("publication") != expected_publication:
            errors.append(f"chapter {chapter.id} publication settings do not match manifest")
            continue
        audio = entry.get("audio")
        if not isinstance(audio, dict):
            errors.append(f"chapter {chapter.id} audio record is missing")
            continue
        master_wav, wav, mp3 = chapter_paths(output_dir, chapter.id)
        if (
            audio.get("master_wav") != master_wav.relative_to(book_root).as_posix()
            or audio.get("wav") != wav.relative_to(book_root).as_posix()
            or audio.get("mp3") != mp3.relative_to(book_root).as_posix()
            or not master_wav.is_file()
            or not wav.is_file()
            or not mp3.is_file()
        ):
            errors.append(f"chapter {chapter.id} output paths are invalid")
            continue
        if audio.get("master_wav_sha256") != sha256_file(master_wav):
            errors.append(f"chapter {chapter.id} master WAV hash does not match")
        try:
            validate_speech_wav(master_wav)
        except RuntimeError as error:
            errors.append(f"chapter {chapter.id} master WAV is invalid: {error}")
        try:
            validate_speech_wav(wav)
        except RuntimeError as error:
            errors.append(f"chapter {chapter.id} publication WAV is invalid: {error}")
        try:
            master_duration, _ = wav_details(master_wav)
            published_duration, _ = wav_details(wav)
        except RuntimeError as error:
            errors.append(f"chapter {chapter.id} WAV duration cannot be read: {error}")
            continue
        if audio.get("master_duration_seconds") != round(master_duration, 3):
            errors.append(f"chapter {chapter.id} master WAV duration does not match")
        if audio.get("duration_seconds") != round(published_duration, 3):
            errors.append(f"chapter {chapter.id} publication WAV duration does not match")
        if (
            expected_publication is not None
            and abs(published_duration - master_duration / expected_publication["tempo"]) > 0.05
        ):
            errors.append(f"chapter {chapter.id} publication tempo duration is invalid")
        if audio.get("wav_sha256") != sha256_file(wav):
            errors.append(f"chapter {chapter.id} WAV hash does not match")
        if audio.get("mp3_sha256") != sha256_file(mp3):
            errors.append(f"chapter {chapter.id} MP3 hash does not match")
    complete = all(
        isinstance(entry, dict) and entry.get("status") == "complete"
        for entry in entries
    )
    if manifest.get("status") != ("complete" if complete else "incomplete"):
        errors.append("audio chapters manifest status does not match chapter states")
    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate assembled audiobook chapter audio.")
    parser.add_argument("--book-root", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--narration-plan", type=Path)
    parser.add_argument("--journal", type=Path)
    parser.add_argument("--manifest", type=Path)
    args = parser.parse_args()
    book_root = args.book_root.expanduser().resolve()
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
    manifest_path = (
        args.manifest.expanduser().resolve()
        if args.manifest
        else book_root / "metadata" / "audio-chapters-manifest.json"
    )
    try:
        errors = validate_chapter_audio(
            book_root,
            output_dir,
            read_json(plan_path, "narration plan"),
            read_json(journal_path, "audio render journal"),
            read_json(manifest_path, "audio chapters manifest"),
        )
    except RuntimeError as error:
        print(f"INVALID chapter audio: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    if errors:
        print("INVALID chapter audio:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID chapter audio")


if __name__ == "__main__":
    main()
