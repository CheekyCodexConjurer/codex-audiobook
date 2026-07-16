from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys


SEMVER = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?$")


def load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise RuntimeError(f"Cannot read JSON {path}: {error}") from error


def validate(plugin_root: Path, marketplace_path: Path | None) -> list[str]:
    errors: list[str] = []
    manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
    try:
        manifest = load_json(manifest_path)
    except RuntimeError as error:
        return [str(error)]
    if not isinstance(manifest, dict):
        return ["plugin manifest must be an object"]

    for key in ("name", "version", "description", "skills", "author", "interface"):
        if key not in manifest:
            errors.append(f"plugin manifest is missing {key}")
    if manifest.get("name") != "audiobook-codex":
        errors.append("plugin manifest name must be audiobook-codex")
    if not isinstance(manifest.get("version"), str) or SEMVER.fullmatch(manifest["version"]) is None:
        errors.append("plugin manifest version must be semver")
    if manifest.get("skills") != "./skills/":
        errors.append("plugin manifest skills path must be ./skills/")
    if "[TODO:" in json.dumps(manifest):
        errors.append("plugin manifest contains a TODO placeholder")

    author = manifest.get("author")
    if not isinstance(author, dict) or not isinstance(author.get("name"), str) or not author["name"].strip():
        errors.append("plugin manifest author.name must be non-empty")
    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        errors.append("plugin manifest interface must be an object")
    else:
        for key in ("displayName", "shortDescription", "longDescription", "developerName", "category", "defaultPrompt"):
            if not isinstance(interface.get(key), str) or not interface[key].strip():
                errors.append(f"plugin interface {key} must be non-empty")
        if not isinstance(interface.get("capabilities"), list):
            errors.append("plugin interface capabilities must be an array")

    skill_root = plugin_root / "skills" / "audiobook-codex"
    skill_path = skill_root / "SKILL.md"
    if not skill_path.is_file():
        errors.append("audiobook skill is missing SKILL.md")
    else:
        skill_text = skill_path.read_text(encoding="utf-8")
        if not re.match(r"^---\r?\nname:\s*audiobook-codex\r?\ndescription:\s*.+?\r?\n---\r?\n", skill_text, re.DOTALL):
            errors.append("audiobook skill frontmatter is invalid")
        for relative_path in (
            "references/artifact-contract.md",
            "references/narrator-policy.md",
            "references/swarm-protocol.md",
        ):
            if not (skill_root / relative_path).is_file():
                errors.append(f"audiobook skill is missing {relative_path}")

    calibration_root = plugin_root / "skills" / "voice-calibration"
    calibration_skill = calibration_root / "SKILL.md"
    if not calibration_skill.is_file():
        errors.append("voice-calibration skill is missing SKILL.md")
    else:
        calibration_text = calibration_skill.read_text(encoding="utf-8")
        if not re.match(
            r"^---\r?\nname:\s*voice-calibration\r?\ndescription:\s*.+?\r?\n---\r?\n",
            calibration_text,
            re.DOTALL,
        ):
            errors.append("voice-calibration skill frontmatter is invalid")
        if "[TODO:" in calibration_text:
            errors.append("voice-calibration skill contains a TODO placeholder")
        for relative_path in (
            "agents/openai.yaml",
            "references/protocol.md",
            "references/evidence-contract.md",
            "references/tts-adapter-contract.md",
            "references/chatterbox-v3-pt-br.md",
            "assets/corpus.template.json",
            "assets/candidate.template.json",
            "assets/adapter.template.json",
            "assets/promotion.template.json",
            "assets/report.template.md",
            "scripts/calibration_workspace.py",
            "scripts/init_calibration_workspace.py",
            "scripts/import_calibration_targets.py",
            "scripts/validate_calibration_workspace.py",
            "scripts/test_voice_calibration.py",
        ):
            if not (calibration_root / relative_path).is_file():
                errors.append(f"voice-calibration skill is missing {relative_path}")
        agent_metadata = calibration_root / "agents" / "openai.yaml"
        if agent_metadata.is_file():
            agent_text = agent_metadata.read_text(encoding="utf-8")
            display_name = re.search(r'(?m)^\s*display_name:\s*"([^"]+)"\s*$', agent_text)
            short_description = re.search(
                r'(?m)^\s*short_description:\s*"([^"]+)"\s*$',
                agent_text,
            )
            default_prompt = re.search(
                r'(?m)^\s*default_prompt:\s*"([^"]+)"\s*$',
                agent_text,
            )
            if display_name is None or not display_name.group(1).strip():
                errors.append("voice-calibration agent display_name must be non-empty")
            if (
                short_description is None
                or not 25 <= len(short_description.group(1)) <= 64
            ):
                errors.append(
                    "voice-calibration agent short_description must be 25-64 characters"
                )
            if (
                default_prompt is None
                or "$voice-calibration" not in default_prompt.group(1)
            ):
                errors.append(
                    "voice-calibration agent default_prompt must invoke $voice-calibration"
                )
        for relative_path in (
            "assets/corpus.template.json",
            "assets/candidate.template.json",
            "assets/adapter.template.json",
            "assets/promotion.template.json",
        ):
            try:
                load_json(calibration_root / relative_path)
            except RuntimeError as error:
                errors.append(str(error))

    for filename in (
        "preflight.py",
        "asset_inventory.py",
        "validate_book_map.py",
        "validate_assets_manifest.py",
        "verify_text_ledger.py",
        "verify_translation_ledger.py",
        "verify_revision_ledger.py",
        "validate_narrator_lineage.py",
        "narrator_quality.py",
        "validate_narrator_quality.py",
        "narration_plan.py",
        "validate_narration_plan.py",
        "build_epub_manifest.py",
        "epub_layout.py",
        "validate_epub_layout.py",
        "epub_presentation.py",
        "export_epub.py",
        "validate_epub_export.py",
        "validate_feminina_profile.py",
        "path_safety.py",
        "audio_tools.py",
        "chapter_audio.py",
        "validate_chapter_audio.py",
        "render_chatterbox.py",
        "chatterbox_text.py",
        "publish_artifacts.py",
        "test_tools.py",
    ):
        if not (plugin_root / "scripts" / filename).is_file():
            errors.append(f"plugin is missing scripts/{filename}")
    for filename in (
        "book-map.template.json",
        "assets-manifest.template.json",
        "text-ledger.template.json",
        "translation-ledger.template.json",
        "revision-ledger.template.json",
        "epub-manifest.template.json",
        "epub-layout.template.json",
        "narrator-changes.template.json",
        "narrator-review.template.json",
    ):
        if not (plugin_root / "assets" / filename).is_file():
            errors.append(f"plugin is missing assets/{filename}")
    template_contracts = {
        "book-map.template.json": ("1.0", ("source", "analysis", "pages")),
        "assets-manifest.template.json": ("1.0", ("source_sha256", "assets")),
        "text-ledger.template.json": ("1.0", ("book_map_sha256", "pages")),
        "translation-ledger.template.json": (
            "1.0",
            (
                "book_map_sha256",
                "text_ledger_sha256",
                "source_language",
                "target_language",
                "translation_decision",
                "edition",
                "pages",
                "chapter_outputs",
            ),
        ),
        "revision-ledger.template.json": (
            "1.0",
            (
                "book_map_sha256",
                "text_ledger_sha256",
                "language",
                "status",
                "reviewed_by",
                "changes",
                "chapter_outputs",
            ),
        ),
        "epub-manifest.template.json": (
            "1.0",
            ("book_map_sha256", "text_ledger_sha256", "assets_manifest_sha256", "documents"),
        ),
        "epub-layout.template.json": (
            "1.0",
            ("text_edition", "book_map_sha256", "text_ledger_sha256", "documents"),
        ),
        "narrator-changes.template.json": (
            "2.0",
            (
                "source_book_sha256",
                "book_map_sha256",
                "base_edition",
                "base_ledger_sha256",
                "mode",
                "outputs",
                "changes",
            ),
        ),
        "narrator-review.template.json": (
            "1.0",
            (
                "profile",
                "status",
                "reviewed_by",
                "output_file",
                "output_sha256",
                "narrator_changes_sha256",
                "review_scope",
                "findings",
                "pronunciation_review",
            ),
        ),
    }
    for filename, (schema_version, keys) in template_contracts.items():
        path = plugin_root / "assets" / filename
        if not path.is_file():
            continue
        try:
            template = load_json(path)
        except RuntimeError as error:
            errors.append(str(error))
            continue
        if not isinstance(template, dict):
            errors.append(f"template {filename} must be a JSON object")
            continue
        if schema_version is not None and template.get("schema_version") != schema_version:
            errors.append(f"template {filename} schema_version must be {schema_version}")
        for key in keys:
            if key not in template:
                errors.append(f"template {filename} is missing {key}")
    for filename in (
        "fonts/im-fell-english/IMFeENrm28P.ttf",
        "fonts/im-fell-english/IMFeENit28P.ttf",
        "fonts/im-fell-english/OFL.txt",
        "voices/Feminina.mp3",
        "voices/feminina-v1.promotion.json",
    ):
        if not (plugin_root / "assets" / filename).is_file():
            errors.append(f"plugin is missing assets/{filename}")
    promotion_path = plugin_root / "assets" / "voices" / "feminina-v1.promotion.json"
    if promotion_path.is_file():
        try:
            load_json(promotion_path)
        except RuntimeError as error:
            errors.append(str(error))

    if marketplace_path is not None:
        try:
            marketplace = load_json(marketplace_path)
        except RuntimeError as error:
            errors.append(str(error))
            marketplace = None
        entries = marketplace.get("plugins") if isinstance(marketplace, dict) else None
        entry = next(
            (
                candidate
                for candidate in entries
                if isinstance(candidate, dict) and candidate.get("name") == "audiobook-codex"
            ),
            None,
        ) if isinstance(entries, list) else None
        if entry is None:
            errors.append("marketplace does not include audiobook-codex")
        else:
            source = entry.get("source")
            if not isinstance(source, dict) or source.get("source") != "local":
                errors.append("marketplace audiobook-codex source must be local")
            elif source.get("path") != "./plugins/audiobook-codex":
                errors.append("marketplace audiobook-codex source path must be ./plugins/audiobook-codex")
            policy = entry.get("policy")
            if not isinstance(policy, dict):
                errors.append("marketplace audiobook-codex policy must be an object")
            else:
                if policy.get("installation") != "AVAILABLE":
                    errors.append("marketplace audiobook-codex installation policy must be AVAILABLE")
                if policy.get("authentication") != "ON_INSTALL":
                    errors.append("marketplace audiobook-codex authentication policy must be ON_INSTALL")
            if entry.get("category") != "Productivity":
                errors.append("marketplace audiobook-codex category must be Productivity")

    return errors


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Audiobook Codex without external Python packages.")
    parser.add_argument("--plugin-root", required=True, type=Path)
    parser.add_argument("--marketplace", type=Path)
    args = parser.parse_args()

    errors = validate(args.plugin_root.expanduser().resolve(), args.marketplace.expanduser().resolve() if args.marketplace else None)
    if errors:
        print("INVALID Audiobook Codex plugin:", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        raise SystemExit(1)
    print("VALID Audiobook Codex plugin")


if __name__ == "__main__":
    main()
