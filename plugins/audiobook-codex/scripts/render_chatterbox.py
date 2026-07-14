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

from chatterbox_text import DEFAULT_MAX_CHARS, prepare_chatterbox_segments
from render_kokoro import (
    CHANNELS,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    SILENCE_SECONDS,
    join_wavs,
    transcode,
    write_wav,
)


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
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def load_ptbr_model(model_root: Path, device: str) -> object:
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

    paths = prepare_runtime_model(model_root)
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
        input_file = args.input_file.expanduser().resolve()
        output_dir = args.output_dir.expanduser().resolve()
        model_root = args.model_root.expanduser().resolve()
        voice_reference = args.voice_reference.expanduser().resolve()
        book_root = args.book_root.expanduser().resolve() if args.book_root else None
        if (book_root is None) != args.standalone:
            raise RuntimeError("Use exactly one of --book-root or --standalone.")
        if not input_file.is_file():
            raise RuntimeError(f"Narrator input is missing: {input_file}")
        if not voice_reference.is_file():
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

        text = input_file.read_text(encoding="utf-8")
        if not text.strip():
            raise RuntimeError(f"Input text file is empty: {input_file}")
        segments = prepare_chatterbox_segments(text, args.max_chars)
        if not segments:
            raise RuntimeError("No renderable narrator chunks were produced.")

        segments_dir = output_dir / "segments"
        raw_dir = output_dir / "raw"
        final_wav = raw_dir / "audiobook.wav"
        final_audio = output_dir / f"audiobook.{args.format}"
        manifest_path = (
            book_root / "metadata" / "audio-manifest.json"
            if book_root is not None
            else output_dir / "audio-manifest.json"
        )
        if (final_wav.exists() or final_audio.exists() or manifest_path.exists()) and not args.overwrite:
            raise RuntimeError(f"Audio artifacts already exist in {output_dir}. Use --overwrite to replace them.")
        output_dir.mkdir(parents=True, exist_ok=True)
        segments_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.mkdir(parents=True, exist_ok=True)

        device = select_device(args.device)
        seed_torch(args.seed, device)
        model = load_ptbr_model(model_root, device)
        segment_paths: list[Path] = []
        segment_records: list[dict] = []
        for index, segment in enumerate(segments, start=1):
            segment_path = segments_dir / f"segment-{index:04d}.wav"
            if segment_path.exists() and not args.overwrite:
                raise RuntimeError(f"Segment already exists: {segment_path}. Use --overwrite to replace it.")
            render_segment(
                model,
                segment.text,
                segment_path,
                voice_reference,
                args.exaggeration,
                args.cfg_weight,
                args.temperature,
                args.repetition_penalty,
                args.min_p,
                args.top_p,
            )
            with wave.open(str(segment_path), "rb") as rendered:
                duration = rendered.getnframes() / rendered.getframerate()
            segment_paths.append(segment_path)
            segment_records.append(
                {
                    "index": index,
                    "locutor_line": segment.line_number,
                    "character_count": len(segment.text),
                    "text_sha256": sha256_bytes(segment.text.encode("utf-8")),
                    "warnings": list(segment.warnings),
                    "path": segment_path,
                    "duration_seconds": round(duration, 3),
                }
            )

        duration = join_wavs(segment_paths, final_wav, args.silence_seconds)
        transcode(final_wav, final_audio, args.format)

        def manifest_path_value(path: Path) -> str:
            return relative_to(path, book_root) if book_root is not None else path.relative_to(output_dir).as_posix()

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
        manifest = {
            "schema_version": "1.0",
            "generated_at": iso_now(),
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
            "input_file": manifest_path_value(input_file) if book_root is not None else str(input_file),
            "input_sha256": sha256_file(input_file),
            "output_dir": manifest_path_value(output_dir),
            "voice_reference": {
                "path": str(voice_reference),
                "sha256": sha256_file(voice_reference),
            },
            "language": "pt-BR",
            "model_language_id": MODEL_LANGUAGE_ID,
            "device": device,
            "sample_rate": SAMPLE_RATE,
            "text_policy": {
                "name": "line-delimited-v1",
                "max_chars": args.max_chars,
            },
            "silence_seconds": args.silence_seconds,
            "exaggeration": args.exaggeration,
            "cfg_weight": args.cfg_weight,
            "temperature": args.temperature,
            "repetition_penalty": args.repetition_penalty,
            "min_p": args.min_p,
            "top_p": args.top_p,
            "seed": args.seed,
            "final_wav": manifest_path_value(final_wav),
            "final_wav_sha256": sha256_file(final_wav),
            "final_audio": manifest_path_value(final_audio),
            "final_audio_sha256": sha256_file(final_audio),
            "duration_seconds": round(duration, 3),
            "segments": [
                {
                    "index": record["index"],
                    "locutor_line": record["locutor_line"],
                    "character_count": record["character_count"],
                    "text_sha256": record["text_sha256"],
                    "warnings": record["warnings"],
                    "path": manifest_path_value(record["path"]),
                    "duration_seconds": record["duration_seconds"],
                }
                for record in segment_records
            ],
        }
        if profile == FEMININA_PROFILE_NAME:
            manifest["profile_calibration"] = FEMININA_PROFILE_CALIBRATION
        write_json(manifest_path, manifest)
    except RuntimeError as error:
        print(f"Cannot render Chatterbox audio: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Rendered {len(segment_paths)} segment(s): {final_audio}")
    print(f"Created {manifest_path}")


if __name__ == "__main__":
    main()
