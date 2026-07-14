---
name: audiobook-codex
description: Build a local audiobook and semantic EPUB from a PDF or EPUB using only Codex, native tools, Codex agents, the PDF plugin, optional Computer Use, native image editing, and the local Chatterbox PT-BR runtime. Use for book mapping, asset inventory, faithful source transcription, PT-BR narrator text, EPUB export, optional reviewed image restoration, and local audiobook audio without third-party OCR or external LLMs.
---

# Audiobook Codex

Use this skill as a three-stage pipeline. Keep the source artifact, visual assets, narrator artifact, EPUB exports, and generated audio traceable to the same source hash and `book-map.json`.

Read [artifact-contract.md](references/artifact-contract.md) before any stage. Read [swarm-protocol.md](references/swarm-protocol.md) when delegating. Read [narrator-policy.md](references/narrator-policy.md) before creating narrator text.

Before adding or replacing a local voice reference, TTS engine, or official narrator
profile, use `$voice-calibration`. Do not calibrate against hosted TTS from this
pipeline and do not alter `feminina-v1` without its separate promotion evidence.

## Non-Negotiable Rules

- Use no third-party OCR service, external LLM, browser chat UI, or hosted TTS.
- Use the PDF plugin and rendered images as the primary visual source. Use Computer Use only when a desktop viewer is necessary for zoom, navigation, or screenshots.
- Treat PDF text extraction as evidence, never as an authoritative transcription.
- Never alter `text/source`. Create all speech-oriented changes only in `text/locutor`.
- Keep `source/`, `pages/`, and `assets/images/original/` immutable after preflight.
- Never transcribe from restored or generated pixels. `text/source` may use only original PDF or EPUB evidence.
- A generated image is a labeled derivative, never a replacement for an original scan or a claim about unreadable source content.
- Never restore text, handwriting, captions, signatures, seals, or other evidence-bearing pixels with image generation.
- A user-approved full-scan cleanup is a `manual_exception`: retain the original, record the exception reason, publish it only in the restored EPUB, and never use it as textual evidence.
- Do not claim source fidelity until every mapped unit has a verified text-ledger record.
- Stop for review instead of guessing an uncertain word. Do not put uncertainty placeholders in final TXT files.

## Stage 1: Map

1. Use `E:\Pessoal\e-books` as the default library root. Never write book artifacts beside an attached source because Codex attachment paths can be temporary.
2. Run `scripts/preflight.py --source <attachment> --library-root "E:\Pessoal\e-books"` with the PDF or EPUB source. It creates an isolated book directory, copies the input to `source/original.pdf` or `source/original.epub`, produces rendered page assets, `metadata/book-map.json`, and `metadata/assets-manifest.json`.
3. Inspect source pages or EPUB units. Establish layout, rotation, logical page order, printed-page alignment, TOC, content range, chapter starts, blanks, exclusions, and visual asset classifications.
4. For every visual asset, record source page or EPUB locator, role, whether text-bearing pixels exist, restoration eligibility, and evidence. Preserve the extracted original regardless of eligibility.
5. Complete the map with evidence for each decision. Use segmented page offsets when preliminary pages and main matter do not share one offset.
6. Run `scripts/validate_book_map.py --require-ready --check-files` and `scripts/validate_assets_manifest.py`.

Do not begin transcription until the map is valid and its `analysis.status` is `ready` or `approved`.

## Stage 2: Faithful Text

1. Transcribe from rendered source pages or EPUB source into per-unit files under `text/source/pages`.
2. Preserve wording, order, spelling, punctuation, and visible language. Do not correct, modernize, translate, expand numbers, or normalize prose.
3. Record each unit in `metadata/text-ledger.json`, including its file hash and independent verification state.
4. Concatenate only verified source units into chapter and book TXT outputs without synthetic page markers.
5. Run `scripts/verify_text_ledger.py --book-map ... --ledger ... --text-root ...`.
6. Run `scripts/build_epub_manifest.py --visual-profile antique-paper` only after the ledger passes. Review the generated reading order and image placement before publication. Do not invent captions or alt text from uncertain pixels.

For a source already in Portuguese, faithful text is PT-BR only to the extent the source itself is PT-BR. For another source language, a literal artifact and a PT-BR adaptation cannot be the same file.

## Stage 3: Narrator, Audio, and EPUB

1. Derive `text/locutor` from verified `text/source`.
2. Write `metadata/narrator-changes.json` for every allowed narrator transformation.
3. Validate that every narrator segment references a verified source segment.
4. Run `scripts/render_chatterbox.py` through the dedicated Chatterbox PT-BR environment. Use the default `feminina-v1` profile, which writes segment audio, a final WAV and MP3 delivery file, and `metadata/audio-manifest.json`.
5. Keep source hashes, narrator hashes, voice, speed, segment hashes, and durations in the manifest. Re-render only segments whose narrator hash changed.
6. The default `antique-paper` EPUB profile embeds IM FELL English with its OFL license, uses the fixed warm paper palette, and generates a local editorial cover from verified title, subtitle, author, place, and year. It never replaces the source cover/title page.
7. Run `scripts/export_epub.py` with `--image-edition original` to create the canonical semantic EPUB from `text/source`, verified manifests, and extracted original assets.
8. Optional restoration is review-gated. Use the native `image_gen` tool only for an asset classified as non-text and review-eligible. A full scan with text requires an explicit `manual_exception` and exception reason. Save candidates under `restoration/candidates/`, record provenance, compare visually against the original, and promote only approved assets under `restoration/approved/`.
9. Run `scripts/export_epub.py --image-edition approved-restored` only after every selected derivative has a complete approval record. This produces a separate restored edition; it never replaces the canonical EPUB.
10. Run `scripts/publish_artifacts.py --book-root <book-root> --audio <final-audio> --epub <final-epub>` after validation. It copies only the unified audiobook and chosen EPUB into the root of the book folder, while preserving the provenance outputs under `audio/` and `exports/epub/`.

## Tool Choice

- PDF with usable text layer: extract local text for comparison, then visually verify it.
- Image-only or mixed PDF: render pages and have Codex read the images directly.
- EPUB: inspect the EPUB spine/XHTML source directly; do not OCR rendered EPUB pages.
- Computer Use: one agent only, serially, when native rendering cannot resolve a visual ambiguity.
- Native image restoration: use the built-in `image_gen` tool, not an API-key script or browser chat. Load a local edit target into the task first, restate every invariant in the prompt, and require a human approval record before export.
- Printed text and handwriting: use only original assets or deterministic local cleanup. Do not use generated output as a source for transcription or semantic EPUB text.
- Chatterbox PT-BR: use `E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe`, the local V3 PT-BR model files, and the bundled `assets/voices/Feminina.mp3` reference. The official render profile is `feminina-v1`; use line-delimited narrator text of at most 320 characters per non-empty line. Do not send the voice reference or narrator text to a hosted TTS service.
- When installed, use `audiobook-structure`, `audiobook-transcriber`, and `audiobook-verifier` for the matching swarm roles. Otherwise use equivalent bounded roles with the same write boundaries.

## Validation

Run the smallest relevant check after each stage. Before declaring completion, run:

```powershell
python scripts/validate_book_map.py --book-map <book-map.json> --require-ready --check-files
python scripts/validate_assets_manifest.py --assets-manifest <assets-manifest.json> --book-root <book-root>
python scripts/verify_text_ledger.py --book-map <book-map.json> --ledger <text-ledger.json> --text-root <text-root>
python scripts/build_epub_manifest.py --book-map <book-map.json> --ledger <text-ledger.json> --assets-manifest <assets-manifest.json> --text-root <text-root> --visual-profile antique-paper
python scripts/export_epub.py --book-root <book-root> --image-edition original
python scripts/validate_epub_export.py --book-root <book-root> --epub <output.epub> --image-edition original
E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe scripts/render_chatterbox.py --book-root <book-root> --input-file <locutor.txt> --output-dir <audio-dir> --format mp3
python scripts/publish_artifacts.py --book-root <book-root> --audio <audio-dir>\audiobook.mp3 --epub <output.epub>
```

Use `--mock` only for validation. Never publish mock audio.
