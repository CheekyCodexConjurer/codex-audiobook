from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile


SCRIPTS_ROOT = Path(__file__).resolve().parent
PLUGIN_ROOT = SCRIPTS_ROOT.parent
REPO_ROOT = PLUGIN_ROOT.parents[1]
RENDERER = SCRIPTS_ROOT / "render_chatterbox.py"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run_validator(
    validator: Path,
    promotion: Path,
    report: Path,
    evidence_mode: str,
) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
    return subprocess.run(
        [
            sys.executable,
            str(validator),
            "--renderer",
            str(RENDERER),
            "--promotion",
            str(promotion),
            "--report",
            str(report),
            "--evidence-mode",
            evidence_mode,
        ],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        env=env,
    )


def assert_profile_modes(
    profile_name: str,
    validator_name: str,
    report_name: str,
    temp_root: Path,
) -> None:
    source_promotion = (
        PLUGIN_ROOT / "assets" / "voices" / f"{profile_name}.promotion.json"
    )
    promotion = json.loads(source_promotion.read_text(encoding="utf-8"))
    promotion["calibration"]["corpus"]["path"] = str(
        temp_root / profile_name / "missing-corpus.json"
    )
    if profile_name == "masculina-v1":
        promotion["runtime"]["renderer_sha256"] = sha256_file(RENDERER)

    promotion_path = temp_root / f"{profile_name}.promotion.json"
    promotion_path.write_text(
        json.dumps(promotion, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report_path = REPO_ROOT / "docs" / "voice-calibration" / report_name
    validator_path = SCRIPTS_ROOT / validator_name

    provenance = run_validator(
        validator_path,
        promotion_path,
        report_path,
        "provenance",
    )
    assert provenance.returncode == 0, provenance.stderr or provenance.stdout
    assert "(provenance evidence)" in provenance.stdout

    full = run_validator(
        validator_path,
        promotion_path,
        report_path,
        "full",
    )
    assert full.returncode != 0
    assert "calibration corpus is missing" in full.stderr

    promotion["reproduction"]["input"]["sha256"] = "not-a-sha256"
    invalid_path = temp_root / f"{profile_name}.invalid.promotion.json"
    invalid_path.write_text(
        json.dumps(promotion, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    invalid = run_validator(
        validator_path,
        invalid_path,
        report_path,
        "provenance",
    )
    assert invalid.returncode != 0
    assert "must be a SHA-256 hex string" in invalid.stderr


def main() -> None:
    with tempfile.TemporaryDirectory() as temp:
        temp_root = Path(temp).resolve()
        assert_profile_modes(
            "feminina-v1",
            "validate_feminina_profile.py",
            "feminina-v1.md",
            temp_root,
        )
        assert_profile_modes(
            "masculina-v1",
            "validate_masculina_profile.py",
            "masculina-v1.md",
            temp_root,
        )
    print("VALID voice profile evidence-mode tests")


if __name__ == "__main__":
    main()
