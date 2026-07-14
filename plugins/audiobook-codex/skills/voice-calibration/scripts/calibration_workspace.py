from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
from typing import Any


SCHEMA_VERSION = "1.0"
PROFILE_ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,62}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
PROMPTS = (
    (
        "01-narracao",
        'Na manhã de junho, a chuva fina cobria o jardim, enquanto a brisa movia lentamente as folhas. O relógio marcou oito e trinta. João abriu a janela e perguntou: "Quem deixou a pequena caixa azul junto à porta?" Após um breve silêncio, respirou devagar e disse: "Muito bem. Hoje começa uma nova história."\n',
    ),
    (
        "02-dialogo",
        'Quando Clara entrou na sala, encontrou as janelas abertas e os papéis espalhados sobre a mesa. Ela respirou fundo e perguntou, "Alguém esteve aqui?" Ninguém respondeu. Então fechou a porta, guardou a carta no bolso e disse, "Vamos descobrir isso antes do amanhecer."\n',
    ),
    (
        "03-semiotica",
        "Na sexta-feira, três de abril de dois mil e vinte e seis, às quatorze horas e trinta minutos, o museu recebeu vinte e cinco visitantes. O ingresso custava quarenta e dois reais e cinquenta centavos. Ana anotou tudo no caderno e avisou que a próxima visita seria às nove horas.\n",
    ),
)
PROMPT_IDS = tuple(item[0] for item in PROMPTS)


class WorkspaceError(RuntimeError):
    pass


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise WorkspaceError(f"Cannot read JSON {path}: {error}") from error
    if not isinstance(value, dict):
        raise WorkspaceError(f"JSON root must be an object: {path}")
    return value


def validate_profile_id(value: str) -> str:
    if not PROFILE_ID_PATTERN.fullmatch(value):
        raise WorkspaceError(
            "profile id must use lowercase letters, digits, and hyphens, "
            "start with a letter or digit, and have at most 63 characters."
        )
    return value


def resolve_within(root: Path, value: str, label: str) -> Path:
    candidate = Path(value)
    if candidate.is_absolute():
        raise WorkspaceError(f"{label} must be workspace-relative: {value}")
    resolved_root = root.resolve()
    resolved = (resolved_root / candidate).resolve()
    try:
        resolved.relative_to(resolved_root)
    except ValueError as error:
        raise WorkspaceError(f"{label} escapes the workspace: {value}") from error
    return resolved


def relative_path(root: Path, path: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def draft_corpus(profile_id: str, root: Path) -> dict[str, Any]:
    prompts: list[dict[str, Any]] = []
    for prompt_id, _ in PROMPTS:
        text_path = root / "validation-corpus" / f"{prompt_id}.txt"
        prompts.append(
            {
                "id": prompt_id,
                "text_path": relative_path(root, text_path),
                "text_sha256": sha256_file(text_path),
                "target_audio_path": None,
                "target_audio_sha256": None,
            }
        )
    return {
        "schema_version": SCHEMA_VERSION,
        "profile_id": profile_id,
        "purpose": "select one local TTS profile by cross-prompt consistency",
        "status": "draft",
        "voice_reference": {"path": None, "sha256": None},
        "prompts": prompts,
    }


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or not SHA256_PATTERN.fullmatch(value):
        raise WorkspaceError(f"{label} must be a lowercase SHA-256 string.")
    return value


def _validate_import(
    root: Path,
    path_value: object,
    hash_value: object,
    label: str,
    require_ready: bool,
    check_files: bool,
) -> None:
    if path_value is None and hash_value is None:
        if require_ready:
            raise WorkspaceError(f"{label} is required for a ready corpus.")
        return
    if not isinstance(path_value, str) or not path_value:
        raise WorkspaceError(f"{label} path must be a non-empty workspace-relative string.")
    expected_hash = _require_sha256(hash_value, f"{label} SHA-256")
    path = resolve_within(root, path_value, label)
    if check_files:
        if not path.is_file() or path.stat().st_size == 0:
            raise WorkspaceError(f"{label} is missing or empty: {path}")
        actual_hash = sha256_file(path)
        if actual_hash != expected_hash:
            raise WorkspaceError(
                f"{label} SHA-256 mismatch: expected {expected_hash}, got {actual_hash}."
            )


def validate_corpus(
    root: Path,
    *,
    require_ready: bool = False,
    check_files: bool = False,
) -> dict[str, Any]:
    corpus_path = root / "validation-corpus" / "corpus.json"
    corpus = load_json(corpus_path)
    if corpus.get("schema_version") != SCHEMA_VERSION:
        raise WorkspaceError(f"Unsupported corpus schema: {corpus.get('schema_version')!r}")
    profile_id = corpus.get("profile_id")
    if not isinstance(profile_id, str):
        raise WorkspaceError("profile_id must be a string.")
    validate_profile_id(profile_id)
    status = corpus.get("status")
    if status not in {"draft", "ready"}:
        raise WorkspaceError("status must be draft or ready.")
    if require_ready and status != "ready":
        raise WorkspaceError("A ready corpus is required.")

    reference = corpus.get("voice_reference")
    if not isinstance(reference, dict):
        raise WorkspaceError("voice_reference must be an object.")
    if status == "draft" and (
        reference.get("path") is not None or reference.get("sha256") is not None
    ):
        raise WorkspaceError("A draft corpus must not contain an imported voice reference.")
    _validate_import(
        root,
        reference.get("path"),
        reference.get("sha256"),
        "voice reference",
        status == "ready" or require_ready,
        check_files,
    )

    prompts = corpus.get("prompts")
    if not isinstance(prompts, list) or len(prompts) != len(PROMPTS):
        raise WorkspaceError("prompts must contain the three standard prompt records.")
    ids = tuple(item.get("id") if isinstance(item, dict) else None for item in prompts)
    if ids != PROMPT_IDS:
        raise WorkspaceError(f"prompt ids must be ordered exactly as {PROMPT_IDS}.")

    for prompt in prompts:
        if not isinstance(prompt, dict):
            raise WorkspaceError("Each prompt must be an object.")
        prompt_id = str(prompt["id"])
        text_path_value = prompt.get("text_path")
        if not isinstance(text_path_value, str) or not text_path_value:
            raise WorkspaceError(f"{prompt_id} text_path must be a non-empty string.")
        text_path = resolve_within(root, text_path_value, f"{prompt_id} text")
        if not text_path.is_file():
            raise WorkspaceError(f"{prompt_id} text is missing: {text_path}")
        expected_text_hash = _require_sha256(prompt.get("text_sha256"), f"{prompt_id} text")
        actual_text_hash = sha256_file(text_path)
        if actual_text_hash != expected_text_hash:
            raise WorkspaceError(
                f"{prompt_id} text SHA-256 mismatch: expected {expected_text_hash}, "
                f"got {actual_text_hash}."
            )
        if status == "draft" and (
            prompt.get("target_audio_path") is not None
            or prompt.get("target_audio_sha256") is not None
        ):
            raise WorkspaceError(
                f"A draft corpus must not contain imported target audio: {prompt_id}."
            )
        _validate_import(
            root,
            prompt.get("target_audio_path"),
            prompt.get("target_audio_sha256"),
            f"{prompt_id} target audio",
            status == "ready" or require_ready,
            check_files,
        )
    return corpus
