from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import tempfile


ROOT = Path(__file__).resolve().parent


def run(script: str, *args: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / script), *args],
        text=True,
        capture_output=True,
    )
    if completed.returncode != 0:
        raise AssertionError(
            f"{script} failed\nstdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )


def run_fails(script: str, *args: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-B", str(ROOT / script), *args],
        text=True,
        capture_output=True,
    )
    if completed.returncode == 0:
        raise AssertionError(f"{script} unexpectedly succeeded")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="voice-calibration-") as temporary:
        root = Path(temporary)
        library = root / "library"
        run(
            "init_calibration_workspace.py",
            "--profile-id",
            "test-voice-v1",
            "--library-root",
            str(library),
        )
        workspace = library / "_voice-calibration-test-voice-v1"
        run(
            "validate_calibration_workspace.py",
            "--workspace-root",
            str(workspace),
            "--check-files",
        )
        run_fails(
            "validate_calibration_workspace.py",
            "--workspace-root",
            str(workspace),
            "--require-ready",
            "--check-files",
        )
        corpus_path = workspace / "validation-corpus" / "corpus.json"
        corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
        corpus["prompts"][0]["target_audio_path"] = "references/original/stale.mp3"
        corpus["prompts"][0]["target_audio_sha256"] = "0" * 64
        corpus_path.write_text(
            json.dumps(corpus, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        run_fails(
            "validate_calibration_workspace.py",
            "--workspace-root",
            str(workspace),
        )
        corpus["prompts"][0]["target_audio_path"] = None
        corpus["prompts"][0]["target_audio_sha256"] = None
        corpus_path.write_text(
            json.dumps(corpus, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )

        source = root / "source"
        source.mkdir()
        reference = source / "reference.mp3"
        reference.write_bytes(b"reference")
        targets = {}
        for prompt_id in ("01-narracao", "02-dialogo", "03-semiotica"):
            target = source / f"{prompt_id}.mp3"
            target.write_bytes(prompt_id.encode("ascii"))
            targets[prompt_id] = target
        run(
            "import_calibration_targets.py",
            "--workspace-root",
            str(workspace),
            "--voice-reference",
            str(reference),
            "--target",
            f"01-narracao={targets['01-narracao']}",
            "--target",
            f"02-dialogo={targets['02-dialogo']}",
            "--target",
            f"03-semiotica={targets['03-semiotica']}",
        )
        run(
            "validate_calibration_workspace.py",
            "--workspace-root",
            str(workspace),
            "--require-ready",
            "--check-files",
        )
        run_fails(
            "import_calibration_targets.py",
            "--workspace-root",
            str(workspace),
            "--voice-reference",
            str(reference),
            "--target",
            f"01-narracao={targets['01-narracao']}",
            "--target",
            f"02-dialogo={targets['02-dialogo']}",
            "--target",
            f"03-semiotica={targets['03-semiotica']}",
        )
        imported_target = workspace / "references" / "original" / "02-dialogo.mp3"
        imported_target.write_bytes(b"tampered")
        run_fails(
            "validate_calibration_workspace.py",
            "--workspace-root",
            str(workspace),
            "--require-ready",
            "--check-files",
        )
    print("Voice-calibration skill tests passed.")


if __name__ == "__main__":
    main()
