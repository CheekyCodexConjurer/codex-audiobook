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
    require_equal(sha256_file(path), expected_hash, f"{label} SHA-256")
    return path


def require_object(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise RuntimeError(f"{label} must be an object.")
    return value


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
        description="Validate the structured masculina-v1 promotion against current evidence."
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
        require_equal(promotion.get("profile_name"), "masculina-v1", "profile name")
        require_equal(promotion.get("status"), "approved", "promotion status")
        require_equal(
            promotion.get("engine"),
            "chatterbox-multilingual-v3-pt-br",
            "promotion engine",
        )

        renderer = load_renderer(renderer_path)
        parameters = require_object(promotion.get("parameters"), "parameters")
        runtime = require_object(promotion.get("runtime"), "runtime")
        calibration = require_object(promotion.get("calibration"), "calibration")
        reference = require_object(promotion.get("voice_reference"), "voice reference")
        text_policy = require_object(promotion.get("text_policy"), "text policy")
        reproduction = require_object(promotion.get("reproduction"), "reproduction")

        require_equal(parameters, renderer.MASCULINA_PROFILE, "renderer profile parameters")
        require_equal(
            promotion.get("conditioning_strategy"),
            renderer.VOICE_PROFILES["masculina-v1"]["conditioning_strategy"],
            "conditioning strategy",
        )
        require_equal(
            promotion.get("seed_strategy"),
            renderer.VOICE_PROFILES["masculina-v1"]["seed_strategy"],
            "seed strategy",
        )
        require_equal(
            text_policy,
            {"name": "line-delimited-v1", "max_chars": renderer.MASCULINA_PROFILE["max_chars"]},
            "text policy",
        )
        renderer_calibration = renderer.MASCULINA_PROFILE_CALIBRATION
        require_equal(
            calibration.get("selection_id"),
            renderer_calibration["selection_id"],
            "selection id",
        )
        require_equal(runtime.get("device"), renderer_calibration["device"], "runtime device")
        require_equal(
            runtime.get("model_root"),
            renderer_calibration["model_root"],
            "runtime model root",
        )
        require_equal(
            runtime.get("chatterbox_tts_version"),
            renderer_calibration["chatterbox_tts_version"],
            "runtime chatterbox version",
        )
        require_equal(runtime.get("model"), renderer_calibration["model"], "runtime model hashes")
        require_equal(
            runtime.get("renderer_sha256"),
            sha256_file(renderer_path),
            "runtime renderer hash",
        )

        plugin_root = renderer_path.parent.parent
        reference_path_value = reference.get("path")
        if not isinstance(reference_path_value, str):
            raise RuntimeError("voice reference path is required.")
        reference_path = (plugin_root / reference_path_value).resolve()
        require_equal(
            reference_path,
            renderer.MASCULINA_REFERENCE_VOICE.resolve(),
            "reference path",
        )
        require_equal(
            reference.get("sha256"),
            renderer.MASCULINA_REFERENCE_VOICE_SHA256,
            "reference hash declaration",
        )
        require_equal(
            sha256_file(reference_path),
            renderer.MASCULINA_REFERENCE_VOICE_SHA256,
            "reference file hash",
        )

        corpus = require_object(calibration.get("corpus"), "calibration corpus")
        selection = require_object(calibration.get("selection"), "calibration selection")
        corpus_path = require_hashed_file(
            corpus.get("path"), corpus.get("sha256"), "calibration corpus"
        )
        selection_path = require_hashed_file(
            selection.get("path"), selection.get("sha256"), "calibration selection"
        )
        require_equal(
            corpus.get("sha256"),
            renderer_calibration["corpus_sha256"],
            "renderer corpus hash",
        )
        require_equal(
            selection.get("sha256"),
            renderer_calibration["selection_sha256"],
            "renderer selection hash",
        )

        corpus_json = load_json(corpus_path)
        selection_json = load_json(selection_path)
        selection_corpus = require_object(selection_json.get("corpus"), "selection corpus")
        winner = require_object(selection_json.get("winner"), "selection winner")
        require_equal(selection_corpus.get("sha256"), corpus.get("sha256"), "selection corpus hash")
        selection_reference = require_object(
            selection_corpus.get("voice_reference"), "selection voice reference"
        )
        require_equal(
            selection_reference.get("sha256"),
            renderer.MASCULINA_REFERENCE_VOICE_SHA256,
            "selection voice reference hash",
        )
        require_hashed_file(
            selection_reference.get("path"),
            selection_reference.get("sha256"),
            "selection voice reference",
        )
        corpus_reference = require_object(
            corpus_json.get("voice_reference"), "corpus voice reference"
        )
        require_equal(
            corpus_reference.get("sha256"),
            renderer.MASCULINA_REFERENCE_VOICE_SHA256,
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
            renderer_calibration["winner_id"],
            "renderer winner id",
        )
        winner_parameters = require_object(winner.get("parameters"), "winner parameters")
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

        decision = require_object(selection_json.get("decision"), "selection decision")
        review_record = require_object(decision.get("review"), "selection review record")
        review_path = require_hashed_file(
            review_record.get("path"),
            review_record.get("sha256"),
            "listening review",
        )
        listening_review = require_object(
            promotion.get("listening_review"), "promotion listening review"
        )
        require_equal(listening_review.get("status"), "approved", "listening review status")
        require_equal(listening_review.get("path"), str(review_path), "listening review path")
        require_equal(
            listening_review.get("sha256"),
            sha256_file(review_path),
            "listening review hash",
        )
        review_json = load_json(review_path)
        require_equal(review_json.get("decision"), "approved", "human decision")
        review_result = require_object(review_json.get("result"), "human review result")
        for prompt in ("01-narracao", "02-dialogo", "03-semiotica", "overall"):
            require_equal(review_result.get(prompt), "A", f"human review {prompt}")
        resolved = require_object(
            review_json.get("resolved_candidate"), "resolved human candidate"
        )
        require_equal(resolved.get("candidate_id"), winner.get("id"), "resolved candidate id")

        raw_wav = require_object(reproduction.get("raw_wav"), "raw WAV")
        publication_wav = require_object(
            reproduction.get("publication_wav"), "publication WAV"
        )
        delivery_mp3 = require_object(reproduction.get("delivery_mp3"), "delivery MP3")
        input_record = require_object(reproduction.get("input"), "smoke input")
        manifest_record = require_object(reproduction.get("manifest"), "smoke manifest")
        require_hashed_file(input_record.get("path"), input_record.get("sha256"), "smoke input")
        require_hashed_file(raw_wav.get("path"), raw_wav.get("sha256"), "approved raw WAV")
        require_hashed_file(
            publication_wav.get("path"),
            publication_wav.get("sha256"),
            "publication WAV",
        )
        require_hashed_file(
            delivery_mp3.get("path"), delivery_mp3.get("sha256"), "approved delivery MP3"
        )
        manifest_path = require_hashed_file(
            manifest_record.get("path"),
            manifest_record.get("sha256"),
            "production smoke manifest",
        )
        require_equal(
            raw_wav.get("sha256"),
            renderer_calibration["main_prompt_wav_sha256"],
            "renderer main prompt WAV hash",
        )
        smoke_manifest = load_json(manifest_path)
        require_equal(smoke_manifest.get("profile"), "masculina-v1", "smoke profile")
        require_equal(
            smoke_manifest.get("conditioning_strategy"),
            promotion.get("conditioning_strategy"),
            "smoke conditioning strategy",
        )
        require_equal(
            smoke_manifest.get("seed_strategy"),
            promotion.get("seed_strategy"),
            "smoke seed strategy",
        )
        require_equal(
            smoke_manifest.get("master_wav_sha256"),
            raw_wav.get("sha256"),
            "smoke master WAV hash",
        )
        smoke_runtime = require_object(smoke_manifest.get("runtime"), "smoke runtime")
        require_equal(
            smoke_runtime.get("renderer_sha256"),
            sha256_file(renderer_path),
            "smoke renderer hash",
        )

        dsp = require_object(promotion.get("dsp"), "DSP decision")
        require_equal(dsp.get("status"), "raw-wav-retained", "DSP status")

        report_text = report_path.read_text(encoding="utf-8")
        report_fragments = (
            "masculina-v1.promotion.json",
            str(reference.get("sha256")),
            str(corpus.get("sha256")),
            str(selection.get("sha256")),
            f"seed_strategy: {promotion.get('seed_strategy')}",
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
        print("VALID masculina-v1 promotion")
    except RuntimeError as error:
        print(f"INVALID masculina-v1 promotion: {error}", file=sys.stderr)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
