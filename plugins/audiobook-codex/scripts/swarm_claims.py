from __future__ import annotations

"""Shared claim and shard helpers for Audiobook Codex swarm workflows.

Claim status is intentionally monotonic. A claim can only advance through the
main ordered states below:

    planned -> leased -> in_progress -> ready_for_verification -> verified -> merged

``blocked`` and ``abandoned`` are terminal side exits and must not be used as a
way to move work backwards. This module does not mutate claims; validators use
this state model to reject unknown states and to make the monotonic contract
explicit for every tool that imports the constants.
"""

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

CLAIM_SCHEMA_VERSION = "1.0"
SHARD_SCHEMA_VERSION = "1.0"

CLAIM_STATES = (
    "planned",
    "leased",
    "in_progress",
    "ready_for_verification",
    "verified",
    "merged",
    "blocked",
    "abandoned",
)
MONOTONIC_CLAIM_STATES = (
    "planned",
    "leased",
    "in_progress",
    "ready_for_verification",
    "verified",
    "merged",
)
TERMINAL_CLAIM_STATES = ("blocked", "abandoned")
CLAIM_STATE_RANK = {state: index for index, state in enumerate(MONOTONIC_CLAIM_STATES)}
HASH_RE = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_READS_BY_STAGE = {
    "TRANSCRIBE": ("metadata/book-map.json",),
    "TRANSLATE": (
        "metadata/book-map.json",
        "metadata/text-ledger.json",
        "metadata/translation-ledger.json",
    ),
    "FLUID": (
        "metadata/book-map.json",
        "metadata/text-ledger.json",
        "metadata/fluid-style.json",
    ),
}
WRITABLE_STAGES = frozenset((*REQUIRED_READS_BY_STAGE, "RENDER"))
READ_ONLY_STAGES = frozenset({"MAP"})
SUPPORTED_STAGES = frozenset((*WRITABLE_STAGES, *READ_ONLY_STAGES))
SHARD_STAGE_BY_KIND = {
    "text": "TRANSCRIBE",
    "translation": "TRANSLATE",
    "fluid": "FLUID",
}


class SwarmValidationError(RuntimeError):
    """Raised when a claim, shard, path, or hash contract is invalid."""


def load_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise SwarmValidationError(f"Cannot read JSON {path}: {error}") from error


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_json(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


CLAIM_LIFECYCLE_FIELDS = frozenset({"status", "lease"})


def claim_contract(claim: dict[str, Any]) -> dict[str, Any]:
    """Return the immutable claim contract used for shard binding.

    ``status`` and ``lease`` are lifecycle fields: they legitimately mutate as
    work advances. All other current fields remain part of the binding so drift
    in producers, verifiers, read hashes, scope, write/no-touch targets, or
    validation commands invalidates existing shards.
    """

    return {
        key: value
        for key, value in claim.items()
        if key not in CLAIM_LIFECYCLE_FIELDS
    }


def claim_digest(claim: dict[str, Any]) -> str:
    return sha256_json(claim_contract(claim))


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    temp_path = Path(temp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, path)
    except Exception:
        try:
            temp_path.unlink()
        except OSError:
            pass
        raise


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def normalized_relative_path(raw_path: Any, *, label: str = "path") -> str:
    if not isinstance(raw_path, str):
        raise SwarmValidationError(f"{label} must be a relative path string")
    value = raw_path.strip().replace("\\", "/")
    if not value:
        raise SwarmValidationError(f"{label} must be non-empty")
    if value.startswith("/") or Path(value).drive:
        raise SwarmValidationError(f"{label} must be relative: {raw_path!r}")
    parts = value.split("/")
    if any(part in {"", ".", ".."} for part in parts):
        raise SwarmValidationError(f"{label} must not contain empty, '.', or '..' segments: {raw_path!r}")
    return "/".join(parts)


def resolve_relative(root: Path, raw_path: Any, *, label: str = "path") -> Path:
    relative = normalized_relative_path(raw_path, label=label)
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as error:
        raise SwarmValidationError(f"{label} escapes root: {raw_path!r}") from error
    return candidate


def require_hash(value: Any, *, label: str, allow_empty: bool = False) -> None:
    if not isinstance(value, str):
        raise SwarmValidationError(f"{label} must be a SHA-256 string")
    if allow_empty and value == "":
        return
    if HASH_RE.fullmatch(value) is None:
        raise SwarmValidationError(f"{label} must be a lowercase 64-character SHA-256")


def _path_conflict(left: str, right: str) -> bool:
    return left == right or left.startswith(right + "/") or right.startswith(left + "/")


def _validate_string_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise SwarmValidationError(f"{label} must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        path = normalized_relative_path(item, label=f"{label}[{index}]")
        if path in seen:
            raise SwarmValidationError(f"{label}[{index}] duplicates path {path}")
        seen.add(path)
        result.append(path)
    return result


def _validate_id_list(value: Any, label: str) -> list[str]:
    if not isinstance(value, list):
        raise SwarmValidationError(f"{label} must be an array")
    result: list[str] = []
    seen: set[str] = set()
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise SwarmValidationError(f"{label}[{index}] must be a non-empty string")
        key = item.strip()
        if key in seen:
            raise SwarmValidationError(f"{label}[{index}] duplicates {key}")
        seen.add(key)
        result.append(key)
    return result


def _read_set_index(read_set: Any, label: str, errors: list[str]) -> dict[str, str]:
    reads: dict[str, str] = {}
    if not isinstance(read_set, list):
        errors.append(f"{label}.read_set must be an array")
        return reads
    for read_index, read_entry in enumerate(read_set):
        read_label = f"{label}.read_set[{read_index}]"
        if not isinstance(read_entry, dict):
            errors.append(f"{read_label} must be an object")
            continue
        try:
            read_path = normalized_relative_path(read_entry.get("path"), label=f"{read_label}.path")
            if read_path in reads:
                errors.append(f"{read_label}.path duplicates {read_path}")
            require_hash(read_entry.get("sha256"), label=f"{read_label}.sha256")
            if isinstance(read_entry.get("sha256"), str):
                reads[read_path] = read_entry["sha256"]
        except SwarmValidationError as error:
            errors.append(str(error))
    return reads


def _uses_translated_fluid_base(book_root: Path | None) -> bool:
    if book_root is None:
        return False
    style_path = book_root / "metadata" / "fluid-style.json"
    try:
        style = json.loads(style_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return isinstance(style, dict) and style.get("base_edition") == "translated-pt-br"


def _required_read_paths(claim: dict[str, Any], book_root: Path | None) -> tuple[str, ...]:
    stage = claim.get("stage")
    if not isinstance(stage, str):
        return ()
    required = list(REQUIRED_READS_BY_STAGE.get(stage, ()))
    if stage == "FLUID" and _uses_translated_fluid_base(book_root):
        required.append("metadata/translation-ledger.json")
    return tuple(required)


def _validate_read_set_dependencies(
    claim: dict[str, Any],
    label: str,
    book_root: Path | None,
    errors: list[str],
) -> None:
    reads = _read_set_index(claim.get("read_set"), label, errors)
    for required_path in _required_read_paths(claim, book_root):
        if required_path not in reads:
            errors.append(f"{label}.read_set must freeze immutable dependency {required_path}")
    if book_root is None:
        return
    for read_path, expected_sha256 in reads.items():
        try:
            disk_path = resolve_relative(book_root, read_path, label=f"{label}.read_set.path")
        except SwarmValidationError as error:
            errors.append(str(error))
            continue
        if not disk_path.is_file():
            errors.append(f"{label}.read_set path is missing under book root: {read_path}")
        elif sha256_file(disk_path) != expected_sha256:
            errors.append(f"{label}.read_set sha256 does not match current file: {read_path}")


def _claim_write_targets(
    claim: dict[str, Any],
    label: str,
    errors: list[str],
) -> tuple[list[str], dict[str, list[str]]]:
    groups: dict[str, list[str]] = {}
    for field in ("write_set", "canonical_targets"):
        try:
            groups[field] = _validate_string_list(claim.get(field), f"{label}.{field}")
        except SwarmValidationError as error:
            errors.append(str(error))
            groups[field] = []
    return groups["write_set"] + groups["canonical_targets"], groups


def validate_claim_map(claim_map: Any, book_root: Path | None = None) -> list[str]:
    errors: list[str] = []
    if not isinstance(claim_map, dict):
        return ["claim map must be a JSON object"]
    if claim_map.get("schema_version") != CLAIM_SCHEMA_VERSION:
        errors.append(f"claim map schema_version must be {CLAIM_SCHEMA_VERSION}")
    claims = claim_map.get("claims")
    if not isinstance(claims, list):
        return errors + ["claim map must include a claims array"]

    by_id: dict[str, dict[str, Any]] = {}
    all_targets: list[tuple[str, str, str]] = []
    claim_orders: dict[int, str] = {}
    for index, claim in enumerate(claims):
        label = f"claims[{index}]"
        if not isinstance(claim, dict):
            errors.append(f"{label} must be an object")
            continue

        claim_id = claim.get("claim_id")
        if not isinstance(claim_id, str) or not claim_id.strip():
            errors.append(f"{label}.claim_id must be non-empty")
            claim_id = f"<invalid-{index}>"
        elif claim_id in by_id:
            errors.append(f"{label}.claim_id is duplicated: {claim_id}")
        else:
            by_id[claim_id] = claim

        stage = claim.get("stage")
        if not isinstance(stage, str) or not stage.strip():
            errors.append(f"{label}.stage must be non-empty")
        elif stage not in SUPPORTED_STAGES:
            errors.append(f"{label}.stage must be one of {sorted(SUPPORTED_STAGES)}")
        if not isinstance(claim.get("producer"), str) or not claim["producer"].strip():
            errors.append(f"{label}.producer must be non-empty")
        if claim.get("status") not in CLAIM_STATES:
            errors.append(f"{label}.status must be one of {list(CLAIM_STATES)}")
        priority = claim.get("priority")
        if not isinstance(priority, int) or isinstance(priority, bool) or priority < 0:
            errors.append(f"{label}.priority must be a non-negative integer")
        claim_order = claim.get("claim_order")
        if not isinstance(claim_order, int) or isinstance(claim_order, bool) or claim_order <= 0:
            errors.append(f"{label}.claim_order must be a positive integer")
        elif claim_order in claim_orders:
            errors.append(f"{label}.claim_order duplicates {claim_orders[claim_order]}: {claim_order}")
        else:
            claim_orders[claim_order] = label

        verifier = claim.get("verifier")
        validation = claim.get("validation")
        requires_verification = isinstance(validation, dict) and validation.get("requires_verification") is True
        if verifier is not None and (not isinstance(verifier, str) or not verifier.strip()):
            errors.append(f"{label}.verifier must be non-empty when present")
        if requires_verification and not (isinstance(verifier, str) and verifier.strip()):
            errors.append(f"{label}.verifier is required when validation.requires_verification is true")
        if isinstance(verifier, str) and verifier.strip() and verifier == claim.get("producer"):
            errors.append(f"{label}.producer and verifier must be distinct")

        try:
            _validate_id_list(claim.get("depends_on"), f"{label}.depends_on")
        except SwarmValidationError as error:
            errors.append(str(error))

        _validate_read_set_dependencies(claim, label, book_root, errors)

        claim_targets, target_groups = _claim_write_targets(claim, label, errors)
        if stage in WRITABLE_STAGES:
            if not target_groups["write_set"]:
                errors.append(f"{label}.write_set must be non-empty for writable stage {stage}")
            if not target_groups["canonical_targets"]:
                errors.append(f"{label}.canonical_targets must be non-empty for writable stage {stage}")
        elif stage in READ_ONLY_STAGES and claim_targets:
            errors.append(f"{label} is read-only for stage {stage} and must not declare write targets")
        try:
            no_touch = _validate_string_list(claim.get("no_touch"), f"{label}.no_touch")
        except SwarmValidationError as error:
            errors.append(str(error))
            no_touch = []
        for target in claim_targets:
            for blocked in no_touch:
                if _path_conflict(target, blocked):
                    errors.append(f"{label} writes {target} but no_touch includes {blocked}")
            all_targets.append((target, str(claim_id), label))

        scope = claim.get("scope")
        if not isinstance(scope, dict):
            errors.append(f"{label}.scope must be an object")
        else:
            if not isinstance(scope.get("unit_kind"), str) or not scope["unit_kind"].strip():
                errors.append(f"{label}.scope.unit_kind must be non-empty")
            for field in ("unit_ids", "context_unit_ids"):
                try:
                    _validate_id_list(scope.get(field), f"{label}.scope.{field}")
                except SwarmValidationError as error:
                    errors.append(str(error))
        if not isinstance(claim.get("context"), dict):
            errors.append(f"{label}.context must be an object")
        if not isinstance(claim.get("validation"), dict):
            errors.append(f"{label}.validation must be an object")
        if not isinstance(claim.get("lease"), dict):
            errors.append(f"{label}.lease must be an object")

    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = claim.get("claim_id")
        depends_on = claim.get("depends_on")
        if not isinstance(claim_id, str) or not isinstance(depends_on, list):
            continue
        for dependency in depends_on:
            if dependency == claim_id:
                errors.append(f"claim {claim_id} depends on itself")
            elif isinstance(dependency, str) and dependency not in by_id:
                errors.append(f"claim {claim_id} depends on unknown claim {dependency}")

    if claim_orders:
        expected_orders = set(range(1, len([claim for claim in claims if isinstance(claim, dict)]) + 1))
        actual_orders = set(claim_orders)
        if actual_orders != expected_orders:
            errors.append(f"claim_order must be contiguous 1..{len(expected_orders)}; got {sorted(actual_orders)}")

    for left_index, (left_path, left_id, left_label) in enumerate(all_targets):
        for right_path, right_id, right_label in all_targets[left_index + 1 :]:
            if left_id != right_id and _path_conflict(left_path, right_path):
                errors.append(
                    f"write/canonical target overlap between {left_label} ({left_path}) "
                    f"and {right_label} ({right_path})"
                )
    return errors


def claims_by_id(claim_map: dict[str, Any]) -> dict[str, dict[str, Any]]:
    claims = claim_map.get("claims")
    if not isinstance(claims, list):
        raise SwarmValidationError("claim map must include a claims array")
    result: dict[str, dict[str, Any]] = {}
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        claim_id = claim.get("claim_id")
        if isinstance(claim_id, str) and claim_id.strip():
            result[claim_id] = claim
    return result


def ensure_no_errors(errors: list[str], header: str = "Invalid swarm data") -> None:
    if errors:
        raise SwarmValidationError(header + ":\n- " + "\n- ".join(errors))
