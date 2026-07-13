from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys
import wave

from render_kokoro import (
    CHANNELS,
    SAMPLE_RATE,
    SAMPLE_WIDTH,
    SILENCE_SECONDS,
    join_wavs,
    split_long_text,
    transcode,
    write_wav,
)


DEFAULT_MODEL_ROOT = Path(r"E:\Pessoal\tts\chatterbox-multilingual-v3\models")
DEFAULT_REFERENCE_VOICE = (
    Path(__file__).resolve().parent.parent / "assets" / "voices" / "Feminina.mp3"
)
MODEL_ID = "ResembleAI/Chatterbox-Multilingual-pt-br"
MODEL_LANGUAGE_ID = "pt"


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


def render_segment(
    model: object,
    text: str,
    target: Path,
    voice_reference: Path,
    exaggeration: float,
    cfg_weight: float,
    temperature: float,
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
    parser.add_argument("--format", choices=("wav", "m4a", "mp3"), default="m4a")
    parser.add_argument("--max-chars", type=int, default=280)
    parser.add_argument("--silence-seconds", type=float, default=SILENCE_SECONDS)
    parser.add_argument("--exaggeration", type=float, default=0.5)
    parser.add_argument("--cfg-weight", type=float, default=0.5)
    parser.add_argument("--temperature", type=float, default=0.8)
    parser.add_argument("--seed", type=int)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args()

    if args.max_chars < 80:
        raise SystemExit("--max-chars must be at least 80.")
    if args.exaggeration <= 0:
        raise SystemExit("--exaggeration must be positive.")
    if args.cfg_weight <= 0:
        raise SystemExit("--cfg-weight must be positive.")
    if args.temperature <= 0:
        raise SystemExit("--temperature must be positive.")

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

        text = input_file.read_text(encoding="utf-8").strip()
        if not text:
            raise RuntimeError(f"Input text file is empty: {input_file}")
        chunks = split_long_text(text, args.max_chars)
        if not chunks:
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
        for index, chunk in enumerate(chunks, start=1):
            segment_path = segments_dir / f"segment-{index:04d}.wav"
            if segment_path.exists() and not args.overwrite:
                raise RuntimeError(f"Segment already exists: {segment_path}. Use --overwrite to replace it.")
            render_segment(
                model,
                chunk,
                segment_path,
                voice_reference,
                args.exaggeration,
                args.cfg_weight,
                args.temperature,
            )
            with wave.open(str(segment_path), "rb") as rendered:
                duration = rendered.getnframes() / rendered.getframerate()
            segment_paths.append(segment_path)
            segment_records.append(
                {
                    "index": index,
                    "text_sha256": sha256_bytes(chunk.encode("utf-8")),
                    "path": segment_path,
                    "duration_seconds": round(duration, 3),
                }
            )

        duration = join_wavs(segment_paths, final_wav, args.silence_seconds)
        transcode(final_wav, final_audio, args.format)

        def manifest_path_value(path: Path) -> str:
            return relative_to(path, book_root) if book_root is not None else path.relative_to(output_dir).as_posix()

        runtime_paths = prepare_runtime_model(model_root)
        manifest = {
            "schema_version": "1.0",
            "generated_at": iso_now(),
            "mock": False,
            "render_mode": "real",
            "engine": "chatterbox-multilingual-v3-pt-br",
            "model": {
                "id": MODEL_ID,
                "t3_sha256": sha256_file(runtime_paths["t3"]),
                "s3gen_sha256": sha256_file(runtime_paths["s3gen"]),
                "voice_encoder_sha256": sha256_file(runtime_paths["voice_encoder"]),
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
            "exaggeration": args.exaggeration,
            "cfg_weight": args.cfg_weight,
            "temperature": args.temperature,
            "seed": args.seed,
            "final_wav": manifest_path_value(final_wav),
            "final_wav_sha256": sha256_file(final_wav),
            "final_audio": manifest_path_value(final_audio),
            "final_audio_sha256": sha256_file(final_audio),
            "duration_seconds": round(duration, 3),
            "segments": [
                {
                    "index": record["index"],
                    "text_sha256": record["text_sha256"],
                    "path": manifest_path_value(record["path"]),
                    "duration_seconds": record["duration_seconds"],
                }
                for record in segment_records
            ],
        }
        write_json(manifest_path, manifest)
    except RuntimeError as error:
        print(f"Cannot render Chatterbox audio: {error}", file=sys.stderr)
        raise SystemExit(1) from error

    print(f"Rendered {len(segment_paths)} segment(s): {final_audio}")
    print(f"Created {manifest_path}")


if __name__ == "__main__":
    main()
