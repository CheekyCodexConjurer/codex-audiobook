from __future__ import annotations

from array import array
import copy
from pathlib import Path
import shutil
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch
import wave

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import audio_tools
import chapter_audio
import render_chatterbox
from narration_plan import sha256_file


def _frames(seconds: float, amplitude: int) -> bytes:
    samples = array("h", [amplitude]) * int(audio_tools.SAMPLE_RATE * seconds)
    return samples.tobytes()


def _write_segment(path: Path, seconds: float, amplitude: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    audio_tools.write_wav(path, _frames(seconds, amplitude))


def _segment(index: int, chapter_id: str, text_hash: str) -> dict:
    return {
        "id": f"{chapter_id}-{index:04d}",
        "index": index,
        "text_sha256": text_hash,
        "pause_after": {"seconds": 0.0},
        "source": {
            "base_output_id": chapter_id,
            "locutor_chapter": f"Capítulo {chapter_id[-2:]}",
            "logical_pages": [index],
        },
    }


def _fixture(root: Path) -> tuple[Path, Path, dict, dict]:
    book_root = root / "book"
    output_dir = book_root / "audio" / "chatterbox"
    segments_dir = output_dir / "segments"
    metadata_dir = book_root / "metadata"
    metadata_dir.mkdir(parents=True)
    first = segments_dir / "segment-0001.wav"
    second = segments_dir / "segment-0002.wav"
    _write_segment(first, 0.4, 1200)
    _write_segment(second, 0.4, 1400)
    plan = {
        "segments": [
            _segment(1, "chapter-01", "a" * 64),
            _segment(2, "chapter-02", "b" * 64),
        ]
    }
    journal = {
        "schema_version": "1.0",
        "segment_render_identity": {"engine": "test", "device": "cpu"},
        "segments": [
            {
                "index": 1,
                "semantic_id": "chapter-01-0001",
                "text_sha256": "a" * 64,
                "path": "segments/segment-0001.wav",
                "audio_sha256": sha256_file(first),
            },
            {
                "index": 2,
                "semantic_id": "chapter-02-0002",
                "text_sha256": "b" * 64,
                "path": "segments/segment-0002.wav",
                "audio_sha256": sha256_file(second),
            },
        ],
    }
    return book_root, output_dir, plan, journal


def _copy_transcode(source: Path, target: Path, audio_format: str) -> None:
    shutil.copy2(source, target)


def test_streaming_join_reads_in_blocks(root: Path) -> None:
    source = root / "source.wav"
    target = root / "joined.wav"
    frames = audio_tools.WAV_COPY_BLOCK_FRAMES * 3 + 117
    audio_tools.write_wav(source, (array("h", [1000]) * frames).tobytes())
    requested_reads: list[int] = []
    original_open = audio_tools.wave.open

    class ProbedReader:
        def __init__(self, wrapped: wave.Wave_read):
            self._wrapped = wrapped

        def __enter__(self) -> "ProbedReader":
            self._wrapped.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._wrapped.__exit__(*args)

        def __getattr__(self, name: str) -> object:
            return getattr(self._wrapped, name)

        def readframes(self, count: int) -> bytes:
            requested_reads.append(count)
            return self._wrapped.readframes(count)

    def probed_open(path: str, mode: str = "rb") -> object:
        opened = original_open(path, mode)
        if mode == "rb":
            return ProbedReader(opened)
        return opened

    with patch.object(audio_tools.wave, "open", probed_open):
        duration = audio_tools.join_wavs([source], target)
    assert abs(duration - frames / audio_tools.SAMPLE_RATE) < 0.0001
    assert requested_reads
    assert max(requested_reads) == audio_tools.WAV_COPY_BLOCK_FRAMES
    assert max(requested_reads) < frames


def test_selective_assembly_reuses_unselected_chapter(root: Path) -> None:
    book_root, output_dir, plan, journal = _fixture(root)
    with patch.object(chapter_audio, "transcode", _copy_transcode):
        manifest = chapter_audio.assemble_chapters(
            book_root,
            output_dir,
            plan,
            journal,
            publication_tempo=1.0,
        )
    original_chapter = next(entry for entry in manifest["chapters"] if entry["id"] == "chapter-01")
    old_segment = output_dir / "segments" / "segment-0001.wav"
    old_segment.unlink()

    changed_journal = copy.deepcopy(journal)
    changed_second = output_dir / "segments" / "segment-0002.wav"
    _write_segment(changed_second, 0.4, 1800)
    changed_journal["segments"][1]["audio_sha256"] = sha256_file(changed_second)
    real_validate = chapter_audio.validate_speech_wav

    def reject_old_chapter_validation(path: Path) -> dict[str, float]:
        if "chapter-01" in path.name or path.name == "segment-0001.wav":
            raise AssertionError(f"unselected chapter was revalidated: {path}")
        return real_validate(path)

    with (
        patch.object(chapter_audio, "transcode", _copy_transcode),
        patch.object(chapter_audio, "validate_speech_wav", reject_old_chapter_validation),
    ):
        updated = chapter_audio.assemble_chapters(
            book_root,
            output_dir,
            plan,
            changed_journal,
            selected_ids=["chapter-02"],
            publication_tempo=1.0,
        )
    updated_chapter = next(entry for entry in updated["chapters"] if entry["id"] == "chapter-01")
    assert updated_chapter["status"] == "complete"
    assert updated_chapter["audio"] == original_chapter["audio"]
    assert updated["status"] == "complete"


def test_identity_cache_skips_rejoin(root: Path) -> None:
    book_root, output_dir, plan, journal = _fixture(root)
    with patch.object(chapter_audio, "transcode", _copy_transcode):
        chapter_audio.assemble_chapters(
            book_root,
            output_dir,
            plan,
            journal,
            publication_tempo=1.0,
        )

    def fail_join(*args: object, **kwargs: object) -> float:
        raise AssertionError("cached chapter should not be rejoined")

    with (
        patch.object(chapter_audio, "transcode", _copy_transcode),
        patch.object(chapter_audio, "_atomic_join", fail_join),
    ):
        cached = chapter_audio.assemble_one_chapter(
            book_root,
            output_dir,
            plan,
            journal,
            "chapter-02",
            publication_tempo=1.0,
        )
    assert next(entry for entry in cached["chapters"] if entry["id"] == "chapter-02")["status"] == "complete"


def test_full_book_master_requires_complete_chapters(root: Path) -> None:
    book_root, output_dir, plan, journal = _fixture(root)
    with patch.object(chapter_audio, "transcode", _copy_transcode):
        manifest = chapter_audio.assemble_chapters(
            book_root,
            output_dir,
            plan,
            journal,
            publication_tempo=1.0,
        )
    incomplete = copy.deepcopy(manifest)
    incomplete["chapters"][1] = {
        "id": "chapter-02",
        "status": "incomplete",
        "locutor_chapter": "Capítulo 02",
        "logical_pages": [2],
        "segment_indexes": [2],
    }
    try:
        chapter_audio.assemble_full_book_master(
            book_root,
            output_dir,
            incomplete,
            interchapter_pauses=[0.2],
        )
    except RuntimeError as error:
        assert "requires every chapter to be complete" in str(error)
    else:
        raise AssertionError("expected incomplete chapter manifest to fail")


def test_full_book_master_assembles_from_chapter_masters(root: Path) -> None:
    book_root, output_dir, plan, journal = _fixture(root)
    with patch.object(chapter_audio, "transcode", _copy_transcode):
        manifest = chapter_audio.assemble_chapters(
            book_root,
            output_dir,
            plan,
            journal,
            publication_tempo=1.0,
        )
        result = chapter_audio.assemble_full_book_master(
            book_root,
            output_dir,
            manifest,
            target=output_dir / "chapters" / "original" / "full-book.master.wav",
            interchapter_pauses=[0.25],
        )
    assert result["status"] == "complete"
    assert result["chapter_ids"] == ["chapter-01", "chapter-02"]
    assert result["interchapter_pauses_seconds"] == [0.25]
    assert result["master_duration_seconds"] == 1.05
    assert (book_root / result["master_wav"]).is_file()


def test_render_journal_fragments_resume_and_compact(root: Path) -> None:
    journal_path = root / "metadata" / "audio-render-journal.json"
    journal = render_chatterbox.new_render_journal(
        {"engine": "test", "runtime": {"renderer_sha256": "a" * 64}},
        "text/locutor/book.txt",
        "b" * 64,
    )
    render_chatterbox.write_json(journal_path, journal)
    record = {
        "index": 1,
        "path": "segments/segment-0001.wav",
        "audio_sha256": "c" * 64,
    }
    render_chatterbox.write_render_journal_record(journal_path, journal, record)

    loaded, records = render_chatterbox.load_render_journal(
        journal_path,
        journal["segment_render_identity"],
    )
    assert records == {1: record}
    assert loaded["segments"] == [record]

    compacted = render_chatterbox.render_journal_snapshot(loaded, records)
    compacted["segment_render_identity"] = {
        "engine": "test",
        "runtime": {"renderer_sha256": "e" * 64},
    }
    render_chatterbox.write_json(journal_path, compacted)
    reloaded, reloaded_records = render_chatterbox.load_render_journal(
        journal_path,
        compacted["segment_render_identity"],
    )
    assert reloaded_records == records
    assert reloaded["segments"] == [record]
    render_chatterbox.clear_render_journal_records(journal_path)
    assert not render_chatterbox.render_journal_records_directory(journal_path).exists()


def test_full_book_render_prunes_stale_journal_records(root: Path) -> None:
    previous_records = {
        1: {"index": 1},
        2: {"index": 2},
        3: {"index": 3},
    }
    plan_segments = [
        SimpleNamespace(line_number=1),
        SimpleNamespace(line_number=2),
    ]

    assert render_chatterbox.journal_records_for_current_render(
        previous_records,
        plan_segments,
        True,
    ) == {
        1: {"index": 1},
        2: {"index": 2},
    }
    assert render_chatterbox.journal_records_for_current_render(
        previous_records,
        plan_segments,
        False,
    ) == previous_records


def test_segment_record_uses_fresh_render_details_without_rereading(root: Path) -> None:
    output_dir = root / "audio"
    segment_path = output_dir / "segments" / "segment-0001.wav"
    segment = SimpleNamespace(
        line_number=1,
        semantic_id="chapter-01-0001",
        text="Trecho já validado durante a renderização.",
        warnings=(),
    )
    details = {
        "duration_seconds": 0.75,
        "audio_sha256": "d" * 64,
        "speech": {"rms": 1200.0},
    }
    with (
        patch.object(
            render_chatterbox,
            "wav_details",
            side_effect=AssertionError("fresh render must not be read again"),
        ),
        patch.object(
            render_chatterbox,
            "validate_speech_wav",
            side_effect=AssertionError("fresh render must not be validated again"),
        ),
    ):
        record = render_chatterbox.segment_record(
            1,
            segment,
            segment_path,
            output_dir,
            123,
            audio_details=details,
        )
    assert record["audio_sha256"] == details["audio_sha256"]
    assert record["duration_seconds"] == details["duration_seconds"]
    assert record["speech"] == details["speech"]


def test_interchapter_pause_comes_from_each_nonfinal_chapter_tail(root: Path) -> None:
    del root
    first = _segment(1, "chapter-01", "a" * 64)
    second = _segment(2, "chapter-01", "b" * 64)
    second["source"]["logical_pages"] = [1]
    third = _segment(3, "chapter-02", "c" * 64)
    plan = {
        "segments": [
            {
                **first,
                "pause_after": {"seconds": 0.1},
            },
            {
                **second,
                "pause_after": {"seconds": 0.65},
            },
            {
                **third,
                "pause_after": {"seconds": 0.0},
            },
        ]
    }
    assert render_chatterbox.interchapter_pause_seconds(plan) == [0.65]


def main() -> None:
    tests = [
        test_streaming_join_reads_in_blocks,
        test_selective_assembly_reuses_unselected_chapter,
        test_identity_cache_skips_rejoin,
        test_full_book_master_requires_complete_chapters,
        test_full_book_master_assembles_from_chapter_masters,
        test_render_journal_fragments_resume_and_compact,
        test_full_book_render_prunes_stale_journal_records,
        test_segment_record_uses_fresh_render_details_without_rereading,
        test_interchapter_pause_comes_from_each_nonfinal_chapter_tail,
    ]
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        for test in tests:
            test_root = root / test.__name__
            test_root.mkdir()
            test(test_root)
    print("audio pipeline tests OK")


if __name__ == "__main__":
    main()
