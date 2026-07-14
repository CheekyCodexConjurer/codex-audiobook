# Chatterbox Multilingual V3 PT-BR

The active local adapter is `chatterbox-multilingual-v3-pt-br`, rendered by
`plugins/audiobook-codex/scripts/render_chatterbox.py`.

## Text Policy

- One complete spoken locution per non-empty line.
- At most 320 characters per line.
- Expand numbers, dates, times, currency, abbreviations, URLs, and email addresses
  into approved PT-BR speech before rendering.
- Do not use bracketed directions, SSML, HTML, Markdown controls, or raw URLs.
- Preserve punctuation that expresses ordinary reading cadence, then listen to the
  output; punctuation is not a substitute for unsupported control tags.

## Reproducibility

The renderer fingerprints the voice reference, local model checkpoint hashes,
`chatterbox-tts` version, device, text policy, sampling parameters, seed, and final
audio hashes. A production profile must be selected only when every required fingerprint
matches its promotion record.

For parameter sweeps, reproduce the production initialization path for finalists.
Changing an RNG reset or model-load order can change a stochastic render even when the
visible sampling parameters are unchanged.

## Current Official Profile

`feminina-v1` is frozen for the bundled `Feminina.mp3` reference. Its calibration
report and hashes are in `docs/voice-calibration/feminina-v1.md`. A future profile must
not replace it without a fresh three-prompt decision and a renderer change reviewed
separately from calibration.
