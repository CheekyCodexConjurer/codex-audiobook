from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import sys


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def load_json(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise RuntimeError(f"JSON root must be an object: {path}")
    return value


def require_equal(actual: object, expected: object, label: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{label} mismatch: expected {expected!r}, got {actual!r}")


def require_hashed_file(path_value: object, expected_hash: object, label: str) -> Path:
    if not isinstance(path_value, str) or not isinstance(expected_hash, str):
        raise RuntimeError(f"{label} path and SHA-256 are required.")
    path = Path(path_value)
    if not path.is_file():
        raise RuntimeError(f"{label} is missing: {path}")
    actual_hash = sha256_file(path)
    require_equal(actual_hash, expected_hash, f"{label} SHA-256")
    return path


def load_renderer(path: Path) -> object:
    sys.path.insert(0, str(path.parent))
    spec = importlib.util.spec_from_file_location("validated_render_chatterbox", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Cannot load renderer: {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Validate the structured feminina-v1 promotion against current evidence."
    )
    parser.add_argument("--renderer", type=Path, required=True)
    parser.add_argument("--promotion", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()

    try:
        renderer_path = args.renderer.expanduser().resolve()
        promotion_path = args.promotion.expanduser().resolve()
        report_path = args.report.expanduser().resolve()
        if not renderer_path.is_file() or not report_path.is_file():
            raise RuntimeError("Renderer and report must exist.")
        promotion = load_json(promotion_path)
        require_equal(promotion.get("schema_version"), "1.0", "promotion schema")
        require_equal(promotion.get("profile_name"), "feminina-v1", "profile name")
        require_equal(promotion.get("status"), "approved", "promotion status")

        renderer = load_renderer(renderer_path)
        require_equal(
            promotion.get("engine"),
            "chatterbox-multilingual-v3-pt-br",
            "promotion engine",
        )
        parameters = promotion.get("parameters")
        runtime = promotion.get("runtime")
        calibration = promotion.get("calibration")
        reference = promotion.get("voice_reference")
        text_policy = promotion.get("text_policy")
        reproduction = promotion.get("reproduction")
        if not all(
            isinstance(value, dict)
            for value in (parameters, runtime, calibration, reference, text_policy, reproduction)
        ):
            raise RuntimeError("Promotion is missing required structured objects.")

        require_equal(parameters, renderer.FEMININA_PROFILE, "renderer profile parameters")
        require_equal(
            text_policy,
            {"name": "line-delimited-v1", "max_chars": renderer.FEMININA_PROFILE["max_chars"]},
            "text policy",
        )
        require_equal(
            calibration.get("selection_id"),
            renderer.FEMININA_PROFILE_CALIBRATION["selection_id"],
            "selection id",
        )
        require_equal(
            runtime.get("device"),
            renderer.FEMININA_PROFILE_CALIBRATION["device"],
            "runtime device",
        )
        require_equal(
            runtime.get("model_root"),
            renderer.FEMININA_PROFILE_CALIBRATION["model_root"],
            "runtime model root",
        )
        require_equal(
            runtime.get("chatterbox_tts_version"),
            renderer.FEMININA_PROFILE_CALIBRATION["chatterbox_tts_version"],
            "runtime chatterbox version",
        )
        require_equal(
            runtime.get("model"),
            renderer.FEMININA_PROFILE_CALIBRATION["model"],
            "runtime model hashes",
        )

        plugin_root = renderer_path.parent.parent
        reference_path_value = reference.get("path")
        if not isinstance(reference_path_value, str):
            raise RuntimeError("voice reference path is required.")
        reference_path = (plugin_root / reference_path_value).resolve()
        require_equal(reference_path, renderer.DEFAULT_REFERENCE_VOICE.resolve(), "reference path")
        require_equal(
            reference.get("sha256"),
            renderer.DEFAULT_REFERENCE_VOICE_SHA256,
            "reference hash declaration",
        )
        require_equal(
            sha256_file(reference_path),
            renderer.DEFAULT_REFERENCE_VOICE_SHA256,
            "reference file hash",
        )

        corpus = calibration.get("corpus")
        selection = calibration.get("selection")
        if not isinstance(corpus, dict) or not isinstance(selection, dict):
            raise RuntimeError("calibration corpus and selection are required.")
        corpus_path = require_hashed_file(
            corpus.get("path"),
            corpus.get("sha256"),
            "calibration corpus",
        )
        selection_path = require_hashed_file(
            selection.get("path"),
            selection.get("sha256"),
            "calibration selection",
        )
        require_equal(
            corpus.get("sha256"),
            renderer.FEMININA_PROFILE_CALIBRATION["corpus_sha256"],
            "renderer corpus hash",
        )
        require_equal(
            selection.get("sha256"),
            renderer.FEMININA_PROFILE_CALIBRATION["selection_sha256"],
            "renderer selection hash",
        )

        corpus_json = load_json(corpus_path)
        selection_json = load_json(selection_path)
        selection_corpus = selection_json.get("corpus")
        winner = selection_json.get("winner")
        if not isinstance(selection_corpus, dict) or not isinstance(winner, dict):
            raise RuntimeError("Selection manifest is missing corpus or winner.")
        require_equal(selection_corpus.get("sha256"), corpus.get("sha256"), "selection corpus hash")
        require_equal(
            selection_corpus.get("voice_reference"),
            {
                "path": str(renderer.DEFAULT_REFERENCE_VOICE.resolve()),
                "sha256": renderer.DEFAULT_REFERENCE_VOICE_SHA256,
            },
            "selection voice reference",
        )
        require_equal(
            corpus_json.get("voice_reference", {}).get("sha256")
            if isinstance(corpus_json.get("voice_reference"), dict)
            else None,
            renderer.DEFAULT_REFERENCE_VOICE_SHA256,
            "corpus voice reference hash",
        )
        for key in (
            "winner_id",
            "robust_score",
            "mean_composite_score",
            "minimum_composite_score",
        ):
            expected = winner.get("id") if key == "winner_id" else winner.get(key)
            require_equal(selection.get(key), expected, f"selection {key}")
        require_equal(
            selection.get("winner_id"),
            renderer.FEMININA_PROFILE_CALIBRATION["winner_id"],
            "renderer winner id",
        )
        winner_parameters = winner.get("parameters")
        if not isinstance(winner_parameters, dict):
            raise RuntimeError("Selection winner parameters are invalid.")
        for key in (
            "exaggeration",
            "cfg_weight",
            "temperature",
            "repetition_penalty",
            "min_p",
            "top_p",
            "seed",
        ):
            require_equal(winner_parameters.get(key), parameters.get(key), f"winner {key}")

        raw_wav = reproduction.get("raw_wav")
        delivery_mp3 = reproduction.get("delivery_mp3")
        input_record = reproduction.get("input")
        if not all(isinstance(value, dict) for value in (raw_wav, delivery_mp3, input_record)):
            raise RuntimeError("Promotion reproduction records are incomplete.")
        require_hashed_file(input_record.get("path"), input_record.get("sha256"), "smoke input")
        require_hashed_file(raw_wav.get("path"), raw_wav.get("sha256"), "approved raw WAV")
        require_hashed_file(delivery_mp3.get("path"), delivery_mp3.get("sha256"), "approved delivery MP3")
        require_equal(
            raw_wav.get("sha256"),
            renderer.FEMININA_PROFILE_CALIBRATION["main_prompt_wav_sha256"],
            "renderer main prompt WAV hash",
        )
        listening_review = promotion.get("listening_review")
        dsp = promotion.get("dsp")
        if not isinstance(listening_review, dict) or listening_review.get("status") != "approved":
            raise RuntimeError("Promotion requires an approved listening review.")
        if not isinstance(dsp, dict) or dsp.get("status") != "raw-wav-retained":
            raise RuntimeError("Promotion must record the approved DSP decision.")
        report_text = report_path.read_text(encoding="utf-8")
        report_fragments = (
            "feminina-v1.promotion.json",
            str(reference.get("sha256")),
            str(corpus.get("sha256")),
            str(selection.get("sha256")),
            *(f"{key}: {value}" for key, value in parameters.items()),
            f"robustez: {selection.get('robust_score')}",
            f"média: {selection.get('mean_composite_score')}",
            f"mínimo: {selection.get('minimum_composite_score')}",
            str(raw_wav.get("sha256")),
            str(delivery_mp3.get("sha256")),
        )
        for fragment in report_fragments:
            if fragment not in report_text:
                raise RuntimeError(
                    f"Calibration report does not match promotion evidence: {fragment}"
                )
        print("VALID feminina-v1 promotion")
    except RuntimeError as error:
        print(f"INVALID feminina-v1 promotion: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
