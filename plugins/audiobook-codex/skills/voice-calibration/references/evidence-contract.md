# Evidence Contract

## Workspace Layout

```text
E:\Pessoal\Library\_voice-calibration-<profile-id>\
|- validation-corpus\
|  |- 01-narracao.txt
|  |- 02-dialogo.txt
|  |- 03-semiotica.txt
|  `- corpus.json
|- references\original\
|- renders\
|- selection\
`- logs\
```

`corpus.json` is the source of truth for the text hashes and imported local audio
copies. A draft corpus has only text hashes. A ready corpus has a voice reference and
one target audio per prompt, all with SHA-256 hashes.

Historical promotion manifests and dated reports are an explicit immutable-provenance allowlist for old `E:\Pessoal\e-books` paths. New calibration workspaces default to `E:\Pessoal\Library`; do not migrate or edit historical paths merely to match the current default.

## Required Evidence

For every decision retain:

- corpus JSON and its SHA-256;
- each prompt text and target audio SHA-256;
- voice-reference SHA-256;
- candidate specification and SHA-256;
- adapter command, version, model identifiers/hashes, device, and seed behavior;
- render manifest containing output SHA-256, duration, format, and effective
  parameters;
- per-prompt scores and aggregate selection JSON;
- listening-review notes;
- DSP comparison, when DSP was considered;
- promotion decision and production smoke hash.

## Repository Validation Modes

- `provenance` verifies that an approved historical promotion remains internally
  consistent with its report, bundled reference voice, declared SHA-256 values, and
  current renderer profile constants. It accepts an absent historical workspace but
  never treats absent bytes as re-verified evidence.
- `full` reopens every declared external path and verifies the retained corpus,
  selection, listening review, reproduction inputs, manifests, and audio bytes.
- Routine repository checks use `provenance`. Any official profile addition,
  replacement, or re-promotion requires `scripts\validate.ps1 -FullVoiceEvidence`;
  missing retained evidence blocks that decision until it is restored or superseded by
  a new complete calibration.

## Decision Boundaries

- The output score is a proxy for closeness to a particular target recording. It is
  not an objective quality score and is not comparable across unrelated corpora.
- A candidate that wins one prompt but loses badly on another is not promoted.
- A target change creates a new calibration decision. Do not silently overwrite the
  imported audio and reuse old scores.
- A renderer, model, package, device, or seed behavior change invalidates a
  reproducibility claim until the smoke render is repeated.
