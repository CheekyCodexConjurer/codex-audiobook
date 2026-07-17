from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib.metadata import PackageNotFoundError, version
import json
import os
from pathlib import Path
import shutil
import sys
import wave

# Rendering must never fetch model assets.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

from book_layout import resolve_book_paths
from chatterbox_text import DEFAULT_MAX_CHARS, prepare_chatterbox_segments
from chapter_audio import assemble_chapters, chapter_specs
from narration_plan import load_plan_segments, read_json as read_narration_plan
from audio_tools import (
    CHANNELS,
    DEFAULT_PUBLICATION_TEMPO,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    SILENCE_SECONDS,
    apply_publication_tempo,
    join_wavs,
    transcode,
    validate_publication_tempo,
    validate_speech_wav,
    write_wav,
)
from validate_narrator_lineage import validate_lineage
from validate_narrator_quality import validate_review
from validate_narration_plan import validate_plan


DEFAULT_MODEL_ROOT = Path(r"E:\Pessoal\tts\chatterbox-multilingual-v3\models")
DEFAULT_REFERENCE_VOICE = (
    Path(__file__).resolve().parent.parent / "assets" / "voices" / "Feminina.mp3"
)
DEFAULT_REFERENCE_VOICE_SHA256 = "20d890c2a97bc2dd97b4ea4021e83681c00830c7b2e8894f944776e44eacde9f"
MODEL_ID = "ResembleAI/Chatterbox-Multilingual-pt-br"
MODEL_LANGUAGE_ID = "pt"
FEMININA_PROFILE_NAME = "feminina-v1"
FEMININA_PROFILE = {
    "max_chars": DEFAULT_MAX_CHARS,
    "silence_seconds": SILENCE_SECONDS,
    "exaggeration": 0.55,
    "cfg_weight": 0.502,
    "temperature": 0.8,
    "repetition_penalty": 1.2,
    "min_p": 0.114,
    "top_p": 1.0,
    "seed": 20260713,
}
FEMININA_PROFILE_CALIBRATION = {
    "selection_id": "cross-prompt-selection-minp-final-2026-07-14",
    "selection_sha256": "656a9e32a603967c9dc2dd3dffd61f67248ba53aeb955b7f00f79ef6aba6a753",
    "corpus_sha256": "908c271cc1e268510910ee5bba119dc69dbaa8686538a97a87c61739ac5d09a6",
    "winner_id": "minp-0-114-temp-0-80",
    "main_prompt_wav_sha256": "5c9e0f38e679c03b99ca0c01318f0a668d47f14e453510a89dcad927d416471b",
    "device": "cuda",
    "model_root": str(DEFAULT_MODEL_ROOT),
    "model": {
        "t3_sha256": "074aaf65255eb9cb960288f7cc72e09d3b5008f6e0b14868c0d4e5b0bd7cbb6c",
        "s3gen_sha256": "f7abce4b196dae2d08d9296cbebc6521b046079577643b42a19a03499d08721e",
        "voice_encoder_sha256": "4b16d836bc598509860f6fa068165a8bb5e9ac84f05582dfcf278a5a372879f1",
    },
    "chatterbox_tts_version": "0.1.7",
}
RENDER_JOURNAL_SCHEMA_VERSION = "2.0"
RENDER_SEED_STRATEGY = "per-segment-index-v1"
RENDER_RETRY_ATTEMPTS = 3
RENDER_RETRY_SEED_STEP = 1_000_003
EXPECTED_WAV_PARAMS = (CHANNELS, SAMPLE_WIDTH, SAMPLE_RATE)


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


def chatterbox_package_version() -> str | None:
    try:
        return version("chatterbox-tts")
    except PackageNotFoundError:
        return None


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def read_json(path: Path, label: str) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read {label} {path}: {error}") from error


def render_identity(
    args: argparse.Namespace,
    profile: str,
    model_hashes: dict[str, str],
    package_version: str | None,
    voice_reference_sha256: str,
    device: str,
) -> dict:
    return {
        "engine": "chatterbox-multilingual-v3-pt-br",
        "profile": profile,
        "model": {
            "id": MODEL_ID,
            **model_hashes,
        },
        "runtime": {
            "chatterbox_tts_version": package_version,
            "renderer_sha256": sha256_file(Path(__file__).resolve()),
        },
        "voice_reference_sha256": voice_reference_sha256,
        "language": "pt-BR",
        "model_language_id": MODEL_LANGUAGE_ID,
        "device": device,
        "sample_rate": SAMPLE_RATE,
        "text_policy": {
            "name": "line-delimited-v1",
            "max_chars": args.max_chars,
        },
        "generation": {
            "exaggeration": args.exaggeration,
            "cfg_weight": args.cfg_weight,
            "temperature": args.temperature,
            "repetition_penalty": args.repetition_penalty,
            "min_p": args.min_p,
            "top_p": args.top_p,
            "seed": args.seed,
            "seed_strategy": RENDER_SEED_STRATEGY,
        },
    }


def new_render_journal(
    identity: dict,
    input_file: str,
    input_sha256: str,
    assembly_identity: dict | None = None,
) -> dict:
    return {
        "schema_version": RENDER_JOURNAL_SCHEMA_VERSION,
        "status": "incomplete",
        "segment_render_identity": identity,
        "assembly_identity": assembly_identity or {},
        "input_file": input_file,
        "input_sha256": input_sha256,
        "segments": [],
    }


def load_render_journal(
    path: Path,
    identity: dict | None,
) -> tuple[dict, dict[int, dict]]:
    journal = read_json(path, "audio render journal")
    if not isinstance(journal, dict):
        raise RuntimeError(f"Audio render journal must be an object: {path}")
    if journal.get("schema_version") != RENDER_JOURNAL_SCHEMA_VERSION:
        raise RuntimeError(
            f"Audio render journal has an unsupported schema: {path}. Use --overwrite to restart."
        )
    previous_identity = journal.get("segment_render_identity")
    if identity is not None and previous_identity != identity and not compatible_render_identity(
        previous_identity,
        identity,
    ):
        raise RuntimeError(
            "Audio render journal does not match the selected model, voice, renderer, or generation "
            "settings. Use --overwrite to restart."
        )
    if identity is not None and previous_identity != identity:
        previous_runtime = previous_identity.get("runtime")
        current_runtime = identity.get("runtime")
        if (
            not isinstance(previous_runtime, dict)
            or not isinstance(current_runtime, dict)
            or not isinstance(previous_runtime.get("renderer_sha256"), str)
            or not isinstance(current_runtime.get("renderer_sha256"), str)
        ):
            raise RuntimeError(
                f"Audio render journal has an invalid render identity: {path}. "
                "Use --overwrite to restart."
            )
        journal["renderer_migration"] = {
            "from_renderer_sha256": previous_runtime["renderer_sha256"],
            "to_renderer_sha256": current_runtime["renderer_sha256"],
            "reason": "The segment render contract is unchanged; only renderer orchestration changed.",
        }
    records = journal.get("segments")
    if not isinstance(records, list):
        raise RuntimeError(f"Audio render journal segments must be an array: {path}")
    by_index: dict[int, dict] = {}
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get("index"), int):
            raise RuntimeError(f"Audio render journal has an invalid segment record: {path}")
        index = record["index"]
        if index <= 0 or index in by_index:
            raise RuntimeError(f"Audio render journal has duplicate or invalid segment indexes: {path}")
        by_index[index] = record
    return journal, by_index


def compatible_render_identity(previous: object, current: dict) -> bool:
    if not isinstance(previous, dict):
        return False
    previous_runtime = previous.get("runtime")
    current_runtime = current.get("runtime")
    if not isinstance(previous_runtime, dict) or not isinstance(current_runtime, dict):
        return False
    previous_copy = json.loads(json.dumps(previous))
    current_copy = json.loads(json.dumps(current))
    previous_copy["runtime"].pop("renderer_sha256", None)
    current_copy["runtime"].pop("renderer_sha256", None)
    return previous_copy == current_copy


def assembly_identity(
    segments: list[object],
    narration_plan: dict | None,
    silence_seconds: float,
) -> dict:
    return {
        "policy": (
            narration_plan.get("policy")
            if isinstance(narration_plan, dict)
            else {"name": "uniform-silence-v1", "seconds": silence_seconds}
        ),
        "narration_plan_sha256": (
            sha256_bytes(
                json.dumps(narration_plan, ensure_ascii=False, sort_keys=True).encode("utf-8")
            )
            if narration_plan is not None
            else None
        ),
        "segments": [
            {
                "index": index,
                "semantic_id": getattr(segment, "semantic_id", None)
                or f"line-{segment.line_number}",
                "text_sha256": sha256_bytes(segment.text.encode("utf-8")),
                "pause_after_seconds": (
                    segment.pause_after_seconds
                    if segment.pause_after_seconds is not None
                    else silence_seconds
                ),
            }
            for index, segment in enumerate(segments, start=1)
        ],
    }


def wav_details(path: Path) -> tuple[float, str]:
    try:
        with wave.open(str(path), "rb") as rendered:
            params = (
                rendered.getnchannels(),
                rendered.getsampwidth(),
                rendered.getframerate(),
            )
            frames = rendered.getnframes()
    except (OSError, wave.Error) as error:
        raise RuntimeError(f"Cannot read rendered WAV {path}: {error}") from error
    if params != EXPECTED_WAV_PARAMS:
        raise RuntimeError(f"Unexpected WAV format: {path}")
    if frames <= 0:
        raise RuntimeError(f"Rendered WAV is empty: {path}")
    return frames / SAMPLE_RATE, sha256_file(path)


def segment_seed(seed: int | None, index: int) -> int | None:
    return None if seed is None else seed + index - 1


def render_retry_seed(seed: int | None, index: int, attempt_offset: int) -> int | None:
    if attempt_offset <= 0:
        return seed
    base = seed if seed is not None else index
    return base + (attempt_offset * RENDER_RETRY_SEED_STEP)


def segment_record(
    index: int,
    segment: object,
    segment_path: Path,
    output_dir: Path,
    seed: int | None,
    render_attempts: list[dict] | None = None,
) -> dict:
    duration, audio_sha256 = wav_details(segment_path)
    speech = validate_speech_wav(segment_path)
    record = {
        "index": index,
        "semantic_id": getattr(segment, "semantic_id", None)
        or f"line-{segment.line_number}",
        "locutor_line": segment.line_number,
        "character_count": len(segment.text),
        "text_sha256": sha256_bytes(segment.text.encode("utf-8")),
        "warnings": list(segment.warnings),
        "path": segment_path.relative_to(output_dir).as_posix(),
        "audio_sha256": audio_sha256,
        "duration_seconds": round(duration, 3),
        "speech": speech,
        "seed": seed,
    }
    if render_attempts is not None:
        record["render_attempts"] = render_attempts
    return record


def segment_speech_identity(segment: object) -> tuple[int, str, tuple[str, ...]]:
    return (
        len(segment.text),
        sha256_bytes(segment.text.encode("utf-8")),
        tuple(segment.warnings),
    )


def record_speech_identity(record: object) -> tuple[int, str, tuple[str, ...]] | None:
    if not isinstance(record, dict):
        return None
    character_count = record.get("character_count")
    text_sha256 = record.get("text_sha256")
    warnings = record.get("warnings")
    if (
        not isinstance(character_count, int)
        or not isinstance(text_sha256, str)
        or not isinstance(warnings, list)
        or not all(isinstance(warning, str) for warning in warnings)
    ):
        return None
    return character_count, text_sha256, tuple(warnings)


def record_segment_path(record: object, output_dir: Path) -> Path:
    if (
        not isinstance(record, dict)
        or not isinstance(record.get("path"), str)
        or not isinstance(record.get("index"), int)
        or record["index"] <= 0
    ):
        raise RuntimeError("Audio render journal has no usable segment path.")
    relative_path = Path(record["path"])
    expected_relative_path = Path("segments") / f"segment-{record['index']:04d}.wav"
    if relative_path.is_absolute() or relative_path.as_posix() != expected_relative_path.as_posix():
        raise RuntimeError("Audio render journal segment path is not canonical.")
    path = output_dir / expected_relative_path
    require_under(path, output_dir / "segments", "Audio render journal segment path")
    return path


def reusable_rendered_audio(record: object, segment_path: Path) -> bool:
    if not isinstance(record, dict):
        return False
    try:
        duration, audio_sha256 = wav_details(segment_path)
        validate_speech_wav(segment_path)
    except RuntimeError:
        return False
    return (
        record.get("audio_sha256") == audio_sha256
        and record.get("duration_seconds") == round(duration, 3)
    )


def reusable_seed(record: dict, expected_seed: int | None) -> bool:
    if record.get("seed") == expected_seed:
        return True
    attempts = record.get("render_attempts")
    index = record.get("index")
    if isinstance(attempts, list) and isinstance(index, int) and index > 0:
        for offset, attempt in enumerate(attempts):
            if not isinstance(attempt, dict):
                return False
            if attempt.get("seed") != render_retry_seed(expected_seed, index, offset):
                return False
            if (
                attempt.get("status") == "accepted"
                and attempt.get("seed") == record.get("seed")
            ):
                return True
    reused_from = record.get("reused_from")
    source_index = reused_from.get("source_index") if isinstance(reused_from, dict) else None
    source_path = reused_from.get("source_path") if isinstance(reused_from, dict) else None
    expected_source_path = (
        f"segments/segment-{source_index:04d}.wav"
        if isinstance(source_index, int) and source_index > 0
        else None
    )
    return (
        isinstance(reused_from, dict)
        and reused_from.get("strategy") == "cross-index-v1"
        and reused_from.get("expected_seed") == expected_seed
        and reused_from.get("source_seed") == record.get("seed")
        and source_path == expected_source_path
        and reused_from.get("source_audio_sha256") == record.get("audio_sha256")
    )


def reusable_segment_record(
    record: object,
    index: int,
    segment: object,
    segment_path: Path,
    output_dir: Path,
    seed: int | None,
) -> bool:
    if not isinstance(record, dict):
        return False
    expected = {
        "index": index,
        "semantic_id": getattr(segment, "semantic_id", None)
        or f"line-{segment.line_number}",
        "locutor_line": segment.line_number,
        "character_count": len(segment.text),
        "text_sha256": sha256_bytes(segment.text.encode("utf-8")),
        "warnings": list(segment.warnings),
        "path": segment_path.relative_to(output_dir).as_posix(),
    }
    if any(record.get(key) != value for key, value in expected.items()):
        return False
    if not reusable_seed(record, seed):
        return False
    return reusable_rendered_audio(record, segment_path)


def copy_or_link_atomically(source: Path, destination: Path) -> None:
    if source == destination:
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(f".{destination.stem}.{os.getpid()}.tmp{destination.suffix}")
    try:
        try:
            os.link(source, temporary)
        except OSError:
            shutil.copy2(source, temporary)
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()


def reflow_reuse_provenance(record: dict, expected_seed: int | None) -> dict:
    return {
        "strategy": "cross-index-v1",
        "source_index": record["index"],
        "source_semantic_id": record.get("semantic_id"),
        "source_path": record["path"],
        "source_audio_sha256": record["audio_sha256"],
        "source_seed": record["seed"],
        "expected_seed": expected_seed,
    }


def prepare_reflow_reuse_sources(
    previous_records: dict[int, dict],
    segments: list[object],
    output_dir: Path,
    cache_dir: Path,
) -> dict[tuple[int, str, tuple[str, ...]], tuple[dict, Path]]:
    target_counts: dict[tuple[int, str, tuple[str, ...]], int] = {}
    for segment in segments:
        key = segment_speech_identity(segment)
        target_counts[key] = target_counts.get(key, 0) + 1

    candidates: dict[tuple[int, str, tuple[str, ...]], list[tuple[dict, Path]]] = {}
    for record in previous_records.values():
        key = record_speech_identity(record)
        if key is None or target_counts.get(key) != 1:
            continue
        if (
            not isinstance(record.get("index"), int)
            or record["index"] <= 0
            or (record.get("seed") is not None and not isinstance(record.get("seed"), int))
        ):
            continue
        try:
            source_path = record_segment_path(record, output_dir)
        except RuntimeError:
            continue
        if not reusable_rendered_audio(record, source_path):
            continue
        candidates.setdefault(key, []).append((record, source_path))

    sources: dict[tuple[int, str, tuple[str, ...]], tuple[dict, Path]] = {}
    for key, matches in candidates.items():
        if len(matches) != 1:
            continue
        record, source_path = matches[0]
        cached_path = cache_dir / f"segment-{record['index']:04d}.wav"
        copy_or_link_atomically(source_path, cached_path)
        sources[key] = record, cached_path
    return sources


def render_segment_atomically(
    model: object,
    text: str,
    target: Path,
    voice_reference: Path,
    exaggeration: float,
    cfg_weight: float,
    temperature: float,
    repetition_penalty: float,
    min_p: float,
    top_p: float,
) -> dict[str, float]:
    temporary = target.with_name(f".{target.stem}.{os.getpid()}.tmp.wav")
    try:
        render_segment(
            model,
            text,
            temporary,
            voice_reference,
            exaggeration,
            cfg_weight,
            temperature,
            repetition_penalty,
            min_p,
            top_p,
        )
        wav_details(temporary)
        speech = validate_speech_wav(temporary)
        os.replace(temporary, target)
        return speech
    finally:
        if temporary.exists():
            temporary.unlink()


def render_segment_with_retries(
    *,
    segment_index: int,
    model: object,
    text: str,
    target: Path,
    voice_reference: Path,
    exaggeration: float,
    cfg_weight: float,
    temperature: float,
    repetition_penalty: float,
    min_p: float,
    top_p: float,
    seed: int | None,
    device: str,
) -> tuple[int | None, list[dict]]:
    attempts: list[dict] = []
    for attempt_offset in range(RENDER_RETRY_ATTEMPTS):
        attempt_number = attempt_offset + 1
        attempt_seed = render_retry_seed(seed, segment_index, attempt_offset)
        seed_torch(attempt_seed, device)
        try:
            speech = render_segment_atomically(
                model,
                text,
                target,
                voice_reference,
                exaggeration,
                cfg_weight,
                temperature,
                repetition_penalty,
                min_p,
                top_p,
            )
        except RuntimeError as error:
            attempts.append(
                {
                    "attempt": attempt_number,
                    "seed": attempt_seed,
                    "status": "rejected",
                    "reason": str(error),
                }
            )
            continue
        attempts.append(
            {
                "attempt": attempt_number,
                "seed": attempt_seed,
                "status": "accepted",
                "speech": speech,
            }
        )
        return attempt_seed, attempts
    seeds = ", ".join(str(attempt["seed"]) for attempt in attempts)
    last_reason = attempts[-1]["reason"] if attempts else "no attempts were recorded"
    raise RuntimeError(
        f"render rejected after {RENDER_RETRY_ATTEMPTS} deterministic attempt(s) "
        f"for segment {segment_index}; seeds=[{seeds}]; last rejection: {last_reason}"
    )


def join_wavs_atomically(
    segment_paths: list[Path],
    target: Path,
    boundary_pauses: list[float],
) -> float:
    temporary = target.with_name(f".{target.stem}.{os.getpid()}.tmp{target.suffix}")
    try:
        duration = join_wavs(segment_paths, temporary, boundary_pauses=boundary_pauses)
        validate_speech_wav(temporary)
        os.replace(temporary, target)
        return duration
    finally:
        if temporary.exists():
            temporary.unlink()


def transcode_atomically(final_wav: Path, target: Path, audio_format: str) -> None:
    temporary = target.with_name(f".{target.stem}.{os.getpid()}.tmp{target.suffix}")
    try:
        transcode(final_wav, temporary, audio_format)
        os.replace(temporary, target)
    finally:
        if temporary.exists():
            temporary.unlink()


def apply_publication_tempo_atomically(
    master_wav: Path,
    target: Path,
    tempo: float,
) -> float:
    temporary = target.with_name(f".{target.stem}.{os.getpid()}.tmp{target.suffix}")
    try:
        apply_publication_tempo(master_wav, temporary, tempo)
        validate_speech_wav(temporary)
        os.replace(temporary, target)
        duration, _ = wav_details(target)
        return duration
    finally:
        if temporary.exists():
            temporary.unlink()


def replacement_journal_path(path: Path) -> Path:
    return path.with_name(f"{path.stem}.next{path.suffix}")


def require_under(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError as error:
        raise RuntimeError(f"{label} must remain under {root}: {path}") from error


def relative_to(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def copy_or_link(source: Path, destination: Path) -> None:
    if destination.exists():
        if sha256_file(destination) != sha256_file(source):
            raise RuntimeError(f"Refusing to replace model runtime asset: {destination}")
        return
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def prepare_runtime_model(model_root: Path) -> dict[str, Path]:
    base_root = model_root / "base"
    ptbr_root = model_root / "pt-br"
    source_paths = {
        "voice_encoder": base_root / "ve.pt",
        "t3": ptbr_root / "t3_pt_br.safetensors",
        "s3gen": ptbr_root / "s3gen_v3.pt",
        "tokenizer": ptbr_root / "grapheme_mtl_merged_expanded_v1.json",
    }
    missing = [str(path) for path in source_paths.values() if not path.is_file()]
    if missing:
        raise RuntimeError(
            "Chatterbox PT-BR model assets are missing: "
            + ", ".join(missing)
            + ". Install the local Chatterbox PT-BR runtime and model files before rendering."
        )
    runtime = model_root / "runtime-pt-br"
    runtime_paths = {
        "voice_encoder": runtime / "ve.pt",
        "t3": runtime / "t3_pt_br.safetensors",
        "s3gen": runtime / "s3gen.pt",
        "tokenizer": runtime / "grapheme_mtl_merged_expanded_v1.json",
    }
    for key, source in source_paths.items():
        copy_or_link(source, runtime_paths[key])
    return runtime_paths


def select_device(value: str) -> str:
    try:
        import torch
    except ImportError as error:
        raise RuntimeError(
            "Chatterbox and Torch are unavailable. Run this script with the Chatterbox venv."
        ) from error
    if value == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if value == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA was requested but is unavailable in this Chatterbox environment.")
    return value


def copy_cuda_checkpoint_tensors(
    module: object,
    checkpoint_keys: list[str],
    get_tensor: object,
    ignored_missing: set[str] | None = None,
) -> None:
    import torch

    targets = module.state_dict()
    expected = set(targets)
    available = set(checkpoint_keys)
    ignored = ignored_missing or set()
    missing = expected - available - ignored
    unexpected = available - expected
    if missing or unexpected:
        raise RuntimeError(
            "Chatterbox checkpoint is incompatible with the installed runtime: "
            f"missing={sorted(missing)}, unexpected={sorted(unexpected)}"
        )

    with torch.no_grad():
        for index, key in enumerate(checkpoint_keys, start=1):
            target = targets[key]
            value = get_tensor(key)
            if tuple(target.shape) != tuple(value.shape):
                raise RuntimeError(
                    "Chatterbox checkpoint tensor shape is incompatible with the installed runtime: "
                    f"{key}: expected {tuple(target.shape)}, got {tuple(value.shape)}"
                )
            target.copy_(value)
            del value
            if index % 256 == 0:
                torch.cuda.synchronize()
    torch.cuda.synchronize()


def assert_no_meta_tensors(model: object, label: str) -> None:
    meta_tensors: list[str] = []
    for name, value in model.named_parameters():
        if value.is_meta:
            meta_tensors.append(name)
    for name, value in model.named_buffers():
        if value.is_meta:
            meta_tensors.append(name)
    for module_name, module in model.named_modules():
        for attribute, value in vars(module).items():
            if (
                attribute not in {"_parameters", "_buffers", "_modules"}
                and hasattr(value, "is_meta")
                and value.is_meta
            ):
                meta_tensors.append(f"{module_name}.{attribute}".strip("."))
    if meta_tensors:
        raise RuntimeError(
            f"{label} has unresolved meta tensors: {', '.join(meta_tensors)}"
        )


def capture_legacy_cuda_compatibility(
    paths: dict[str, Path],
    device: str,
    T3: object,
    T3Config: object,
    S3Gen: object,
    MTLTokenizer: object,
    VoiceEncoder: object,
    ChatterboxMultilingualTTS: object,
) -> dict[str, object]:
    """Capture CPU construction state required by the historical CUDA loader."""
    import gc
    import torch

    voice_encoder = VoiceEncoder()
    del voice_encoder

    t3 = T3(T3Config.multilingual())
    rotary_embedding = t3.tfmr.rotary_emb
    t3_buffers = {
        "inv_freq": rotary_embedding.inv_freq.detach().clone(),
        "original_inv_freq": rotary_embedding.original_inv_freq.detach().clone(),
        "attention_scaling": rotary_embedding.attention_scaling,
    }
    del rotary_embedding
    del t3
    gc.collect()

    s3gen = S3Gen()
    relative_position = {
        name: module.pe.detach().clone()
        for name, module in s3gen.named_modules()
        if module.__class__.__name__ == "EspnetRelPositionalEncoding"
    }
    s3gen_buffers = {
        "mel_filters": s3gen.tokenizer._mel_filters.detach().clone(),
        "window": s3gen.tokenizer.window.detach().clone(),
        "freqs_cis": s3gen.tokenizer.encoder.freqs_cis.detach().clone(),
        "trim_fade": s3gen.trim_fade.detach().clone(),
        "relative_position": relative_position,
    }
    del s3gen
    gc.collect()

    tokenizer = MTLTokenizer(str(paths["tokenizer"]))
    rng_before_watermarker = torch.get_rng_state()
    watermarker_owner = ChatterboxMultilingualTTS(
        None,
        None,
        None,
        tokenizer,
        device,
        conds=None,
    )
    rng_after_legacy_construction = torch.get_rng_state()
    del watermarker_owner
    del tokenizer
    gc.collect()
    return {
        "t3": t3_buffers,
        "s3gen": s3gen_buffers,
        "rng_before_watermarker": rng_before_watermarker,
        "rng_after_legacy_construction": rng_after_legacy_construction,
    }


def apply_legacy_cuda_buffers(
    t3: object | None,
    s3gen: object | None,
    device: str,
    compatibility: dict[str, object],
) -> None:
    if t3 is not None:
        t3_buffers = compatibility["t3"]
        if not isinstance(t3_buffers, dict):
            raise RuntimeError("Legacy T3 compatibility buffers are invalid.")
        rotary_embedding = t3.tfmr.rotary_emb
        rotary_embedding.register_buffer(
            "inv_freq",
            t3_buffers["inv_freq"].to(device),
            persistent=False,
        )
        rotary_embedding.register_buffer(
            "original_inv_freq",
            t3_buffers["original_inv_freq"].to(device),
            persistent=False,
        )
        rotary_embedding.attention_scaling = t3_buffers["attention_scaling"]

    if s3gen is not None:
        s3gen_buffers = compatibility["s3gen"]
        if not isinstance(s3gen_buffers, dict):
            raise RuntimeError("Legacy S3Gen compatibility buffers are invalid.")
        tokenizer = s3gen.tokenizer
        tokenizer._mel_filters = s3gen_buffers["mel_filters"].to(device)
        tokenizer.window = s3gen_buffers["window"].to(device)
        tokenizer.encoder.freqs_cis = s3gen_buffers["freqs_cis"].to(device)
        s3gen.trim_fade = s3gen_buffers["trim_fade"].to(device)

        relative_position = s3gen_buffers["relative_position"]
        if not isinstance(relative_position, dict):
            raise RuntimeError("Legacy S3Gen position buffers are invalid.")
        modules = dict(s3gen.named_modules())
        for name, value in relative_position.items():
            module = modules.get(name)
            if module is None:
                raise RuntimeError(f"Missing S3Gen position buffer target: {name}")
            module.pe = value.to(device)


def load_ptbr_model_cuda(
    paths: dict[str, Path],
    device: str,
    T3: object,
    T3Config: object,
    S3Gen: object,
    MTLTokenizer: object,
    VoiceEncoder: object,
    ChatterboxMultilingualTTS: object,
) -> object:
    import torch
    from safetensors import safe_open

    compatibility = capture_legacy_cuda_compatibility(
        paths,
        device,
        T3,
        T3Config,
        S3Gen,
        MTLTokenizer,
        VoiceEncoder,
        ChatterboxMultilingualTTS,
    )

    voice_encoder = VoiceEncoder()
    voice_encoder.load_state_dict(
        torch.load(paths["voice_encoder"], map_location="cpu", weights_only=True)
    )
    voice_encoder.to(device).eval()

    with torch.device("meta"):
        t3 = T3(T3Config.multilingual())
    t3.to_empty(device=device)
    with safe_open(str(paths["t3"]), framework="pt", device="cpu") as checkpoint:
        checkpoint_keys = list(checkpoint.keys())
        copy_cuda_checkpoint_tensors(t3, checkpoint_keys, checkpoint.get_tensor)
    apply_legacy_cuda_buffers(t3, s3gen=None, device=device, compatibility=compatibility)
    t3.eval()
    assert_no_meta_tensors(t3, "T3")

    with torch.device("meta"):
        s3gen = S3Gen()
    s3gen.to_empty(device=device)
    s3gen_checkpoint = torch.load(
        paths["s3gen"],
        map_location="cpu",
        weights_only=True,
        mmap=True,
    )
    copy_cuda_checkpoint_tensors(
        s3gen,
        list(s3gen_checkpoint),
        s3gen_checkpoint.__getitem__,
        ignored_missing={"tokenizer._mel_filters", "tokenizer.window"},
    )
    del s3gen_checkpoint
    apply_legacy_cuda_buffers(t3=None, s3gen=s3gen, device=device, compatibility=compatibility)
    s3gen.eval()
    assert_no_meta_tensors(s3gen, "S3Gen")

    tokenizer = MTLTokenizer(str(paths["tokenizer"]))
    torch.set_rng_state(compatibility["rng_before_watermarker"])
    model = ChatterboxMultilingualTTS(
        t3,
        s3gen,
        voice_encoder,
        tokenizer,
        device,
        conds=None,
    )
    torch.set_rng_state(compatibility["rng_after_legacy_construction"])
    return model


def load_ptbr_model(
    model_root: Path,
    device: str,
    runtime_paths: dict[str, Path] | None = None,
) -> object:
    try:
        import torch
        from chatterbox.models.s3gen import S3Gen
        from chatterbox.models.t3 import T3
        from chatterbox.models.t3.modules.t3_config import T3Config
        from chatterbox.models.tokenizers import MTLTokenizer
        from chatterbox.models.voice_encoder import VoiceEncoder
        from chatterbox.mtl_tts import ChatterboxMultilingualTTS
        from safetensors.torch import load_file as load_safetensors
    except ImportError as error:
        raise RuntimeError(
            "Chatterbox PT-BR dependencies are unavailable. Run this script with the Chatterbox venv."
        ) from error

    paths = runtime_paths if runtime_paths is not None else prepare_runtime_model(model_root)
    if device == "cuda":
        model = load_ptbr_model_cuda(
            paths,
            device,
            T3,
            T3Config,
            S3Gen,
            MTLTokenizer,
            VoiceEncoder,
            ChatterboxMultilingualTTS,
        )
        if model.sr != SAMPLE_RATE:
            raise RuntimeError(
                f"Unexpected Chatterbox sample rate {model.sr}; expected {SAMPLE_RATE}."
            )
        return model

    voice_encoder = VoiceEncoder()
    voice_encoder.load_state_dict(
        torch.load(paths["voice_encoder"], map_location="cpu", weights_only=True)
    )
    voice_encoder.to(device).eval()

    t3 = T3(T3Config.multilingual())
    t3_state = load_safetensors(paths["t3"])
    if "model" in t3_state:
        t3_state = t3_state["model"][0]
    t3.load_state_dict(t3_state)
    t3.to(device).eval()

    s3gen = S3Gen()
    missing, unexpected = s3gen.load_state_dict(
        torch.load(paths["s3gen"], map_location="cpu", weights_only=True),
        strict=False,
    )
    allowed_missing = {"tokenizer._mel_filters", "tokenizer.window"}
    if set(missing) != allowed_missing or unexpected:
        raise RuntimeError(
            "Chatterbox PT-BR S3Gen checkpoint is incompatible with the installed runtime: "
            f"missing={missing}, unexpected={unexpected}"
        )
    s3gen.to(device).eval()
    tokenizer = MTLTokenizer(str(paths["tokenizer"]))
    model = ChatterboxMultilingualTTS(t3, s3gen, voice_encoder, tokenizer, device, conds=None)
    if model.sr != SAMPLE_RATE:
        raise RuntimeError(
            f"Unexpected Chatterbox sample rate {model.sr}; expected {SAMPLE_RATE}."
        )
    return model


def seed_torch(value: int | None, device: str) -> None:
    if value is None:
        return
    import torch

    torch.manual_seed(value)
    if device == "cuda":
        torch.cuda.manual_seed(value)
        torch.cuda.manual_seed_all(value)


def selected_profile(
    args: argparse.Namespace,
    voice_reference: Path,
    model_root: Path,
    device: str,
    model_hashes: dict[str, str],
    package_version: str | None,
) -> str:
    profile_values = (
        args.max_chars,
        args.silence_seconds,
        args.exaggeration,
        args.cfg_weight,
        args.temperature,
        args.repetition_penalty,
        args.min_p,
        args.top_p,
        args.seed,
    )
    expected_values = (
        FEMININA_PROFILE["max_chars"],
        FEMININA_PROFILE["silence_seconds"],
        FEMININA_PROFILE["exaggeration"],
        FEMININA_PROFILE["cfg_weight"],
        FEMININA_PROFILE["temperature"],
        FEMININA_PROFILE["repetition_penalty"],
        FEMININA_PROFILE["min_p"],
        FEMININA_PROFILE["top_p"],
        FEMININA_PROFILE["seed"],
    )
    if (
        voice_reference == DEFAULT_REFERENCE_VOICE.resolve()
        and sha256_file(voice_reference) == DEFAULT_REFERENCE_VOICE_SHA256
        and model_root == DEFAULT_MODEL_ROOT.resolve()
        and device == FEMININA_PROFILE_CALIBRATION["device"]
        and model_hashes == FEMININA_PROFILE_CALIBRATION["model"]
        and package_version == FEMININA_PROFILE_CALIBRATION["chatterbox_tts_version"]
        and profile_values == expected_values
    ):
        return FEMININA_PROFILE_NAME
    return "custom"


def render_segment(
    model: object,
    text: str,
    target: Path,
    voice_reference: Path,
    exaggeration: float,
    cfg_weight: float,
    temperature: float,
    repetition_penalty: float,
    min_p: float,
    top_p: float,
) -> None:
    try:
        import numpy as np
    except ImportError as error:
        raise RuntimeError("numpy is required for Chatterbox rendering.") from error
    waveform = model.generate(
        text,
        language_id=MODEL_LANGUAGE_ID,
        audio_prompt_path=str(voice_reference),
        exaggeration=exaggeration,
        cfg_weight=cfg_weight,
        temperature=temperature,
        repetition_penalty=repetition_penalty,
        min_p=min_p,
        top_p=top_p,
    )
    if hasattr(waveform, "detach"):
        waveform = waveform.detach().cpu().numpy()
    samples = np.asarray(waveform, dtype=np.float32).reshape(-1)
    if samples.size == 0:
        raise RuntimeError("Chatterbox did not generate audio for a non-empty segment.")
    frames = (samples.clip(-1, 1) * 32767).astype(np.int16).tobytes()
    write_wav(target, frames)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Render local Chatterbox Multilingual V3 Brazilian Portuguese audiobook audio."
    )
    parser.add_argument("--input-file", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--book-root", type=Path)
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument(
        "--chapters",
        help="Comma-separated narration plan chapter IDs to render and assemble without remounting the full book.",
    )
    parser.add_argument(
        "--remount",
        action="store_true",
        help="Rebuild verified chapter and book delivery audio without TTS synthesis.",
    )
    parser.add_argument("--narrator-changes", type=Path)
    parser.add_argument("--require-lineage", action="store_true")
    parser.add_argument("--narrator-review", type=Path)
    parser.add_argument("--require-quality", action="store_true")
    parser.add_argument("--model-root", type=Path, default=DEFAULT_MODEL_ROOT)
    parser.add_argument("--voice-reference", type=Path, default=DEFAULT_REFERENCE_VOICE)
    parser.add_argument("--device", choices=("auto", "cuda", "cpu"), default="auto")
    parser.add_argument("--format", choices=("wav", "m4a", "mp3"), default="mp3")
    parser.add_argument("--max-chars", type=int, default=FEMININA_PROFILE["max_chars"])
    parser.add_argument(
        "--silence-seconds",
        type=float,
        default=FEMININA_PROFILE["silence_seconds"],
    )
    parser.add_argument("--exaggeration", type=float, default=FEMININA_PROFILE["exaggeration"])
    parser.add_argument("--cfg-weight", type=float, default=FEMININA_PROFILE["cfg_weight"])
    parser.add_argument("--temperature", type=float, default=FEMININA_PROFILE["temperature"])
    parser.add_argument(
        "--repetition-penalty",
        type=float,
        default=FEMININA_PROFILE["repetition_penalty"],
    )
    parser.add_argument("--min-p", type=float, default=FEMININA_PROFILE["min_p"])
    parser.add_argument("--top-p", type=float, default=FEMININA_PROFILE["top_p"])
    parser.add_argument("--seed", type=int, default=FEMININA_PROFILE["seed"])
    parser.add_argument(
        "--publication-tempo",
        type=float,
        default=DEFAULT_PUBLICATION_TEMPO,
        help="Pitch-preserving delivery cadence applied after immutable 1.0x WAV masters.",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if not 80 <= args.max_chars <= DEFAULT_MAX_CHARS:
        raise SystemExit(f"--max-chars must be between 80 and {DEFAULT_MAX_CHARS}.")
    if args.silence_seconds < 0:
        raise SystemExit("--silence-seconds must not be negative.")
    if args.exaggeration <= 0:
        raise SystemExit("--exaggeration must be positive.")
    if args.cfg_weight <= 0:
        raise SystemExit("--cfg-weight must be positive.")
    if args.temperature <= 0:
        raise SystemExit("--temperature must be positive.")
    if args.repetition_penalty <= 0:
        raise SystemExit("--repetition-penalty must be positive.")
    if not 0 <= args.min_p <= 1:
        raise SystemExit("--min-p must be between 0 and 1.")
    if not 0 < args.top_p <= 1:
        raise SystemExit("--top-p must be greater than 0 and at most 1.")
    try:
        args.publication_tempo = validate_publication_tempo(args.publication_tempo)
    except RuntimeError as error:
        raise SystemExit(str(error)) from error
    if args.remount and args.overwrite:
        raise SystemExit("--remount cannot be combined with --overwrite.")
    if args.remount and args.chapters:
        raise SystemExit("--remount cannot be combined with --chapters.")

    reflow_cache_dir: Path | None = None
    try:
        input_file = args.input_file.expanduser().resolve()
        output_dir = args.output_dir.expanduser().resolve()
        model_root = args.model_root.expanduser().resolve()
        voice_reference = args.voice_reference.expanduser().resolve()
        book_root = resolve_book_paths(args.book_root).assembly_root if args.book_root else None
        if (book_root is None) != args.standalone:
            raise RuntimeError("Use exactly one of --book-root or --standalone.")
        if (
            args.require_lineage
            or args.narrator_changes is not None
            or args.require_quality
            or args.narrator_review is not None
        ) and book_root is None:
            raise RuntimeError(
                "--require-lineage, --narrator-changes, --require-quality, and --narrator-review "
                "require --book-root."
            )
        if args.chapters and book_root is None:
            raise RuntimeError("--chapters requires --book-root.")
        if args.remount and book_root is None:
            raise RuntimeError("--remount requires --book-root.")
        if not input_file.is_file():
            raise RuntimeError(f"Narrator input is missing: {input_file}")
        if not args.remount and not voice_reference.is_file():
            raise RuntimeError(f"Voice reference is missing: {voice_reference}")
        if book_root is not None:
            audio_root = book_root / "audio"
            require_under(input_file, book_root / "text" / "locutor", "Narrator input")
            require_under(output_dir, audio_root, "Audio output")
            try:
                output_dir.relative_to((audio_root / "mock").resolve())
            except ValueError:
                pass
            else:
                raise RuntimeError("Chatterbox audio output must not use audio/mock.")
        narrator_lineage = {"status": "standalone"}
        narrator_quality = {"status": "not-required"}
        narration_plan: dict | None = None
        narration_plan_provenance = {"status": "standalone"}
        if book_root is not None:
            narrator_changes = (
                args.narrator_changes.expanduser().resolve()
                if args.narrator_changes
                else book_root / "metadata" / "narrator-changes.json"
            )
            lineage_errors, provenance = validate_lineage(book_root, narrator_changes, input_file)
            if lineage_errors:
                raise RuntimeError("; ".join(lineage_errors))
            narrator_lineage = {
                **(provenance or {}),
                "path": relative_to(narrator_changes, book_root),
            }
            narrator_review = (
                args.narrator_review.expanduser().resolve()
                if args.narrator_review
                else book_root / "metadata" / "narrator-review.json"
            )
            quality_errors, quality_provenance = validate_review(
                book_root,
                narrator_review,
                input_file,
                narrator_changes,
            )
            if quality_errors:
                raise RuntimeError("; ".join(quality_errors))
            narrator_quality = {
                **(quality_provenance or {}),
                "path": relative_to(narrator_review, book_root),
            }

        text = input_file.read_text(encoding="utf-8")
        if not text.strip():
            raise RuntimeError(f"Input text file is empty: {input_file}")
        if book_root is not None:
            narration_plan_path = book_root / "metadata" / "narration-plan.json"
            plan_errors, plan_provenance = validate_plan(
                book_root,
                input_file,
                narration_plan_path,
            )
            if plan_errors:
                raise RuntimeError("; ".join(plan_errors))
            narration_plan = read_narration_plan(narration_plan_path, "narration plan")
            segments = load_plan_segments(book_root, input_file, narration_plan)
            narration_plan_provenance = {
                **(plan_provenance or {}),
                "path": relative_to(narration_plan_path, book_root),
            }
        else:
            segments = prepare_chatterbox_segments(text, args.max_chars)
        if not segments:
            raise RuntimeError("No renderable narrator chunks were produced.")
        all_segments = segments
        selected_chapter_ids = (
            {chapter.strip() for chapter in args.chapters.split(",") if chapter.strip()}
            if args.chapters
            else set()
        )
        if selected_chapter_ids:
            available_chapter_ids = {
                segment.chapter_id
                for segment in all_segments
                if segment.chapter_id is not None
            }
            unknown_chapters = selected_chapter_ids - available_chapter_ids
            if unknown_chapters:
                raise RuntimeError(
                    "Unknown narration plan chapters: " + ", ".join(sorted(unknown_chapters))
                )
            segments = [
                segment
                for segment in all_segments
                if segment.chapter_id in selected_chapter_ids
            ]
        full_book_render = not selected_chapter_ids

        segments_dir = output_dir / "segments"
        raw_dir = output_dir / "raw"
        master_wav = raw_dir / "audiobook.master.wav"
        final_wav = raw_dir / "audiobook.wav"
        final_audio = output_dir / f"audiobook.{args.format}"
        manifest_path = (
            book_root / "metadata" / "audio-manifest.json"
            if book_root is not None
            else output_dir / "audio-manifest.json"
        )
        journal_path = (
            book_root / "metadata" / "audio-render-journal.json"
            if book_root is not None
            else output_dir / "audio-render-journal.json"
        )
        working_journal_path = (
            replacement_journal_path(journal_path)
            if args.overwrite and full_book_render
            else journal_path
        )
        output_dir.mkdir(parents=True, exist_ok=True)
        segments_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        existing_manifest: dict | None = None
        if args.remount:
            device = None
            runtime_paths = {}
            model_hashes = {}
            package_version = None
            profile = ""
            voice_reference_sha256 = ""
            identity = None
        else:
            device = select_device(args.device)
            runtime_paths = prepare_runtime_model(model_root)
            model_hashes = {
                "t3_sha256": sha256_file(runtime_paths["t3"]),
                "s3gen_sha256": sha256_file(runtime_paths["s3gen"]),
                "voice_encoder_sha256": sha256_file(runtime_paths["voice_encoder"]),
            }
            package_version = chatterbox_package_version()
            profile = selected_profile(
                args,
                voice_reference,
                model_root,
                device,
                model_hashes,
                package_version,
            )
            voice_reference_sha256 = sha256_file(voice_reference)
            identity = render_identity(
                args,
                profile,
                model_hashes,
                package_version,
                voice_reference_sha256,
                device,
            )

        def manifest_path_value(path: Path) -> str:
            return relative_to(path, book_root) if book_root is not None else path.relative_to(output_dir).as_posix()

        input_file_value = (
            manifest_path_value(input_file) if book_root is not None else str(input_file)
        )
        input_sha256 = sha256_file(input_file)
        current_assembly_identity = assembly_identity(
            all_segments,
            narration_plan,
            args.silence_seconds,
        )
        existing_segment_paths = list(segments_dir.glob("segment-*.wav"))
        if args.overwrite and full_book_render:
            if working_journal_path.exists():
                working_journal_path.unlink()
            journal = new_render_journal(
                identity,
                input_file_value,
                input_sha256,
                current_assembly_identity,
            )
            previous_records = {}
        elif journal_path.exists():
            journal, previous_records = load_render_journal(journal_path, identity)
            if args.remount and journal.get("status") != "complete":
                raise RuntimeError("--remount requires a complete audio render journal.")
            previous_assembly_identity = journal.get("assembly_identity")
            if (
                full_book_render
                and manifest_path.exists()
                and previous_assembly_identity == current_assembly_identity
                and not args.remount
            ):
                raise RuntimeError(
                    f"Audio artifacts already exist in {output_dir}. Use --overwrite to replace them."
                )
        else:
            if args.remount:
                raise RuntimeError("--remount requires an existing complete audio render journal.")
            if selected_chapter_ids:
                raise RuntimeError(
                    "Selective chapter rendering requires an existing full-book render journal."
                )
            if (
                existing_segment_paths
                or master_wav.exists()
                or final_wav.exists()
                or final_audio.exists()
                or manifest_path.exists()
            ):
                raise RuntimeError(
                    "Untracked audio artifacts already exist without an audio render journal. "
                    "Refusing to reuse them; use --overwrite to replace them."
                )
            journal = new_render_journal(
                identity,
                input_file_value,
                input_sha256,
                current_assembly_identity,
            )
            previous_records = {}

        if args.remount:
            existing_manifest = read_json(manifest_path, "audio manifest")
            if not isinstance(existing_manifest, dict):
                raise RuntimeError("--remount requires an existing audio manifest object.")

        journal_records = dict(previous_records)
        if not args.remount:
            journal["status"] = "incomplete"
            journal["input_file"] = input_file_value
            journal["input_sha256"] = input_sha256
            journal["segment_render_identity"] = identity
            journal["assembly_identity"] = current_assembly_identity
            journal["segments"] = [
                journal_records[index]
                for index in sorted(journal_records)
            ]
            write_json(working_journal_path, journal)

        model: object | None = None
        segment_paths: list[Path] = []
        segment_records: list[dict] = []
        reused_segments = 0
        rendered_segments = 0
        reflow_sources: dict[tuple[int, str, tuple[str, ...]], tuple[dict, Path]] = {}
        if not args.remount and previous_records:
            reflow_cache_dir = segments_dir / f".reflow-reuse-{os.getpid()}"
            reflow_sources = prepare_reflow_reuse_sources(
                previous_records,
                segments,
                output_dir,
                reflow_cache_dir,
            )
        chapter_last_lines = {
            segment.chapter_id: segment.line_number
            for segment in all_segments
            if segment.chapter_id is not None
        }
        for segment in segments:
            index = segment.line_number
            segment_path = segments_dir / f"segment-{index:04d}.wav"
            previous = previous_records.get(index)
            reflow_source: tuple[dict, Path] | None = None
            render_attempts: list[dict] | None = None
            if args.remount:
                if not isinstance(previous, dict) or "seed" not in previous:
                    raise RuntimeError(
                        f"Cannot remount segment {index} without its recorded render seed."
                    )
                seed = previous["seed"]
                if seed is not None and not isinstance(seed, int):
                    raise RuntimeError(f"Cannot remount segment {index} with an invalid render seed.")
                if not reusable_segment_record(
                    previous,
                    index,
                    segment,
                    segment_path,
                    output_dir,
                    seed,
                ):
                    raise RuntimeError(
                        f"Cannot remount unverified segment {index} "
                        f"(locutor line {segment.line_number})."
                    )
                reused_segments += 1
            else:
                seed = segment_seed(args.seed, index)
                if not (
                    selected_chapter_ids and args.overwrite
                ) and reusable_segment_record(
                    previous,
                    index,
                    segment,
                    segment_path,
                    output_dir,
                    seed,
                ):
                    reused_segments += 1
                else:
                    reflow_source = reflow_sources.get(segment_speech_identity(segment))
                    if reflow_source is not None:
                        source_record, source_path = reflow_source
                        copy_or_link_atomically(source_path, segment_path)
                        seed = source_record["seed"]
                        reused_segments += 1
                    else:
                        if model is None:
                            model = load_ptbr_model(model_root, device, runtime_paths)
                        try:
                            seed, render_attempts = render_segment_with_retries(
                                segment_index=index,
                                model=model,
                                text=segment.text,
                                target=segment_path,
                                voice_reference=voice_reference,
                                exaggeration=args.exaggeration,
                                cfg_weight=args.cfg_weight,
                                temperature=args.temperature,
                                repetition_penalty=args.repetition_penalty,
                                min_p=args.min_p,
                                top_p=args.top_p,
                                seed=seed,
                                device=device,
                            )
                        except RuntimeError as error:
                            raise RuntimeError(
                                f"Chatterbox failed on segment {index} (locutor line {segment.line_number}): "
                                f"{error}"
                            ) from error
                        rendered_segments += 1
            segment_paths.append(segment_path)
            record = segment_record(
                index,
                segment,
                segment_path,
                output_dir,
                seed,
                render_attempts,
            )
            if not args.remount and reflow_source is not None:
                record["reused_from"] = reflow_reuse_provenance(
                    reflow_source[0],
                    segment_seed(args.seed, index),
                )
            segment_records.append(record)
            if not args.remount:
                journal_records[index] = segment_records[-1]
                journal["segments"] = [
                    journal_records[journal_index]
                    for journal_index in sorted(journal_records)
                ]
                write_json(working_journal_path, journal)
            if (
                book_root is not None
                and narration_plan is not None
                and segment.chapter_id is not None
                and chapter_last_lines[segment.chapter_id] == index
            ):
                assemble_chapters(
                    book_root,
                    output_dir,
                    narration_plan,
                    journal,
                    [segment.chapter_id],
                    args.publication_tempo,
                )

        if not full_book_render:
            if book_root is None or narration_plan is None:
                raise RuntimeError("Selective chapter rendering requires a narration plan.")
            complete_segments = all(
                reusable_segment_record(
                    journal_records.get(segment.line_number),
                    segment.line_number,
                    segment,
                    segments_dir / f"segment-{segment.line_number:04d}.wav",
                    output_dir,
                    segment_seed(args.seed, segment.line_number),
                )
                for segment in all_segments
            )
            journal["status"] = "complete" if complete_segments else "incomplete"
            journal["segments"] = [
                journal_records[journal_index]
                for journal_index in sorted(journal_records)
            ]
            journal["assembly_identity"] = current_assembly_identity
            journal.pop("audio_manifest", None)
            journal.pop("audio_manifest_sha256", None)
            write_json(working_journal_path, journal)
            print(
                f"Rendered {rendered_segments} segment(s), reused {reused_segments} segment(s): "
                + ", ".join(sorted(selected_chapter_ids))
            )
            return

        boundary_pauses = [
            (
                segment.pause_after_seconds
                if segment.pause_after_seconds is not None
                else args.silence_seconds
            )
            for segment in segments[:-1]
        ]
        master_duration = join_wavs_atomically(segment_paths, master_wav, boundary_pauses)
        duration = apply_publication_tempo_atomically(
            master_wav,
            final_wav,
            args.publication_tempo,
        )
        transcode_atomically(final_wav, final_audio, args.format)
        if not args.remount:
            expected_paths = set(segment_paths)
            for stale_path in segments_dir.glob("segment-*.wav"):
                if stale_path not in expected_paths:
                    stale_path.unlink()
        delivery_fields = {
            "generated_at": iso_now(),
            "input_file": input_file_value,
            "input_sha256": input_sha256,
            "narrator_lineage": narrator_lineage,
            "narrator_quality": narrator_quality,
            "narration_plan": narration_plan_provenance,
            "output_dir": manifest_path_value(output_dir),
            "silence_seconds": args.silence_seconds,
            "boundary_pauses_seconds": boundary_pauses,
            "resume": {
                "journal": manifest_path_value(journal_path),
                "reused_segments": reused_segments,
                "rendered_segments": rendered_segments,
            },
            "master_wav": manifest_path_value(master_wav),
            "master_wav_sha256": sha256_file(master_wav),
            "master_duration_seconds": round(master_duration, 3),
            "publication": {
                "processor": "ffmpeg-atempo-v1",
                "tempo": args.publication_tempo,
                "preserves_pitch": True,
                "wav": manifest_path_value(final_wav),
                "wav_sha256": sha256_file(final_wav),
                "duration_seconds": round(duration, 3),
            },
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
        if args.remount:
            if existing_manifest is None or existing_manifest.get("render_mode") != "real":
                raise RuntimeError("--remount requires an existing real audio manifest.")
            manifest = dict(existing_manifest)
            manifest.update(delivery_fields)
        else:
            manifest = {
                "schema_version": "1.0",
                **delivery_fields,
                "mock": False,
                "render_mode": "real",
                "engine": "chatterbox-multilingual-v3-pt-br",
                "profile": profile,
                "model": {
                    "id": MODEL_ID,
                    **model_hashes,
                },
                "runtime": {
                    "chatterbox_tts_version": package_version,
                    "renderer_sha256": sha256_file(Path(__file__).resolve()),
                },
                "voice_reference": {
                    "path": str(voice_reference),
                    "sha256": voice_reference_sha256,
                },
                "language": "pt-BR",
                "model_language_id": MODEL_LANGUAGE_ID,
                "device": device,
                "sample_rate": SAMPLE_RATE,
                "text_policy": {
                    "name": (
                        narration_plan.get("policy", {}).get("name")
                        if narration_plan is not None
                        else "line-delimited-v1"
                    ),
                    "max_chars": args.max_chars,
                },
                "exaggeration": args.exaggeration,
                "cfg_weight": args.cfg_weight,
                "temperature": args.temperature,
                "repetition_penalty": args.repetition_penalty,
                "min_p": args.min_p,
                "top_p": args.top_p,
                "seed": args.seed,
                "seed_strategy": RENDER_SEED_STRATEGY,
            }
        if not args.remount and profile == FEMININA_PROFILE_NAME:
            manifest["profile_calibration"] = FEMININA_PROFILE_CALIBRATION
        write_json(manifest_path, manifest)
        if not args.remount:
            journal["status"] = "complete"
            journal["segments"] = segment_records
            journal["assembly_identity"] = current_assembly_identity
            journal["audio_manifest"] = manifest_path_value(manifest_path)
            journal["audio_manifest_sha256"] = sha256_file(manifest_path)
            write_json(working_journal_path, journal)
            if working_journal_path != journal_path:
                os.replace(working_journal_path, journal_path)
    except RuntimeError as error:
        print(f"Cannot render Chatterbox audio: {error}", file=sys.stderr)
        raise SystemExit(1) from error
    finally:
        if reflow_cache_dir is not None and reflow_cache_dir.exists():
            shutil.rmtree(reflow_cache_dir)

    print(
        f"Rendered {rendered_segments} segment(s), reused {reused_segments} segment(s): {final_audio}"
    )
    print(f"Created {manifest_path}")


if __name__ == "__main__":
    main()
