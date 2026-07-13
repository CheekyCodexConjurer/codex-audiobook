---
name: audiobook-codex
description: Build a local audiobook from a PDF or EPUB using only Codex, native tools, Codex agents, the PDF plugin, optional Computer Use, and a local Kokoro runtime. Use for book mapping, chapter and page offset analysis, faithful source transcription, PT-BR narrator text, and local audiobook audio without third-party OCR or external LLMs.
---

# Audiobook Codex

Use this skill as a three-stage pipeline. Keep the source artifact, the narrator artifact, and generated audio traceable to the same `book-map.json`.

Read [artifact-contract.md](references/artifact-contract.md) before any stage. Read [swarm-protocol.md](references/swarm-protocol.md) when delegating. Read [narrator-policy.md](references/narrator-policy.md) before creating narrator text.

## Non-Negotiable Rules

- Use no third-party OCR service, external LLM, browser chat UI, or hosted TTS.
- Use the PDF plugin and rendered images as the primary visual source. Use Computer Use only when a desktop viewer is necessary for zoom, navigation, or screenshots.
- Treat PDF text extraction as evidence, never as an authoritative transcription.
- Never alter `text/source`. Create all speech-oriented changes only in `text/locutor`.
- Do not claim source fidelity until every mapped unit has a verified text-ledger record.
- Stop for review instead of guessing an uncertain word. Do not put uncertainty placeholders in final TXT files.

## Stage 1: Map

1. Use `E:\Pessoal\e-books` as the default library root. Never write book artifacts beside an attached source because Codex attachment paths can be temporary.
2. Run `scripts/preflight.py --source <attachment> --library-root "E:\Pessoal\e-books"` with the PDF or EPUB source. It creates an isolated book directory, copies the input to `source/original.pdf` or `source/original.epub`, and produces rendered page assets plus a `book-map.json` draft.
3. Inspect source pages or EPUB units. Establish layout, rotation, logical page order, printed-page alignment, TOC, content range, chapter starts, blanks, and exclusions.
4. Complete the map with evidence for each decision. Use segmented page offsets when preliminary pages and main matter do not share one offset.
5. Run `scripts/validate_book_map.py --require-ready --check-files`.

Do not begin transcription until the map is valid and its `analysis.status` is `ready` or `approved`.

## Stage 2: Faithful Text

1. Transcribe from rendered source pages or EPUB source into per-unit files under `text/source/pages`.
2. Preserve wording, order, spelling, punctuation, and visible language. Do not correct, modernize, translate, expand numbers, or normalize prose.
3. Record each unit in `metadata/text-ledger.json`, including its file hash and independent verification state.
4. Concatenate only verified source units into chapter and book TXT outputs without synthetic page markers.
5. Run `scripts/verify_text_ledger.py --book-map ... --ledger ... --text-root ...`.

For a source already in Portuguese, faithful text is PT-BR only to the extent the source itself is PT-BR. For another source language, a literal artifact and a PT-BR adaptation cannot be the same file.

## Stage 3: Narrator and Audio

1. Derive `text/locutor` from verified `text/source`.
2. Write `metadata/narrator-changes.json` for every allowed narrator transformation.
3. Validate that every narrator segment references a verified source segment.
4. Run `scripts/render_kokoro.py` through the Python environment that contains Kokoro. It writes segment audio, a final WAV or compressed file, and an audio manifest.
5. Keep source hashes, narrator hashes, voice, speed, segment hashes, and durations in the manifest. Re-render only segments whose narrator hash changed.

## Tool Choice

- PDF with usable text layer: extract local text for comparison, then visually verify it.
- Image-only or mixed PDF: render pages and have Codex read the images directly.
- EPUB: inspect the EPUB spine/XHTML source directly; do not OCR rendered EPUB pages.
- Computer Use: one agent only, serially, when native rendering cannot resolve a visual ambiguity.
- When installed, use `audiobook-structure`, `audiobook-transcriber`, and `audiobook-verifier` for the matching swarm roles. Otherwise use equivalent bounded roles with the same write boundaries.

## Validation

Run the smallest relevant check after each stage. Before declaring completion, run:

```powershell
python scripts/validate_book_map.py --book-map <book-map.json> --require-ready --check-files
python scripts/verify_text_ledger.py --book-map <book-map.json> --ledger <text-ledger.json> --text-root <text-root>
python scripts/render_kokoro.py --input-file <locutor.txt> --output-dir <audio-dir> --voice pm_alex --format m4a
```

Use `--mock` only for validation. Never publish mock audio.
