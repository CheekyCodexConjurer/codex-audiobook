from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import shutil
import subprocess
import sys
import wave


SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
SILENCE_SECONDS = 0.22


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def split_long_text(text: str, max_chars: int) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []

    chunks: list[str] = []
    paragraphs = [part.strip() for part in re.split(r"\n{2,}", normalized) if part.strip()]
    for paragraph in paragraphs:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?…])\s+", paragraph) if part.strip()]
        if not sentences:
            sentences = [paragraph]
        current = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                words = sentence.split()
                for word in words:
                    candidate = f"{current} {word}".strip()
                    if current and len(candidate) > max_chars:
                        chunks.append(current)
                        current = word
                    else:
                        current = candidate
                continue
            candidate = f"{current} {sentence}".strip()
            if current and len(candidate) > max_chars:
                chunks.append(current)
                current = sentence
            else:
                current = candidate
        if current:
            chunks.append(current)
    return chunks


def wave_params(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as source:
        return source.getnchannels(), source.getsampwidth(), source.getframerate()


def write_wav(path: Path, frames: bytes) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(CHANNELS)
        target.setsampwidth(SAMPLE_WIDTH)
        target.setframerate(SAMPLE_RATE)
        target.writeframes(frames)


def write_mock_wav(path: Path, text: str) -> None:
    duration = min(4.0, max(0.3, len(text) / 55))
    frames = bytearray()
    for sample in range(int(duration * SAMPLE_RATE)):
        value = int(3000 * math.sin((2 * math.pi * 180 * sample) / SAMPLE_RATE))
        frames.extend(value.to_bytes(2, byteorder="little", signed=True))
    write_wav(path, bytes(frames))


def render_real_wav(path: Path, text: str, voice: str, speed: float) -> None:
    try:
        import numpy as np
        from kokoro import KPipeline
    except ImportError as error:
        raise RuntimeError(
            "Kokoro and numpy are unavailable in this Python environment. "
            "Run this script with KOKORO_ROOT\\venv\\Scripts\\python.exe."
        ) from error

    pipeline = KPipeline(lang_code="p", repo_id="hexgrad/Kokoro-82M")
    frames = bytearray()
    for result in pipeline(text, voice=voice, speed=speed, split_pattern=None):
        audio = getattr(result, "audio", None)
        if audio is None:
            continue
        if hasattr(audio, "numpy"):
            audio = audio.numpy()
        samples = np.asarray(audio, dtype=np.float32)
        frames.extend((samples.clip(-1, 1) * 32767).astype(np.int16).tobytes())
    if not frames:
        raise RuntimeError("Kokoro did not generate audio for a non-empty segment.")
    write_wav(path, bytes(frames))


def join_wavs(segment_paths: list[Path], target: Path, silence_seconds: float) -> float:
    silence = b"\x00\x00" * int(SAMPLE_RATE * max(0, silence_seconds))
    total_frames = 0
    with wave.open(str(target), "wb") as output:
        output.setnchannels(CHANNELS)
        output.setsampwidth(SAMPLE_WIDTH)
        output.setframerate(SAMPLE_RATE)
        for index, segment in enumerate(segment_paths):
            if wave_params(segment) != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE):
                raise RuntimeError(f"Unexpected WAV format: {segment}")
            with wave.open(str(segment), "rb") as source:
                frames = source.readframes(source.getnframes())
                output.writeframes(frames)
                total_frames += source.getnframes()
            if index < len(segment_paths) - 1 and silence:
                output.writeframes(silence)
                total_frames += len(silence) // (CHANNELS * SAMPLE_WIDTH)
    return total_frames / SAMPLE_RATE


def transcode(final_wav: Path, output_path: Path, audio_format: str) -> None:
    if audio_format == "wav":
        if final_wav != output_path:
            shutil.copy2(final_wav, output_path)
        return
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg was not found on PATH.")
    command = [executable, "-y", "-i", str(final_wav), "-vn", "-ac", "1"]
    if audio_format == "m4a":
        command.extend(("-c:a", "aac"))
    else:
        command.extend(("-ar", "44100", "-c:a", "libmp3lame", "-b:a", "128k"))
    completed = subprocess.run(
        [*command, str(output_path)],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffmpeg audio conversion failed")


def relative_to(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def require_under(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError(f"{label} must remain under {root}: {path}") from error


def main() -> None:
    parser = argparse.ArgumentParser(description="Render local Kokoro audiobook audio from narrator text.")
    parser.add_argument("--input-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--book-root", type=Path)
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--voice", default="pm_alex")
    parser.add_argument("--speed", type=float, default=1.0)
    parser.add_argument("--format", choices=("wav", "m4a", "mp3"), default="m4a")
    parser.add_argument("--max-chars", type=int, default=450)
    parser.add_argument("--silence-seconds", type=float, default=SILENCE_SECONDS)
    parser.add_argument("--mock", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.speed <= 0:
        raise SystemExit("--speed must be positive.")
    if args.max_chars < 80:
        raise SystemExit("--max-chars must be at least 80.")

    input_file = args.input_file.expanduser().resolve()
    output_dir = args.output_dir.expanduser().resolve()
    book_root = args.book_root.expanduser().resolve() if args.book_root else None
    if (book_root is None) != args.standalone:
        raise SystemExit("Use exactly one of --book-root or --standalone.")
    if book_root is not None:
        audio_root = book_root / "audio"
        mock_root = audio_root / "mock"
        require_under(input_file, book_root / "text" / "locutor", "Narrator input")
        if args.mock:
            require_under(output_dir, mock_root, "Mock audio output")
        else:
            require_under(output_dir, audio_root, "Audio output")
            try:
                output_dir.relative_to(mock_root.resolve())
            except ValueError:
                pass
            else:
                raise SystemExit("Non-mock audio output must not use audio/mock.")
    text = input_file.read_text(encoding="utf-8").strip()
    if not text:
        raise SystemExit(f"Input text file is empty: {input_file}")

    chunks = split_long_text(text, args.max_chars)
    if not chunks:
        raise SystemExit("No renderable narrator chunks were produced.")

    segments_dir = output_dir / "segments"
    raw_dir = output_dir / "raw"
    output_dir.mkdir(parents=True, exist_ok=True)
    segments_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)
    final_wav = raw_dir / "audiobook.wav"
    final_audio = output_dir / f"audiobook.{args.format}"
    manifest_path = (
        book_root / "metadata" / "audio-manifest.json"
        if book_root is not None
        else output_dir / "audio-manifest.json"
    )
    if (final_wav.exists() or final_audio.exists() or manifest_path.exists()) and not args.overwrite:
        raise SystemExit(f"Audio artifacts already exist in {output_dir}. Use --overwrite to replace them.")

    segment_paths: list[Path] = []
    segment_records: list[dict] = []
    for index, chunk in enumerate(chunks, start=1):
        segment_path = segments_dir / f"segment-{index:04d}.wav"
        if segment_path.exists() and not args.overwrite:
            raise SystemExit(f"Segment already exists: {segment_path}. Use --overwrite to replace it.")
        if args.mock:
            write_mock_wav(segment_path, chunk)
        else:
            render_real_wav(segment_path, chunk, args.voice, args.speed)
        with wave.open(str(segment_path), "rb") as rendered:
            duration = rendered.getnframes() / rendered.getframerate()
        segment_paths.append(segment_path)
        segment_records.append(
            {
                "index": index,
                "text_sha256": sha256_bytes(chunk.encode("utf-8")),
                "path": segment_path.relative_to(output_dir).as_posix(),
                "duration_seconds": round(duration, 3),
            }
        )

    duration = join_wavs(segment_paths, final_wav, args.silence_seconds)
    transcode(final_wav, final_audio, args.format)
    def manifest_path_value(path: Path) -> str:
        return relative_to(path, book_root) if book_root is not None else path.relative_to(output_dir).as_posix()

    manifest = {
        "schema_version": "1.0",
        "generated_at": iso_now(),
        "mock": args.mock,
        "render_mode": "mock" if args.mock else "real",
        "input_file": manifest_path_value(input_file) if book_root is not None else str(input_file),
        "input_sha256": sha256_file(input_file),
        "output_dir": manifest_path_value(output_dir),
        "voice": args.voice,
        "speed": args.speed,
        "language": "pt-BR",
        "sample_rate": SAMPLE_RATE,
        "final_wav": manifest_path_value(final_wav),
        "final_wav_sha256": sha256_file(final_wav),
        "final_audio": manifest_path_value(final_audio),
        "final_audio_sha256": sha256_file(final_audio),
        "duration_seconds": round(duration, 3),
        "segments": [
            {
                **record,
                "path": manifest_path_value(output_dir / record["path"]),
            }
            for record in segment_records
        ],
    }
    write_json(manifest_path, manifest)
    print(f"Rendered {len(segment_paths)} segment(s): {final_audio}")


if __name__ == "__main__":
    main()
