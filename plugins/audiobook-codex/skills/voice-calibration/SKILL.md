---
name: voice-calibration
description: Calibrate, compare, and promote a local audiobook TTS voice profile from immutable reference audio and a three-prompt PT-BR corpus. Use when adding or replacing a voice reference, changing a local TTS engine or model, tuning a production narrator profile, importing manually generated target audio, or deciding whether a candidate may replace an official voice profile.
---

# Voice Calibration

Use this skill before changing an official voice profile or local TTS renderer.
Keep calibration artifacts outside Git under `E:\Pessoal\e-books\_voice-calibration-<profile-id>`.

Read [protocol.md](references/protocol.md) before any calibration. Read
[evidence-contract.md](references/evidence-contract.md) before importing targets,
ranking candidates, or promoting a profile. For a new engine, read
[tts-adapter-contract.md](references/tts-adapter-contract.md). For the current
engine, read [chatterbox-v3-pt-br.md](references/chatterbox-v3-pt-br.md).

## Rules

- Keep every target audio, voice reference, rendered candidate, and decision manifest
  hashable and immutable after import.
- Use the same three prompts for every candidate in a comparison. Do not rank a
  candidate from only the primary narration prompt.
- Treat metric scores as rankings within one corpus and target set, not universal
  quality scores across voices or engines.
- Require a listening review for pronunciation, text fidelity, artifacts, and
  perceived naturalness. Metrics cannot replace it.
- Do not call hosted TTS APIs from this workflow. Import targets manually after they
  are generated or downloaded elsewhere. Never place credentials in the workspace.
- Do not replace an official profile until the candidate wins the full corpus,
  passes listening review, has a reproducible smoke render, and records its
  implementation handoff.
- Keep source audiobook text separate from calibration text. Calibration text is a
  controlled test fixture, never book transcription evidence.

## Workflow

1. Initialize a dedicated workspace:

   ```powershell
   python scripts/init_calibration_workspace.py --profile-id <profile-id>
   ```

2. Generate or download one target audio for each TXT in `validation-corpus/`.
   The text must be identical; do not add bracketed direction, SSML, or unrecorded
   editorial changes.

3. Copy the voice reference and the three targets into the workspace:

   ```powershell
   python scripts/import_calibration_targets.py `
     --workspace-root <workspace> `
     --voice-reference <reference-audio> `
     --target 01-narracao=<target-audio> `
     --target 02-dialogo=<target-audio> `
     --target 03-semiotica=<target-audio>
   ```

4. Validate the immutable corpus:

   ```powershell
   python scripts/validate_calibration_workspace.py `
     --workspace-root <workspace> --require-ready --check-files
   ```

5. Establish a baseline, then run a bounded parameter sweep through the selected
   local-engine adapter. Persist renderer version, model hashes, parameters, seed,
   command, output hashes, and scores for every candidate.

6. Rank candidates on all three prompts. Use the robust criterion from
   [protocol.md](references/protocol.md), then perform blinded or paired listening
   review of the finalists.

7. Test post-processing only against the raw winner. Adopt DSP only when it improves
   the selected comparison and the listening review; delivery encoding alone does not
   justify a new production signal path.

8. Produce a promotion decision from
   [promotion.template.json](assets/promotion.template.json). Update the production
   renderer only in a separately reviewed implementation step.

## Current Chatterbox PT-BR

For `chatterbox-multilingual-v3-pt-br`, use complete spoken locutions on separate
non-empty lines, with at most 320 characters per line. Expand digits and common
abbreviations into PT-BR speech. Do not use brackets, SSML, Markdown, URLs, or email
addresses in narrator input.

The current official profile is `feminina-v1`. Its evidence is documented in
`docs/voice-calibration/feminina-v1.md`; do not change its parameters merely because
one new prompt scores higher.
