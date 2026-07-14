# Calibration Protocol

## Purpose

Select one reproducible local TTS profile for one voice reference and one fixed
corpus. The result is a profile decision, not a claim that an engine reproduces all
speech from another engine.

## Gates

1. **Initialize**
   - Create a new workspace with `init_calibration_workspace.py`.
   - Keep the generated three prompt texts unchanged for the whole decision.
   - Record the target engine/model and intended local render engine.

2. **Import**
   - Generate or download exactly one target audio per prompt outside the repository.
   - Import local copies with `import_calibration_targets.py`.
   - Verify `corpus.json` with `--require-ready --check-files`.
   - Do not overwrite an existing imported target. Start a new workspace for a new
     target set.

3. **Baseline**
   - Render every prompt with the adapter defaults.
   - Record the command, runtime version, model hashes, seed behavior, audio hashes,
     duration, and output sample rate.
   - Listen for missing words, mispronunciations, clipped audio, and conditioning
     failures before spending time on a sweep.

4. **Bounded sweep**
   - Change one meaningful group of parameters per round: seed, sampling,
     conditioning, expression, or pause policy.
   - Keep all non-tested inputs fixed.
   - Retain only finalists between rounds; do not expand a sweep indefinitely.
   - When standalone production rendering reinitializes model state, make finalists
     use the same initialization behavior before selection.

5. **Selection**
   - Score every finalist against every immutable target.
   - Rank by `0.7 * mean(composite) + 0.3 * min(composite)`.
   - Break ties by higher mean, then higher minimum, then lower standard deviation.
   - Never compare absolute scores from different target corpora as though they share
     a quality scale.

6. **Listening and DSP**
   - Compare the leading candidates in matched A/B or blind order.
   - Check pronunciation, wording, pauses, dialogue, numbers, artifacts, and
     perceived character.
   - Test loudness, codec, EQ, de-essing, compression, or limiting only after a raw
     winner exists. Keep raw WAV when no processed result wins.

7. **Promotion**
   - Freeze the corpus, candidate specification, adapter version, selected render,
     selection output, and review notes.
   - Produce a promotion manifest and a human-readable report.
   - Update the local renderer/profile in a distinct implementation change.
   - Run the production smoke gate using the exact promoted input and record the
     resulting hash.

## Corpus

The standard corpus intentionally covers:

- natural narration and transitions;
- dialogue and pacing;
- dates, hours, quantities, currency, and pronunciation-sensitive syntax.

Do not insert audio-control markup into the corpus. The target TTS may accept it, but
the comparison would no longer represent ordinary audiobook text and another engine
may reject it.
