from __future__ import annotations

import json
import re
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from assemble_text_outputs import (
    SwarmValidationError,
    assemble_fluid_book,
    assemble_source_outputs,
    assemble_translation_outputs,
    join_text_units,
)
from merge_ledger_shards import merge_ledgers
from swarm_claims import (
    REQUIRED_READS_BY_STAGE,
    SHARD_STAGE_BY_KIND,
    claim_digest,
    sha256_bytes,
    sha256_file,
    sha256_json,
    validate_claim_map,
)


def text_hash(text: str) -> str:
    return sha256_bytes(text.encode("utf-8"))


class SwarmWorkflowTests(unittest.TestCase):
    def valid_claim(self, claim_id: str, target: str, *, order: int = 1) -> dict:
        return {
            "claim_id": claim_id,
            "stage": "TRANSCRIBE",
            "status": "planned",
            "claim_order": order,
            "priority": order,
            "depends_on": [],
            "producer": "producer",
            "verifier": "verifier",
            "read_set": [{"path": "metadata/book-map.json", "sha256": "0" * 64}],
            "write_set": [target],
            "canonical_targets": [target],
            "no_touch": [],
            "scope": {
                "unit_kind": "chapter",
                "unit_ids": [f"chapter-{order:02d}"],
                "context_unit_ids": [],
            },
            "context": {},
            "validation": {"requires_verification": True, "commands": []},
            "lease": {"holder": "", "issued_at": "", "expires_at": ""},
        }

    def freeze_required_reads(self, root: Path, *claims: dict) -> None:
        for claim in claims:
            paths = REQUIRED_READS_BY_STAGE.get(claim["stage"], ())
            claim["read_set"] = []
            for path in paths:
                content = f"{path}\n"
                disk_path = root / path
                disk_path.parent.mkdir(parents=True, exist_ok=True)
                disk_path.write_text(content, encoding="utf-8", newline="\n")
                claim["read_set"].append({"path": path, "sha256": text_hash(content)})

    def test_claim_map_rejects_unsafe_paths_overlap_and_same_verifier(self) -> None:
        claim_a = self.valid_claim("A", "text/source/chapter-01.txt")
        claim_b = self.valid_claim("B", "text/source/chapter-02.txt", order=2)
        claim_b["write_set"] = ["../escape.txt"]
        claim_b["producer"] = "same"
        claim_b["verifier"] = "same"
        claim_map = {"schema_version": "1.0", "claims": [claim_a, claim_b]}
        errors = validate_claim_map(claim_map)
        self.assertTrue(any("must not contain" in error for error in errors), errors)
        self.assertTrue(any("producer and verifier" in error for error in errors), errors)

        claim_b = self.valid_claim("B", "text/source", order=2)
        errors = validate_claim_map({"schema_version": "1.0", "claims": [claim_a, claim_b]})
        self.assertTrue(any("overlap" in error for error in errors), errors)

    def test_claim_map_verifies_read_hashes_against_book_root(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "metadata" / "book-map.json"
            source.parent.mkdir(parents=True)
            source.write_text("map", encoding="utf-8")
            claim = self.valid_claim("A", "text/source/chapter-01.txt")
            claim["read_set"] = [{"path": "metadata/book-map.json", "sha256": sha256_file(source)}]
            self.assertEqual(validate_claim_map({"schema_version": "1.0", "claims": [claim]}, root), [])
            claim["read_set"][0]["sha256"] = "0" * 64
            errors = validate_claim_map({"schema_version": "1.0", "claims": [claim]}, root)
            self.assertTrue(any("does not match" in error for error in errors), errors)

    def test_claim_map_requires_structural_reads_targets_and_order(self) -> None:
        claim = self.valid_claim("A", "text/source/chapter-01.txt")
        claim["read_set"] = []
        errors = validate_claim_map({"schema_version": "1.0", "claims": [claim]})
        self.assertTrue(any("metadata/book-map.json" in error for error in errors), errors)

        claim = self.valid_claim("A", "text/source/chapter-01.txt")
        claim["write_set"] = []
        errors = validate_claim_map({"schema_version": "1.0", "claims": [claim]})
        self.assertTrue(any("write_set must be non-empty" in error for error in errors), errors)

        claim = self.valid_claim("A", "text/source/chapter-01.txt")
        claim["canonical_targets"] = []
        errors = validate_claim_map({"schema_version": "1.0", "claims": [claim]})
        self.assertTrue(any("canonical_targets must be non-empty" in error for error in errors), errors)

        claim_a = self.valid_claim("A", "text/source/chapter-01.txt")
        claim_b = self.valid_claim("B", "text/source/chapter-02.txt", order=3)
        errors = validate_claim_map({"schema_version": "1.0", "claims": [claim_a, claim_b]})
        self.assertTrue(any("claim_order must be contiguous" in error for error in errors), errors)

        map_claim = self.valid_claim("M", "metadata/work/map-shard.json")
        map_claim["stage"] = "MAP"
        map_claim["read_set"] = []
        map_claim["write_set"] = []
        map_claim["canonical_targets"] = []
        self.assertEqual([], validate_claim_map({"schema_version": "1.0", "claims": [map_claim]}))

        map_claim["write_set"] = ["metadata/work/map-shard.json"]
        errors = validate_claim_map({"schema_version": "1.0", "claims": [map_claim]})
        self.assertTrue(any("read-only" in error for error in errors), errors)

    def test_claim_map_rejects_unknown_stage(self) -> None:
        claim = self.valid_claim("A", "text/source/chapter-01.txt")
        claim["stage"] = "TYPO"
        errors = validate_claim_map({"schema_version": "1.0", "claims": [claim]})
        self.assertTrue(any("stage must be one of" in error for error in errors), errors)

    def test_validate_claim_map_cli_rejects_missing_required_read(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            claim = self.valid_claim("A", "text/source/chapter-01.txt")
            claim["read_set"] = []
            claim_map_path = root / "claim-map.json"
            claim_map_path.write_text(
                json.dumps({"schema_version": "1.0", "claims": [claim]}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent / "validate_claim_map.py"),
                    str(claim_map_path),
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("metadata/book-map.json", completed.stderr)

    def test_merge_rejects_hash_drift_order_gaps_and_duplicates(self) -> None:
        claim_a = self.valid_claim("A", "text/source/pages/page-0001.txt")
        claim_b = self.valid_claim("B", "text/source/pages/page-0002.txt", order=2)
        claim_a["status"] = "verified"
        claim_b["status"] = "verified"
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.freeze_required_reads(root, claim_a, claim_b)
        claim_map = {"schema_version": "1.0", "claims": [claim_a, claim_b]}
        shard_a = {
            "schema_version": "1.0",
            "shard_kind": "text",
            "claim_id": "A",
            "claim_sha256": claim_digest(claim_a),
            "producer": "producer",
            "verifier": "verifier",
            "order": 1,
            "text": {"pages": [{"logical_page": 1}], "chapter_outputs": [{"id": "c1"}]},
        }
        shard_b = deepcopy(shard_a)
        shard_b.update({"claim_id": "B", "claim_sha256": claim_digest(claim_b), "order": 1})
        shard_b["text"] = {"pages": [{"logical_page": 2}], "chapter_outputs": [{"id": "c2"}]}
        shard_a["order"] = 2
        with self.assertRaisesRegex(SwarmValidationError, "claim_order"):
            merge_ledgers({"schema_version": "1.0", "pages": [], "chapter_outputs": []}, [shard_a, shard_b], "text", claim_map, root)

        shard_a["order"] = 1
        shard_b["order"] = 2
        shard_b["claim_sha256"] = "0" * 64
        with self.assertRaisesRegex(SwarmValidationError, "diverges"):
            merge_ledgers({"schema_version": "1.0", "pages": [], "chapter_outputs": []}, [shard_a, shard_b], "text", claim_map, root)

        shard_b["claim_sha256"] = claim_digest(claim_b)
        shard_b["text"]["pages"] = [{"logical_page": 1}]
        with self.assertRaisesRegex(SwarmValidationError, "duplicate key"):
            merge_ledgers({"schema_version": "1.0", "pages": [], "chapter_outputs": []}, [shard_a, shard_b], "text", claim_map, root)

    def test_nonempty_merge_requires_claim_map_in_api_and_cli(self) -> None:
        shard = {
            "schema_version": "1.0",
            "shard_kind": "text",
            "claim_id": "A",
            "claim_sha256": "0" * 64,
            "producer": "producer",
            "verifier": "verifier",
            "order": 1,
            "text": {"pages": [{"logical_page": 1}], "chapter_outputs": [{"id": "chapter-01"}]},
        }
        with self.assertRaisesRegex(SwarmValidationError, "requires a claim map"):
            merge_ledgers(
                {"schema_version": "1.0", "pages": [], "chapter_outputs": []},
                [shard],
                "text",
            )
        claim = self.valid_claim("A", "text/source/pages/page-0001.txt")
        claim["status"] = "verified"
        shard["claim_sha256"] = claim_digest(claim)
        with self.assertRaisesRegex(SwarmValidationError, "requires a book root"):
            merge_ledgers(
                {"schema_version": "1.0", "pages": [], "chapter_outputs": []},
                [shard],
                "text",
                {"schema_version": "1.0", "claims": [claim]},
            )

        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            base_path = root / "base.json"
            shard_path = root / "shard.json"
            output_path = root / "merged.json"
            base_path.write_text(
                json.dumps({"schema_version": "1.0", "pages": [], "chapter_outputs": []}),
                encoding="utf-8",
            )
            shard_path.write_text(json.dumps(shard), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent / "merge_ledger_shards.py"),
                    "--kind",
                    "text",
                    "--base-ledger",
                    str(base_path),
                    "--output",
                    str(output_path),
                    "--shard",
                    str(shard_path),
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("--claim-map is required", completed.stderr)

            claim_map_path = root / "claim-map.json"
            claim_map_path.write_text(json.dumps({"schema_version": "1.0", "claims": [claim]}), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent / "merge_ledger_shards.py"),
                    "--kind",
                    "text",
                    "--base-ledger",
                    str(base_path),
                    "--output",
                    str(output_path),
                    "--claim-map",
                    str(claim_map_path),
                    "--shard",
                    str(shard_path),
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("--book-root is required", completed.stderr)

    def test_nonempty_merge_revalidates_read_hashes_before_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            claim = self.valid_claim("A", "text/source/pages/page-0001.txt")
            claim["status"] = "verified"
            self.freeze_required_reads(root, claim)
            shard = {
                "schema_version": "1.0",
                "shard_kind": "text",
                "claim_id": "A",
                "claim_sha256": claim_digest(claim),
                "producer": "producer",
                "verifier": "verifier",
                "order": 1,
                "text": {"pages": [{"logical_page": 1}], "chapter_outputs": [{"id": "chapter-01"}]},
            }
            (root / "metadata" / "book-map.json").write_text("mutated\n", encoding="utf-8", newline="\n")
            with self.assertRaisesRegex(SwarmValidationError, "does not match current file"):
                merge_ledgers(
                    {"schema_version": "1.0", "pages": [], "chapter_outputs": []},
                    [shard],
                    "text",
                    {"schema_version": "1.0", "claims": [claim]},
                    root,
                )

            base_path = root / "base.json"
            shard_path = root / "shard.json"
            claim_map_path = root / "claim-map.json"
            output_path = root / "merged.json"
            output_path.write_text("keep\n", encoding="utf-8")
            base_path.write_text(
                json.dumps({"schema_version": "1.0", "pages": [], "chapter_outputs": []}),
                encoding="utf-8",
            )
            shard_path.write_text(json.dumps(shard), encoding="utf-8")
            claim_map_path.write_text(json.dumps({"schema_version": "1.0", "claims": [claim]}), encoding="utf-8")
            completed = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).resolve().parent / "merge_ledger_shards.py"),
                    "--kind",
                    "text",
                    "--base-ledger",
                    str(base_path),
                    "--output",
                    str(output_path),
                    "--claim-map",
                    str(claim_map_path),
                    "--book-root",
                    str(root),
                    "--shard",
                    str(shard_path),
                ],
                text=True,
                capture_output=True,
            )
            self.assertNotEqual(0, completed.returncode)
            self.assertIn("does not match current file", completed.stderr)
            self.assertEqual("keep\n", output_path.read_text(encoding="utf-8"))

    def test_merge_is_deterministic_and_preserves_headers(self) -> None:
        claim_a = self.valid_claim("A", "text/source/pages/page-0001.txt")
        claim_b = self.valid_claim("B", "text/source/pages/page-0002.txt", order=2)
        claim_a["status"] = "verified"
        claim_b["status"] = "verified"
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.freeze_required_reads(root, claim_a, claim_b)
        claim_map = {"schema_version": "1.0", "claims": [claim_a, claim_b]}
        shard_a = {
            "schema_version": "1.0",
            "shard_kind": "text",
            "claim_id": "A",
            "claim_sha256": claim_digest(claim_a),
            "producer": "producer",
            "verifier": "verifier",
            "order": 1,
            "text": {"pages": [{"logical_page": 1}], "chapter_outputs": [{"id": "c1"}]},
        }
        shard_b = {
            "schema_version": "1.0",
            "shard_kind": "text",
            "claim_id": "B",
            "claim_sha256": claim_digest(claim_b),
            "producer": "producer",
            "verifier": "verifier",
            "order": 2,
            "text": {"pages": [{"logical_page": 2}], "chapter_outputs": [{"id": "c2"}]},
        }
        base = {"schema_version": "1.0", "book_map_sha256": "keep", "pages": [], "chapter_outputs": []}
        merged = merge_ledgers(base, [shard_b, shard_a], "text", claim_map, root)
        self.assertEqual(merged["book_map_sha256"], "keep")
        self.assertEqual([page["logical_page"] for page in merged["pages"]], [1, 2])
        self.assertEqual([entry["id"] for entry in merged["chapter_outputs"]], ["c1", "c2"])

    def test_merge_translation_and_fluid_canonical_sections(self) -> None:
        claim_t = self.valid_claim("T", "text/translation/pt-BR/chapters/chapter-01.txt")
        claim_f = self.valid_claim("F", "text/fluid/pt-BR/chapters/chapter-01.txt")
        claim_t["stage"] = "TRANSLATE"
        claim_f["stage"] = "FLUID"
        claim_t["status"] = "verified"
        claim_f["status"] = "verified"
        root_t = Path(self.enterContext(tempfile.TemporaryDirectory()))
        root_f = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.freeze_required_reads(root_t, claim_t)
        self.freeze_required_reads(root_f, claim_f)
        translation_shard = {
            "schema_version": "1.0",
            "shard_kind": "translation",
            "claim_id": "T",
            "claim_sha256": claim_digest(claim_t),
            "producer": "producer",
            "verifier": "verifier",
            "order": 1,
            "translation": {
                "pages": [{"logical_page": 1}],
                "chapter_outputs": [{"id": "chapter-01"}],
                "glossary_proposals": [{"id": "g1", "term": "x"}],
                "ambiguities": [{"id": "a1", "source_span": "x"}],
            },
        }
        merged_translation = merge_ledgers(
            {"schema_version": "1.1", "source_language": "en", "pages": [], "chapter_outputs": []},
            [translation_shard],
            "translation",
            {"schema_version": "1.0", "claims": [claim_t]},
            root_t,
        )
        self.assertEqual(merged_translation["source_language"], "en")
        self.assertEqual(merged_translation["glossary_proposals"][0]["id"], "g1")
        self.assertEqual(merged_translation["ambiguities"][0]["id"], "a1")

        fluid_shard = {
            "schema_version": "1.0",
            "shard_kind": "fluid",
            "claim_id": "F",
            "claim_sha256": claim_digest(claim_f),
            "producer": "producer",
            "verifier": "verifier",
            "order": 1,
            "fluid": {
                "chapter_outputs": [{"id": "chapter-01"}],
                "blocks": [{"id": "chapter-01-b0001"}],
            },
        }
        merged_fluid = merge_ledgers(
            {"schema_version": "1.2", "profile": "fluid-faithful-ptbr-v1", "chapter_outputs": [], "blocks": []},
            [fluid_shard],
            "fluid",
            {"schema_version": "1.0", "claims": [claim_f]},
            root_f,
        )
        self.assertEqual(merged_fluid["profile"], "fluid-faithful-ptbr-v1")
        self.assertEqual(merged_fluid["blocks"][0]["id"], "chapter-01-b0001")

    def test_merge_rejects_incompatible_claim_stage_for_shard_kind(self) -> None:
        def payload_for(kind: str) -> dict:
            if kind == "text":
                return {
                    "text": {
                        "pages": [{"logical_page": 1}],
                        "chapter_outputs": [{"id": "chapter-01"}],
                    }
                }
            if kind == "translation":
                return {
                    "translation": {
                        "pages": [{"logical_page": 1}],
                        "chapter_outputs": [{"id": "chapter-01"}],
                        "glossary_proposals": [],
                        "ambiguities": [],
                    }
                }
            if kind == "fluid":
                return {
                    "fluid": {
                        "chapter_outputs": [{"id": "chapter-01"}],
                        "blocks": [{"id": "chapter-01-b0001"}],
                    }
                }
            raise AssertionError(f"unknown kind {kind}")

        def base_for(kind: str) -> dict:
            if kind in {"text", "translation"}:
                return {"schema_version": "1.0", "pages": [], "chapter_outputs": []}
            if kind == "fluid":
                return {"schema_version": "1.2", "chapter_outputs": [], "blocks": []}
            raise AssertionError(f"unknown kind {kind}")

        stages = ("MAP", "TRANSCRIBE", "TRANSLATE", "FLUID", "RENDER")
        for kind, expected_stage in SHARD_STAGE_BY_KIND.items():
            for stage in stages:
                if stage == expected_stage:
                    continue
                with self.subTest(kind=kind, stage=stage):
                    claim = self.valid_claim("A", "text/source/pages/page-0001.txt")
                    claim["stage"] = stage
                    claim["status"] = "verified"
                    if stage == "MAP":
                        claim["read_set"] = []
                        claim["write_set"] = []
                        claim["canonical_targets"] = []
                    elif stage == "RENDER":
                        claim["read_set"] = []
                    root = Path(self.enterContext(tempfile.TemporaryDirectory()))
                    self.freeze_required_reads(root, claim)
                    shard = {
                        "schema_version": "1.0",
                        "shard_kind": kind,
                        "claim_id": "A",
                        "claim_sha256": claim_digest(claim),
                        "producer": "producer",
                        "verifier": "verifier",
                        "order": 1,
                        **payload_for(kind),
                    }
                    with self.assertRaisesRegex(SwarmValidationError, "incompatible"):
                        merge_ledgers(
                            base_for(kind),
                            [shard],
                            kind,
                            {"schema_version": "1.0", "claims": [claim]},
                            root,
                        )

        claim = self.valid_claim("A", "text/source/pages/page-0001.txt")
        claim["stage"] = "NARRATE"
        claim["status"] = "verified"
        shard = {
            "schema_version": "1.0",
            "shard_kind": "text",
            "claim_id": "A",
            "claim_sha256": claim_digest(claim),
            "producer": "producer",
            "verifier": "verifier",
            "order": 1,
            "text": {"pages": [{"logical_page": 1}], "chapter_outputs": [{"id": "chapter-01"}]},
        }
        with self.assertRaisesRegex(SwarmValidationError, "stage must be one of"):
            merge_ledgers(
                {"schema_version": "1.0", "pages": [], "chapter_outputs": []},
                [shard],
                "text",
                {"schema_version": "1.0", "claims": [claim]},
                Path(self.enterContext(tempfile.TemporaryDirectory())),
            )

    def test_claim_digest_is_stable_across_lifecycle_only_changes(self) -> None:
        claim = self.valid_claim("A", "text/source/pages/page-0001.txt")
        digest = claim_digest(claim)
        claim["status"] = "verified"
        claim["lease"] = {"holder": "worker", "issued_at": "now", "expires_at": "later"}
        self.assertEqual(digest, claim_digest(claim))

    def test_claim_digest_rejects_immutable_contract_drift(self) -> None:
        claim = self.valid_claim("A", "text/source/pages/page-0001.txt")
        digest = claim_digest(claim)
        claim["validation"]["commands"] = ["python verify_text_ledger.py"]
        self.assertNotEqual(digest, claim_digest(claim))

    def test_merge_requires_verified_claim_status(self) -> None:
        claim = self.valid_claim("A", "text/source/pages/page-0001.txt")
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.freeze_required_reads(root, claim)
        shard = {
            "schema_version": "1.0",
            "shard_kind": "text",
            "claim_id": "A",
            "claim_sha256": claim_digest(claim),
            "producer": "producer",
            "verifier": "verifier",
            "order": 1,
            "text": {"pages": [{"logical_page": 1}], "chapter_outputs": [{"id": "chapter-01"}]},
        }
        for status in ("planned", "ready_for_verification"):
            claim["status"] = status
            shard["claim_sha256"] = claim_digest(claim)
            with self.assertRaisesRegex(SwarmValidationError, "must be verified before merge"):
                merge_ledgers(
                    {"schema_version": "1.0", "pages": [], "chapter_outputs": []},
                    [shard],
                    "text",
                    {"schema_version": "1.0", "claims": [claim]},
                    root,
                )
        claim["status"] = "verified"
        shard["claim_sha256"] = claim_digest(claim)
        merged = merge_ledgers(
            {"schema_version": "1.0", "pages": [], "chapter_outputs": []},
            [shard],
            "text",
            {"schema_version": "1.0", "claims": [claim]},
            root,
        )
        self.assertEqual([{"logical_page": 1}], merged["pages"])

    def test_merge_rejects_unknown_claim_even_with_verified_references(self) -> None:
        claim = self.valid_claim("A", "text/source/pages/page-0001.txt")
        claim["status"] = "verified"
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.freeze_required_reads(root, claim)
        shard = {
            "schema_version": "1.0",
            "shard_kind": "text",
            "claim_id": "B",
            "claim_sha256": claim_digest(claim),
            "producer": "producer",
            "verifier": "verifier",
            "order": 1,
            "text": {"pages": [{"logical_page": 1}], "chapter_outputs": [{"id": "chapter-01"}]},
        }
        with self.assertRaisesRegex(SwarmValidationError, "not present in claim map"):
            merge_ledgers(
                {"schema_version": "1.0", "pages": [], "chapter_outputs": []},
                [shard],
                "text",
                {"schema_version": "1.0", "claims": [claim]},
                root,
            )

    def test_merge_rejects_legacy_full_claim_hash(self) -> None:
        claim = self.valid_claim("A", "text/source/pages/page-0001.txt")
        claim["status"] = "verified"
        root = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.freeze_required_reads(root, claim)
        shard = {
            "schema_version": "1.0",
            "shard_kind": "text",
            "claim_id": "A",
            "claim_sha256": sha256_json(claim),
            "producer": "producer",
            "verifier": "verifier",
            "order": 1,
            "text": {"pages": [{"logical_page": 1}], "chapter_outputs": [{"id": "chapter-01"}]},
        }
        with self.assertRaisesRegex(SwarmValidationError, "claim_sha256 diverges"):
            merge_ledgers(
                {"schema_version": "1.0", "pages": [], "chapter_outputs": []},
                [shard],
                "text",
                {"schema_version": "1.0", "claims": [claim]},
                root,
            )

    def test_artifact_contract_claim_map_example_is_valid_json_and_schema(self) -> None:
        contract_path = (
            Path(__file__).resolve().parents[1]
            / "skills"
            / "audiobook-codex"
            / "references"
            / "artifact-contract.md"
        )
        contract = contract_path.read_text(encoding="utf-8")
        match = re.search(
            r"A schema `1\.0` claim map binds one immutable unit of work:\n\n```json\n(.*?)\n```",
            contract,
            re.S,
        )
        self.assertIsNotNone(match)
        example = json.loads(match.group(1))
        self.assertEqual([], validate_claim_map(example))

    def test_assemble_source_translation_and_fluid_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source/pages").mkdir(parents=True)
            (root / "translation/pt-BR/pages").mkdir(parents=True)
            (root / "fluid/pt-BR/chapters").mkdir(parents=True)
            p1 = "Source page 1\n"
            p2 = "Source page 2\n"
            t1 = "Página traduzida 1\n"
            t2 = "Página traduzida 2\n"
            (root / "source/pages/page-0001.txt").write_text(p1, encoding="utf-8", newline="\n")
            (root / "source/pages/page-0002.txt").write_text(p2, encoding="utf-8", newline="\n")
            (root / "translation/pt-BR/pages/page-0001.txt").write_text(t1, encoding="utf-8", newline="\n")
            (root / "translation/pt-BR/pages/page-0002.txt").write_text(t2, encoding="utf-8", newline="\n")
            source_chapter = join_text_units([p1, p2])
            translation_chapter = join_text_units([t1, t2])
            source_ledger = {
                "pages": [
                    {"logical_page": 1, "status": "verified", "source_file": "source/pages/page-0001.txt", "source_sha256": text_hash(p1)},
                    {"logical_page": 2, "status": "verified", "source_file": "source/pages/page-0002.txt", "source_sha256": text_hash(p2)},
                ],
                "chapter_outputs": [
                    {
                        "id": "chapter-01",
                        "source_file": "source/chapters/chapter-01.txt",
                        "source_sha256": text_hash(source_chapter),
                        "source_pages": [{"logical_page": 1}, {"logical_page": 2}],
                    }
                ],
            }
            translation_ledger = {
                "pages": [
                    {"logical_page": 1, "status": "verified", "translation_file": "translation/pt-BR/pages/page-0001.txt", "translation_sha256": text_hash(t1)},
                    {"logical_page": 2, "status": "verified", "translation_file": "translation/pt-BR/pages/page-0002.txt", "translation_sha256": text_hash(t2)},
                ],
                "chapter_outputs": [
                    {
                        "id": "chapter-01",
                        "translation_file": "translation/pt-BR/chapters/chapter-01.txt",
                        "translation_sha256": text_hash(translation_chapter),
                        "source_pages": [{"logical_page": 1}, {"logical_page": 2}],
                    }
                ],
            }
            assemble_source_outputs(source_ledger, root, "source/book.txt")
            assemble_translation_outputs(translation_ledger, root, "translation/pt-BR/book.txt")
            self.assertEqual((root / "source/chapters/chapter-01.txt").read_text(encoding="utf-8"), source_chapter)
            self.assertEqual((root / "source/book.txt").read_text(encoding="utf-8"), source_chapter)
            self.assertEqual((root / "translation/pt-BR/chapters/chapter-01.txt").read_text(encoding="utf-8"), translation_chapter)
            self.assertEqual((root / "translation/pt-BR/book.txt").read_text(encoding="utf-8"), translation_chapter)

            fluid_chapter = "Texto fluido.\n"
            (root / "fluid/pt-BR/chapters/chapter-01.txt").write_text(fluid_chapter, encoding="utf-8", newline="\n")
            fluid_ledger = {
                "chapter_outputs": [
                    {
                        "id": "chapter-01",
                        "fluid_file": "fluid/pt-BR/chapters/chapter-01.txt",
                        "fluid_sha256": text_hash(fluid_chapter),
                    }
                ],
                "book_output": {
                    "fluid_file": "fluid/pt-BR/book.txt",
                    "fluid_sha256": text_hash(join_text_units([fluid_chapter])),
                },
            }
            assemble_fluid_book(fluid_ledger, root)
            self.assertEqual((root / "fluid/pt-BR/book.txt").read_text(encoding="utf-8"), fluid_chapter)

    def test_assembly_failure_does_not_replace_existing_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "source/pages").mkdir(parents=True)
            (root / "source/chapters").mkdir(parents=True)
            (root / "source/pages/page-0001.txt").write_text("new\n", encoding="utf-8", newline="\n")
            existing = root / "source/chapters/chapter-01.txt"
            existing.write_text("old\n", encoding="utf-8")
            ledger = {
                "pages": [
                    {"logical_page": 1, "status": "verified", "source_file": "source/pages/page-0001.txt", "source_sha256": text_hash("new\n")}
                ],
                "chapter_outputs": [
                    {
                        "id": "chapter-01",
                        "source_file": "source/chapters/chapter-01.txt",
                        "source_sha256": "0" * 64,
                        "source_pages": [{"logical_page": 1}],
                    }
                ],
            }
            with self.assertRaises(SwarmValidationError):
                assemble_source_outputs(ledger, root, "source/book.txt")
            self.assertEqual(existing.read_text(encoding="utf-8"), "old\n")
            self.assertFalse((root / "source/book.txt").exists())


if __name__ == "__main__":
    unittest.main(verbosity=2)
