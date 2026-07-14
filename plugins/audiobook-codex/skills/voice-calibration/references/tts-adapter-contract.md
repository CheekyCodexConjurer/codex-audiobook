# Local TTS Adapter Contract

Create an adapter when the local render engine changes. Do not reuse a Chatterbox
candidate schema for another engine unless its documented parameters and deterministic
behavior actually match.

## Adapter Manifest

Use [adapter.template.json](../assets/adapter.template.json) to record:

- engine and model identifiers;
- executable or Python environment;
- local model paths and file hashes;
- input text policy and maximum segment length;
- supported parameter schema and defaults;
- seed and determinism behavior;
- output format, sample rate, channel count, and output manifest fields;
- exact render command shape;
- failure behavior and smoke command.

## Adapter Requirements

- Use local execution only. Targets from a hosted generator may be imported manually,
  but the calibration renderer must not transmit the voice reference or text.
- Emit a machine-readable result for every candidate and prompt.
- Preserve the original text, parameters, command, output hash, duration, and runtime
  fingerprint.
- Refuse to label a profile official when any required version or asset hash differs
  from the promotion record.
- Provide one standalone render path that matches production initialization. Use that
  path for finalists and the promotion smoke test.

## Promotion Handoff

An adapter must provide the data needed to add a production profile without guessing:
the reference path/hash, engine/model fingerprint, fixed parameters, text policy,
render manifest fields, and canonical smoke input/output hashes.
