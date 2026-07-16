# Codex Audiobook

This repository owns the native-only audiobook workflow.

- Use `$audiobook-codex` for PDF and EPUB mapping, asset inventory, faithful transcription, optional whole-book PT-BR translation, semantic EPUB export, narrator text, and local Chatterbox PT-BR rendering.
- Use `$voice-calibration` before adding or replacing a local voice reference, TTS engine, or official narrator profile. Calibration artifacts belong under `E:\Pessoal\e-books\_voice-calibration-<profile-id>` and must not replace a production profile without immutable evidence, cross-prompt selection, listening review, and a reproducible smoke render.
- Store book artifacts under `E:\Pessoal\e-books`, never inside this Git repository.
- Use only Codex, native tools, the PDF plugin, optional Computer Use, and local Chatterbox. Do not use browser chat UI, external OCR, external LLMs, or hosted TTS.
- Keep `text/source` immutable. Use `text/translation/pt-BR` only for whole foreign-language books, and create speech changes only in `text/locutor`.
- Keep original source renders and extracted assets immutable. Restored images are optional reviewed derivatives and never transcription evidence.
- Use the installed `audiobook-structure`, `audiobook-transcriber`, and `audiobook-verifier` roles when delegation improves coverage.
- Validate the smallest affected stage before continuing. Run `scripts\validate.ps1` before declaring repository changes complete.

The generic `$codex-workflows` skill remains installed from `codex-workflows-prompt-pad`. The AHK bindings beginning with `NUM0` are owned here to avoid duplicate prefix-key handling.
