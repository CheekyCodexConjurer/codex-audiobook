from __future__ import annotations

from array import array
from pathlib import Path
import shutil
import subprocess
import sys
import wave


SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2
CHANNELS = 1
SILENCE_SECONDS = 0.22
DEFAULT_PUBLICATION_TEMPO = 1.2
MIN_PUBLICATION_TEMPO = 0.5
MAX_PUBLICATION_TEMPO = 2.0
SPEECH_RMS_THRESHOLD = 180
MAX_RENDERED_SILENCE_SECONDS = 4.0
MIN_RENDERED_VOICED_RATIO = 0.10
SPEECH_WINDOW_SECONDS = 0.05


def wave_params(path: Path) -> tuple[int, int, int]:
    with wave.open(str(path), "rb") as source:
        return source.getnchannels(), source.getsampwidth(), source.getframerate()


def write_wav(path: Path, frames: bytes) -> None:
    with wave.open(str(path), "wb") as target:
        target.setnchannels(CHANNELS)
        target.setsampwidth(SAMPLE_WIDTH)
        target.setframerate(SAMPLE_RATE)
        target.writeframes(frames)


def speech_metrics(path: Path) -> dict[str, float]:
    try:
        with wave.open(str(path), "rb") as source:
            params = (
                source.getnchannels(),
                source.getsampwidth(),
                source.getframerate(),
            )
            if params != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE):
                raise RuntimeError(f"Unexpected WAV format: {path}")
            total_frames = source.getnframes()
            if total_frames <= 0:
                raise RuntimeError(f"Rendered WAV is empty: {path}")

            window_frames = max(1, round(SAMPLE_RATE * SPEECH_WINDOW_SECONDS))
            peak_rms = 0.0
            voiced_seconds = 0.0
            longest_silence_seconds = 0.0
            silence_started_at: float | None = None
            position_frames = 0
            while position_frames < total_frames:
                frames = source.readframes(min(window_frames, total_frames - position_frames))
                if not frames:
                    break
                samples = array("h")
                samples.frombytes(frames)
                if sys.byteorder != "little":
                    samples.byteswap()
                if not samples:
                    break
                rms = (sum(sample * sample for sample in samples) / len(samples)) ** 0.5
                peak_rms = max(peak_rms, rms)
                chunk_seconds = len(samples) / (CHANNELS * SAMPLE_RATE)
                position_seconds = position_frames / SAMPLE_RATE
                if rms > SPEECH_RMS_THRESHOLD:
                    voiced_seconds += chunk_seconds
                    if silence_started_at is not None:
                        longest_silence_seconds = max(
                            longest_silence_seconds,
                            position_seconds - silence_started_at,
                        )
                        silence_started_at = None
                elif silence_started_at is None:
                    silence_started_at = position_seconds
                position_frames += len(samples) // CHANNELS

            duration_seconds = total_frames / SAMPLE_RATE
            if silence_started_at is not None:
                longest_silence_seconds = max(
                    longest_silence_seconds,
                    duration_seconds - silence_started_at,
                )
    except (OSError, wave.Error) as error:
        raise RuntimeError(f"Cannot read rendered WAV {path}: {error}") from error

    voiced_ratio = voiced_seconds / duration_seconds if duration_seconds > 0 else 0.0
    return {
        "duration_seconds": round(duration_seconds, 3),
        "peak_rms": round(peak_rms, 3),
        "voiced_seconds": round(voiced_seconds, 3),
        "voiced_ratio": round(voiced_ratio, 4),
        "longest_silence_seconds": round(longest_silence_seconds, 3),
    }


def validate_speech_wav(path: Path) -> dict[str, float]:
    metrics = speech_metrics(path)
    summary = (
        f"duration={metrics['duration_seconds']:.3f}s, "
        f"peak_rms={metrics['peak_rms']:.3f}, "
        f"voiced={metrics['voiced_seconds']:.3f}s, "
        f"voiced_ratio={metrics['voiced_ratio']:.4f}, "
        f"longest_silence={metrics['longest_silence_seconds']:.3f}s"
    )
    if metrics["peak_rms"] <= SPEECH_RMS_THRESHOLD:
        raise RuntimeError(f"Rendered WAV contains no audible speech ({summary}): {path}")
    if metrics["longest_silence_seconds"] >= MAX_RENDERED_SILENCE_SECONDS:
        raise RuntimeError(
            "Rendered WAV contains "
            f"{metrics['longest_silence_seconds']:.3f}s of continuous silence "
            f"({summary}): {path}"
        )
    if metrics["voiced_ratio"] < MIN_RENDERED_VOICED_RATIO:
        raise RuntimeError(
            "Rendered WAV voiced ratio is materially insufficient "
            f"({metrics['voiced_ratio']:.4f} < {MIN_RENDERED_VOICED_RATIO:.4f}; "
            f"{summary}): {path}"
        )
    return metrics


def validate_publication_tempo(value: float) -> float:
    if not MIN_PUBLICATION_TEMPO <= value <= MAX_PUBLICATION_TEMPO:
        raise RuntimeError(
            "Publication tempo must be between "
            f"{MIN_PUBLICATION_TEMPO:g} and {MAX_PUBLICATION_TEMPO:g}."
        )
    return value


def join_wavs(
    segment_paths: list[Path],
    target: Path,
    silence_seconds: float | None = None,
    boundary_pauses: list[float] | None = None,
) -> float:
    if boundary_pauses is None:
        silence_seconds = 0.0 if silence_seconds is None else silence_seconds
        boundary_pauses = [max(0, silence_seconds)] * max(0, len(segment_paths) - 1)
    if len(boundary_pauses) != max(0, len(segment_paths) - 1):
        raise RuntimeError("WAV boundary pauses must have one entry between each segment.")
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
            pause = boundary_pauses[index] if index < len(boundary_pauses) else 0.0
            silence = b"\x00\x00" * int(SAMPLE_RATE * max(0, pause))
            if silence:
                output.writeframes(silence)
                total_frames += len(silence) // (CHANNELS * SAMPLE_WIDTH)
    return total_frames / SAMPLE_RATE


def apply_publication_tempo(source_wav: Path, target_wav: Path, tempo: float) -> None:
    tempo = validate_publication_tempo(tempo)
    if source_wav.resolve() == target_wav.resolve():
        raise RuntimeError("Publication WAV target must differ from its master WAV source.")
    if wave_params(source_wav) != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE):
        raise RuntimeError(f"Unexpected WAV format: {source_wav}")
    if tempo == 1:
        shutil.copy2(source_wav, target_wav)
        return
    executable = shutil.which("ffmpeg")
    if not executable:
        raise RuntimeError("ffmpeg was not found on PATH.")
    completed = subprocess.run(
        [
            executable,
            "-y",
            "-i",
            str(source_wav),
            "-vn",
            "-map",
            "0:a:0",
            "-af",
            f"atempo={tempo:.8g}",
            "-ac",
            str(CHANNELS),
            "-ar",
            str(SAMPLE_RATE),
            "-c:a",
            "pcm_s16le",
            str(target_wav),
        ],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise RuntimeError(completed.stderr.strip() or "ffmpeg tempo processing failed")
    if wave_params(target_wav) != (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE):
        raise RuntimeError(f"Unexpected publication WAV format: {target_wav}")


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
