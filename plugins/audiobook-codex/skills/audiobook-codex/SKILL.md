---
name: audiobook-codex
description: Build a local audiobook and semantic EPUB from a PDF or EPUB using only Codex, native tools, Codex agents, the PDF plugin, optional Computer Use, native image editing, and the local Chatterbox PT-BR runtime. Use for book mapping, asset inventory, faithful source transcription, PT-BR narrator text, EPUB export, optional reviewed image restoration, and local audiobook audio without third-party OCR or external LLMs.
---

# Audiobook Codex

Use this skill as a source-faithful pipeline with one optional translation stage. Keep the source artifact, visual assets, translation artifact, narrator artifact, EPUB exports, and generated audio traceable to the same source hash and `book-map.json`.

Read [artifact-contract.md](references/artifact-contract.md) before any stage. Read [swarm-protocol.md](references/swarm-protocol.md) when delegating. Read [narrator-policy.md](references/narrator-policy.md) before creating narrator text.

Before adding or replacing a local voice reference, TTS engine, or official narrator
profile, use `$voice-calibration`. Do not calibrate against hosted TTS from this
pipeline and do not alter `feminina-v1` without its separate promotion evidence.

## Non-Negotiable Rules

- Use no third-party OCR service, external LLM, browser chat UI, or hosted TTS.
- Use the PDF plugin and rendered images as the primary visual source. Use Computer Use only when a desktop viewer is necessary for zoom, navigation, or screenshots.
- Treat PDF text extraction as evidence, never as an authoritative transcription.
- Never alter `text/source`. Create all speech-oriented changes only in `text/locutor`.
- Use `text/translation/pt-BR` only for a whole source book written in another language. A Portuguese book with intentional isolated English or other foreign words is not a translation case.
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
6. Create `metadata/epub-layout.json` for original-text EPUBs. It must cover every non-empty verified `text/source/pages` line exactly once, preserve the validated chapter-output order, and classify each block as a paragraph, dialogue, verse, or heading without altering source text.
7. Run `scripts/validate_epub_layout.py --book-root <book-root>` and then `scripts/build_epub_manifest.py --layout semantic --visual-profile antique-paper`. Review the generated reading order and image placement before publication. Do not invent captions or alt text from uncertain pixels.

For a source already in Portuguese, faithful text is PT-BR only to the extent the source itself is PT-BR. For another source language, a literal artifact and a PT-BR adaptation cannot be the same file.

## Optional Stage: Translate

Invoke directly when, and only when, the whole source book is in another language:

```text
$audiobook-codex stage=TRANSLATE native-only input{text/source|text-ledger.json|epub-manifest.json} output{text/translation/pt-BR|translation-ledger.json|translated-epub} scope{whole-foreign-language-book-only} language=pt-BR epub-images{original|approved-restored}
```

1. Start only after `text/source`, `metadata/text-ledger.json`, and the source EPUB manifest pass validation. Set `analysis.source_language` from the predominant language of the whole verified work, never from an isolated quotation or word; record page-backed source spans for every verified page in the translation decision.
2. Create PT-BR text under `text/translation/pt-BR`. Do not overwrite `text/source` or treat translation as a source correction.
3. Write `metadata/translation-ledger.json` with the base source ledger, target output files, hashes, per-unit source references, and reviewer state.
4. Export a separate translated EPUB with semantic PT-BR text and metadata. Preserve source image pixels; approved restored images remain optional derivatives and never become translation evidence.
5. Do not translate isolated foreign words, titles, citations, or expressions inside a Portuguese source merely because they are not Portuguese. In a whole foreign-language book, preserve proper names, trademarks, code, and intentionally original quoted forms only when the translation review records why.

## Stage 3: Narrator, Audio, and EPUB

1. Derive `text/locutor` from verified `text/source`, or from `text/translation/pt-BR` only when the optional translation stage has produced a complete approved ledger.
2. Apply `faithful-natural-v1`: classify every locution in context, record each finding's category, make only evidence-backed speech changes, and review headings, dialogue turns, quotations, verse, punctuation, numbers, abbreviations, and pronunciation-sensitive terms.
3. Run `scripts/narrator_quality.py` to create a review draft, resolve or explicitly preserve every finding, and approve `metadata/narrator-review.json`. This profile is mandatory for every new `stage=RENDER`; existing artifacts are reprocessed only when explicitly requested.
4. Write `metadata/narrator-changes.json` v2 for every allowed narrator transformation, including the base edition, base ledger, output file hashes, and granular change records. Use `archaic-modernized` only after exact textual evidence confirms it is needed; leave ordinary Portuguese faithful.
5. Build `metadata/narration-plan.json` with `scripts/narration_plan.py --refresh-approved-metadata`. It reflows only `text/locutor/book.txt` into complete semantic segments, preserves the approved normalized wording, records source chapter/paragraph/page provenance, and assigns continuation, sentence, paragraph, or heading pauses.
6. Run `scripts/validate_narrator_lineage.py`, `scripts/validate_narrator_quality.py`, and `scripts/validate_narration_plan.py` for the selected locutor file. A full Portuguese source with isolated English, Latin, or other intentional words remains `faithful`; it is not a translation case.
7. Run `scripts/render_chatterbox.py --require-lineage --require-quality` through the dedicated Chatterbox PT-BR environment. Book-root renders enforce all three gates even if those flags are omitted. As each source chapter reaches its final verified segment, write its immutable 1.0x matrix to `audio/<profile>/chapters/original/<chapter-id>.wav` and the canonical delivery files to `audio/<profile>/chapters/final/<chapter-id>.wav` and `.mp3`, plus `metadata/audio-chapters-manifest.json`. Keep comparison renders under `audio/<profile>/chapters/temp/`. The default delivery cadence is `1.20x`; override it only with `--publication-tempo <multiplier>`. Use `--chapters <chapter-id>` to render or reassemble only a reviewed chapter; it must not replace the full-book audio manifest.
8. Keep base text hashes, narrator hashes, voice, segment hashes, master and delivery durations, and publication tempo in the manifest. The atomic render journal resumes a segment at the same plan position only with its matching seed and verified WAV checksum. After a plan reflow, it may move only one unique, canonical, validated speech payload with matching text, model, voice, renderer, and settings; it retains the historical render seed and records `reused_from` provenance. Ambiguous, silent, corrupt, or changed text re-renders. Publication tempo is deliberately outside this identity. Use `--remount --publication-tempo <multiplier>` to rebuild chapters and the unified delivery audio from verified segments without regenerating speech. A pause-only plan change remounts the final audio without regenerating speech; changed text boundaries re-render only affected segments.
8. The default `antique-paper` EPUB profile embeds IM FELL English with its OFL license, uses black text on a white reading surface, and generates a local editorial cover from verified title, subtitle, author, place, and year. It never replaces the source cover/title page.
9. Run `scripts/export_epub.py` with `--image-edition original` to create the canonical semantic EPUB from `text/source`, verified manifests, and extracted original assets.
10. Optional restoration is review-gated. Use the native `image_gen` tool only for an asset classified as non-text and review-eligible. A full scan with text requires an explicit `manual_exception` and exception reason. Save candidates under `restoration/candidates/`, record provenance, compare visually against the original, and promote only approved assets under `restoration/approved/`.
11. Run `scripts/export_epub.py --image-edition approved-restored` only after every selected derivative has a complete approval record. This produces a separate restored edition; it never replaces the canonical EPUB.
12. Run `scripts/publish_artifacts.py --book-root <book-root> --audio <final-audio> --epub <final-epub>` after validation. It copies only the unified audiobook and chosen EPUB into the root of the book folder, while preserving the provenance outputs under `audio/` and `exports/epub/`.

## Tool Choice

- PDF with usable text layer: extract local text for comparison, then visually verify it.
- Image-only or mixed PDF: render pages and have Codex read the images directly.
- EPUB: inspect the EPUB spine/XHTML source directly; do not OCR rendered EPUB pages.
- Translation: invoke `stage=TRANSLATE` directly for whole foreign-language books only. Keep the translated text and EPUB separate from source-faithful outputs.
- Computer Use: one agent only, serially, when native rendering cannot resolve a visual ambiguity.
- Native image restoration: use the built-in `image_gen` tool, not an API-key script or browser chat. Load a local edit target into the task first, restate every invariant in the prompt, and require a human approval record before export.
- Printed text and handwriting: use only original assets or deterministic local cleanup. Do not use generated output as a source for transcription or semantic EPUB text.
- Chatterbox PT-BR: use `E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe`, the local V3 PT-BR model files, and the bundled `assets/voices/Feminina.mp3` reference. The official render profile is `feminina-v1`; use line-delimited narrator text of at most 320 characters per non-empty line plus the required `paragraph-pauses-v1` narration plan. Do not send the voice reference or narrator text to a hosted TTS service.
- When installed, use `audiobook-structure`, `audiobook-transcriber`, and `audiobook-verifier` for the matching swarm roles. Otherwise use equivalent bounded roles with the same write boundaries.

## Validation

Run the smallest relevant check after each stage. Before declaring completion, run:

```powershell
python scripts/validate_book_map.py --book-map <book-map.json> --require-ready --check-files
python scripts/validate_assets_manifest.py --assets-manifest <assets-manifest.json> --book-root <book-root>
python scripts/verify_text_ledger.py --book-map <book-map.json> --ledger <text-ledger.json> --text-root <text-root>
python scripts/validate_epub_layout.py --book-root <book-root>
python scripts/build_epub_manifest.py --book-map <book-map.json> --ledger <text-ledger.json> --assets-manifest <assets-manifest.json> --text-root <text-root> --layout semantic --visual-profile antique-paper
python scripts/export_epub.py --book-root <book-root> --image-edition original
python scripts/validate_epub_export.py --book-root <book-root> --epub <output.epub> --image-edition original
python scripts/verify_translation_ledger.py --book-map <book-map.json> --ledger <text-ledger.json> --translation-ledger <translation-ledger.json> --text-root <text-root>
python scripts/build_epub_manifest.py --book-map <book-map.json> --ledger <text-ledger.json> --assets-manifest <assets-manifest.json> --text-root <text-root> --text-edition translated-pt-br --visual-profile antique-paper
python scripts/export_epub.py --book-root <book-root> --text-edition translated-pt-br --image-edition original
python scripts/validate_epub_export.py --book-root <book-root> --epub <output.epub> --text-edition translated-pt-br --image-edition original
python scripts/validate_narrator_lineage.py --book-root <book-root> --input-file <locutor.txt>
python scripts/narrator_quality.py --book-root <book-root> --input-file <locutor.txt> --output <book-root>\metadata\narrator-review.json [--narrator-changes <changes.json>]
python scripts/validate_narrator_quality.py --book-root <book-root> --input-file <locutor.txt>
python scripts/narration_plan.py --book-root <book-root> --refresh-approved-metadata
python scripts/validate_narration_plan.py --book-root <book-root> --input-file <locutor.txt>
E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe scripts/render_chatterbox.py --book-root <book-root> --input-file <locutor.txt> --output-dir <audio-dir> --format mp3 --require-lineage --require-quality
python scripts/publish_artifacts.py --book-root <book-root> --audio <audio-dir>\audiobook.mp3 --epub <output.epub>
```

Use `--mock` only for validation. Never publish mock audio.
