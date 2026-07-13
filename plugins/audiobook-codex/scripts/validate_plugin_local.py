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

    for filename in (
        "preflight.py",
        "validate_book_map.py",
        "verify_text_ledger.py",
        "render_kokoro.py",
        "test_tools.py",
    ):
        if not (plugin_root / "scripts" / filename).is_file():
            errors.append(f"plugin is missing scripts/{filename}")
    for filename in (
        "book-map.template.json",
        "text-ledger.template.json",
        "narrator-changes.template.json",
    ):
        if not (plugin_root / "assets" / filename).is_file():
            errors.append(f"plugin is missing assets/{filename}")

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
