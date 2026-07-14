from __future__ import annotations

from pathlib import Path
import shutil
import subprocess
import wave


SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
SILENCE_SECONDS = 0.22


def wave_params(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as source:
        return source.getnchannels(), source.getsampwidth(), source.getframerate()


def write_wav(path: Path, frames: bytes) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(CHANNELS)
        target.setsampwidth(SAMPLE_WIDTH)
        target.setframerate(SAMPLE_RATE)
        target.writeframes(frames)


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
