from __future__ import annotations

import argparse
from copy import deepcopy
from pathlib import Path
from typing import Any

from swarm_claims import (
    SHARD_SCHEMA_VERSION,
    SHARD_STAGE_BY_KIND,
    SwarmValidationError,
    atomic_write_json,
    claim_digest,
    claims_by_id,
    ensure_no_errors,
    load_json,
    require_hash,
    validate_claim_map,
)

SECTION_CONFIG = {
    "text": ("text", ("pages", "chapter_outputs")),
    "translation": (
        "translation",
        ("pages", "chapter_outputs", "glossary_proposals", "ambiguities"),
    ),
    "fluid": ("fluid", ("chapter_outputs", "blocks")),
}


def _entry_key(section: str, entry: Any) -> str | None:
    if not isinstance(entry, dict):
        return None
    if section == "pages":
        value = entry.get("logical_page")
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return str(value)
        return None
    if section in {"chapter_outputs", "blocks"}:
        value = entry.get("id")
        return value.strip() if isinstance(value, str) and value.strip() else None
    if section == "glossary_proposals":
        for field in ("id", "term", "source_term"):
            value = entry.get(field)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
    if section == "ambiguities":
        value = entry.get("id")
        if isinstance(value, str) and value.strip():
            return value.strip()
        logical_page = entry.get("logical_page")
        source_span = entry.get("source_span")
        if isinstance(logical_page, int) and isinstance(source_span, str) and source_span.strip():
            return f"{logical_page}:{source_span.strip()}"
        return None
    return None


def _validate_section_entries(section: str, entries: Any, label: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(entries, list):
        return [f"{label} must be an array"]
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        entry_label = f"{label}[{index}]"
        if not isinstance(entry, dict):
            errors.append(f"{entry_label} must be an object")
            continue
        key = _entry_key(section, entry)
        if key is None:
            errors.append(f"{entry_label} must include a stable key for section {section}")
            continue
        if key in seen:
            errors.append(f"{entry_label} duplicates key {key} in section {section}")
        seen.add(key)
    return errors


def validate_shard(
    shard: Any,
    kind: str,
    claim_index: dict[str, dict[str, Any]] | None = None,
    *,
    label: str = "shard",
) -> list[str]:
    errors: list[str] = []
    if kind not in SECTION_CONFIG:
        return [f"unsupported shard kind: {kind}"]
    if not isinstance(shard, dict):
        return [f"{label} must be an object"]
    section_name, sections = SECTION_CONFIG[kind]
    if shard.get("schema_version") != SHARD_SCHEMA_VERSION:
        errors.append(f"{label}.schema_version must be {SHARD_SCHEMA_VERSION}")
    if shard.get("shard_kind") != kind:
        errors.append(f"{label}.shard_kind must be {kind}")
    claim_id = shard.get("claim_id")
    if not isinstance(claim_id, str) or not claim_id.strip():
        errors.append(f"{label}.claim_id must be non-empty")
    try:
        require_hash(shard.get("claim_sha256"), label=f"{label}.claim_sha256")
    except SwarmValidationError as error:
        errors.append(str(error))
    if not isinstance(shard.get("producer"), str) or not shard["producer"].strip():
        errors.append(f"{label}.producer must be non-empty")
    verifier = shard.get("verifier")
    if verifier is not None and (not isinstance(verifier, str) or not verifier.strip()):
        errors.append(f"{label}.verifier must be non-empty when present")
    if isinstance(verifier, str) and verifier.strip() and verifier == shard.get("producer"):
        errors.append(f"{label}.producer and verifier must be distinct")
    order = shard.get("order")
    if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
        errors.append(f"{label}.order must be a positive integer")

    if isinstance(claim_id, str) and claim_index is not None:
        claim = claim_index.get(claim_id)
        if claim is None:
            errors.append(f"{label}.claim_id is not present in claim map: {claim_id}")
        else:
            expected_hash = claim_digest(claim)
            if shard.get("claim_sha256") != expected_hash:
                errors.append(f"{label}.claim_sha256 diverges from claim map for {claim_id}")
            if shard.get("producer") != claim.get("producer"):
                errors.append(f"{label}.producer does not match claim map for {claim_id}")
            claim_verifier = claim.get("verifier")
            if claim_verifier and shard.get("verifier") != claim_verifier:
                errors.append(f"{label}.verifier does not match claim map for {claim_id}")
            if shard.get("order") != claim.get("claim_order"):
                errors.append(f"{label}.order does not match claim map claim_order for {claim_id}")
            expected_stage = SHARD_STAGE_BY_KIND[kind]
            if claim.get("stage") != expected_stage:
                errors.append(
                    f"{label}.claim_id {claim_id} stage {claim.get('stage')!r} "
                    f"is incompatible with {kind} shard; expected {expected_stage}"
                )

    section_payload = shard.get(section_name)
    if not isinstance(section_payload, dict):
        errors.append(f"{label}.{section_name} must be an object")
    else:
        for section in sections:
            errors += _validate_section_entries(
                section,
                section_payload.get(section),
                f"{label}.{section_name}.{section}",
            )
    return errors


def _validate_orders_and_claims(shards: list[dict[str, Any]]) -> list[str]:
    errors: list[str] = []
    orders: dict[int, str] = {}
    claim_ids: dict[str, int] = {}
    for index, shard in enumerate(shards):
        order = shard.get("order")
        if isinstance(order, int) and not isinstance(order, bool):
            if order in orders:
                errors.append(f"shard[{index}].order duplicates shard {orders[order]}: {order}")
            orders[order] = str(index)
        claim_id = shard.get("claim_id")
        if isinstance(claim_id, str):
            if claim_id in claim_ids:
                errors.append(f"shard[{index}].claim_id duplicates shard {claim_ids[claim_id]}: {claim_id}")
            claim_ids[claim_id] = index
    return errors


def _claim_order_for_shard(shard: dict[str, Any], claim_index: dict[str, dict[str, Any]]) -> int:
    claim = claim_index[shard["claim_id"]]
    return claim["claim_order"]


def _validate_claims_verified_for_merge(
    shards: list[dict[str, Any]],
    claim_index: dict[str, dict[str, Any]] | None,
) -> list[str]:
    if claim_index is None:
        return []
    errors: list[str] = []
    for index, shard in enumerate(shards):
        claim_id = shard.get("claim_id")
        if not isinstance(claim_id, str):
            continue
        claim = claim_index.get(claim_id)
        if claim is None:
            continue
        if claim.get("status") != "verified":
            errors.append(
                f"shards[{index}].claim_id {claim_id} must be verified before merge; "
                f"got {claim.get('status')!r}"
            )
    return errors


def _base_section_keys(base: dict[str, Any], sections: tuple[str, ...]) -> tuple[dict[str, set[str]], list[str]]:
    keys: dict[str, set[str]] = {section: set() for section in sections}
    errors: list[str] = []
    for section in sections:
        entries = base.get(section, [])
        if not isinstance(entries, list):
            errors.append(f"base ledger section {section} must be an array when present")
            continue
        for index, entry in enumerate(entries):
            key = _entry_key(section, entry)
            if key is None:
                errors.append(f"base ledger {section}[{index}] must include a stable key")
                continue
            if key in keys[section]:
                errors.append(f"base ledger {section}[{index}] duplicates key {key}")
            keys[section].add(key)
    return keys, errors


def merge_ledgers(
    base_ledger: Any,
    shards: list[Any],
    kind: str,
    claim_map: Any | None = None,
    book_root: Path | None = None,
) -> dict[str, Any]:
    if kind not in SECTION_CONFIG:
        raise SwarmValidationError(f"unsupported shard kind: {kind}")
    if not isinstance(base_ledger, dict):
        raise SwarmValidationError("base ledger must be an object")
    typed_shards: list[dict[str, Any]] = []
    for shard in shards:
        if isinstance(shard, dict):
            typed_shards.append(shard)
        else:
            raise SwarmValidationError("each shard must be an object")
    if typed_shards and claim_map is None:
        raise SwarmValidationError("non-empty shard merge requires a claim map")
    if typed_shards and book_root is None:
        raise SwarmValidationError("non-empty shard merge requires a book root")

    claim_index = None
    if claim_map is not None:
        ensure_no_errors(validate_claim_map(claim_map, book_root), "Invalid claim map")
        claim_index = claims_by_id(claim_map)

    errors: list[str] = []
    for index, shard in enumerate(typed_shards):
        errors += validate_shard(shard, kind, claim_index, label=f"shards[{index}]")
    errors += _validate_orders_and_claims(typed_shards)
    errors += _validate_claims_verified_for_merge(typed_shards, claim_index)
    ensure_no_errors(errors, "Invalid ledger shards")

    section_name, sections = SECTION_CONFIG[kind]
    result = deepcopy(base_ledger)
    keys, base_errors = _base_section_keys(result, sections)
    ensure_no_errors(base_errors, "Invalid base ledger")
    for section in sections:
        result.setdefault(section, [])

    assert claim_index is not None or not typed_shards
    for shard in sorted(
        typed_shards,
        key=lambda item: _claim_order_for_shard(item, claim_index) if claim_index is not None else item["order"],
    ):
        payload = shard[section_name]
        for section in sections:
            for entry in payload[section]:
                key = _entry_key(section, entry)
                if key in keys[section]:
                    raise SwarmValidationError(
                        f"duplicate key {key} in section {section} while merging claim {shard['claim_id']}"
                    )
                keys[section].add(key)
                result[section].append(deepcopy(entry))
    return result


def merge_ledger_files(
    base_ledger_path: Path,
    shard_paths: list[Path],
    kind: str,
    output_path: Path,
    claim_map_path: Path | None = None,
    book_root: Path | None = None,
) -> dict[str, Any]:
    claim_map = load_json(claim_map_path) if claim_map_path else None
    merged = merge_ledgers(
        load_json(base_ledger_path),
        [load_json(path) for path in shard_paths],
        kind,
        claim_map,
        book_root,
    )
    atomic_write_json(output_path, merged)
    return merged


def main() -> None:
    parser = argparse.ArgumentParser(description="Deterministically merge Audiobook Codex ledger shards.")
    parser.add_argument("--kind", required=True, choices=sorted(SECTION_CONFIG))
    parser.add_argument("--base-ledger", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--claim-map", type=Path)
    parser.add_argument("--book-root", type=Path)
    parser.add_argument("--shard", action="append", type=Path, default=[], help="Shard JSON path. Repeat in any order.")
    args = parser.parse_args()
    if args.shard and args.claim_map is None:
        parser.error("--claim-map is required when merging non-empty shards")
    if args.shard and args.book_root is None:
        parser.error("--book-root is required when merging non-empty shards")
    try:
        merge_ledger_files(
            args.base_ledger.expanduser().resolve(),
            [path.expanduser().resolve() for path in args.shard],
            args.kind,
            args.output.expanduser().resolve(),
            args.claim_map.expanduser().resolve() if args.claim_map else None,
            args.book_root.expanduser().resolve() if args.book_root else None,
        )
    except RuntimeError as error:
        raise SystemExit(f"INVALID ledger shards: {error}") from error
    print("MERGED ledger shards")


if __name__ == "__main__":
    main()
