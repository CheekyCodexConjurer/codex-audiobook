# Narrator Policy

Apply this policy only after source text has passed its page ledger.

Prepare narrator text through schema `1.0` chapter claims. Each claim pins the approved
base output and hash, semantic layout/note context, output path, and no-touch scope.
The `audiobook-narrator` worker writes only its exclusive locutor chapter and
narrator-change/review shards under `metadata/work`; a different verifier accepts or
rejects the claim. The main agent alone assembles full-book locutor text and canonical
narrator metadata.

## Allowed

- Expand numbers, dates, abbreviations, and symbols when necessary for natural PT-BR speech.
- Add conservative pauses and punctuation that do not change meaning.
- Expand a Roman numeral only when its heading or surrounding label proves it is a
  number. Never use a global Roman-numeral substitution: words such as `vi`, `mil`,
  and `civil` are not Roman numerals.
- Remove page furniture already marked as excluded in `book-map.json`.
- Omit an isolated printed Arabic folio only through a reviewed
  `page_furniture_exclusion` record. Its exact base span must be the standalone
  numeric folio and its spoken span must be empty; never use this record for
  semantic numbers in prose, headings, lists, notes, or citations.
- Omit a complete chapter/front-matter narrator base output only when all of its pages
  are covered by `book-map.json` `ranges.narration_excluded`; record one reviewed
  `mapped_exclusion` with an empty spoken span.
- Omit non-content supplementary back matter from narration while preserving it in the
  selected textual edition and EPUB/PDF. This includes bibliography/references,
  glossaries, indexes, further-reading/source lists, and colophons. Prefer a separate
  map-backed base output. When such matter begins inside the final page of an otherwise
  narrated base output, omit only one exact contiguous trailing span through a reviewed
  `supplementary_matter_exclusion` with an empty spoken span and a supported
  `matter_kind`. Do not classify appendices, notes by the author, acknowledgments, or
  other potentially substantive sections as supplementary without an explicit
  book-specific decision.
- Introduce short, objective descriptions for informative figures approved in the map.
- When the selected base is the faithful Portuguese source rather than an approved
  fluid edition, modernize archaic spelling or inflection only in `text/locutor`, after
  exact SHA-bound source-page evidence and review show the spoken form is required.
  Each modernization must match its assessment evidence by normalized source span, one
  logical page, and that page's SHA-256. Publication date alone is never sufficient.
- Correct an unambiguous typographical or grammatical source error only in `text/locutor` after the exact source span, logical page, source hash, correction rationale, and reviewer are recorded as an `editorial_correction`. Never alter `text/source` or silently present that correction as source fidelity.
- Exclude every semantic footnote from narration. Keep the complete note and its semantic
  noteref in the selected textual edition and EPUB/PDF, but omit the note body and its
  attached reference marker from `text/locutor`. Record exact reviewed
  `footnote_exclusion` changes tied to the layout note `id`: use `note_part: "content"`
  with an empty `locutor_span` for the note body, and a separate
  `note_part: "marker"` record for an attached marker when removing it requires a
  contextual replacement.
- Translate a whole non-Portuguese source only through `text/translation/pt-BR` and its approved translation ledger; the locutor then derives from that translation.
- When the selected reading base is `fluid-pt-br`, derive the locutor only from the
  approved `text/fluid/pt-BR` files and `fluid-edition-ledger.json`. Do not silently
  repeat fluid editorial changes as narrator changes.
- Use a reviewed spoken form for a pronunciation-sensitive acronym, name, foreign term,
  technical term, or religious term only when the book review records the decision.

## Faithful Natural Profile

Every new render uses `faithful-natural-v1` after its selected base edition is already
valid. This profile improves cadence and intelligibility without becoming a rewrite.

1. Review every locution in context and classify it as a heading, prose, dialogue,
   quotation, verse, note, list, or approved exclusion. Record that category on every
   remaining narrator-review finding. Draft suggestions are not decisions; the reviewer
   must choose the category explicitly.
   A semantic footnote is always an approved narration exclusion, not a spoken note.
   Reviewed non-content back matter is also an approved narration exclusion, not prose.
2. Keep one complete spoken locution per line. Split only at a semantic boundary and
   never divide a dialogue turn, name, number, quotation, or verse line mechanically.
3. Convert unambiguous numbered headings into spoken PT-BR, for example
   `XXII.` to `Capítulo vinte e dois.`.
4. Repair duplicated punctuation, spacing artifacts, and extraction noise only after
   the source page confirms the intended reading. Preserve a deliberate source form
   only with an explicit review finding and rationale.
5. Keep dialogue attribution with the relevant turn when it changes how the sentence
   is read. Do not add speaker names, emotion labels, stage directions, or unsupported
   audio controls.
6. Review pronunciation-sensitive terms per book. Resolve only the terms with an
   evidence-backed spoken form; record an intentional preservation when the source form
   is retained. A multiword all-caps heading remains a review finding, but its ordinary
   words are not presumed acronyms.

## Realism Contract

The narrator is an editor of spoken realization, not a new author. It may act
autonomously only when the intended spoken form and semantic boundary are
unambiguous in complete sentence context. Normalize the spoken form before deciding
the render line; the 320-character limit is a renderer ceiling, never a target unit
size.

The narrator may autonomously expand an unambiguous written form, apply a reviewed
pronunciation entry, preserve a dialogue turn or attribution, and split at an existing
semantic or syntactic boundary. It must keep a number with its unit, a name with its
surname, a quotation with its completion, and a connective with the proposition it
relates.

The narrator must leave a review finding rather than guess when a spelling, acronym,
date, number, name, foreign term, dialogue speaker, rhetorical emphasis, or punctuation
change has more than one plausible reading. Pronunciation entries are occurrence-scoped:
record the source term, spoken form, locutor span, logical pages, reason, and reviewer.

Punctuation expresses written syntax; it is not a pause-control language. Preserve
meaningful punctuation and correct only an objectively defective or missing syntactic
boundary. Never add commas, full stops, dashes, or ellipses merely to slow delivery,
manufacture emotion, or imitate spontaneous conversation. Do not add fillers,
hesitations, self-corrections, emotion labels, character voices, or stage directions.
Lists retain their order, grouping, and final conjunction; a difficult list, quotation,
or long documentary sentence requires listening review of the rendered result.

## Chatterbox PT-BR Input

- Use UTF-8 NFC and one complete spoken locution per non-empty line.
- Keep each line at or below 320 characters after expansion. Split only at an approved
  syntactic boundary; never split a word, number, name, or dialogue turn mechanically.
- Write numbers, dates, times, currency, percentages, units, abbreviations, URLs, and
  email addresses in an approved spoken PT-BR form before rendering.
- Use sentence punctuation deliberately. Do not rely on ellipses, colons, semicolons,
  or dashes as pause controls because Chatterbox normalizes them internally.
- Do not pass SSML, Markdown controls, or bracketed audio tags. For meaningful literal
  brackets or codes, write the intended speech explicitly.

## Forbidden

- Change source wording silently.
- Summarize, censor, interpret, moralize, explain, or invent content.
- Translate a Portuguese source because it contains intentional isolated English or other foreign words.
- Translate a whole foreign-language book directly inside `text/locutor` without a reviewed translation ledger.
- Create or repair a fluid reading edition inside `text/locutor`; fluid edits belong
  under `text/fluid/pt-BR` and require their own ledger.
- Modernize archaic wording based only on publication date, genre, or assumption.
- Repair an uncertain word by guessing.
- Merge unrelated fragments across a page boundary.
- Delete meaningful source text without a map-backed exclusion, semantic
  `footnote_exclusion`, or reviewed trailing `supplementary_matter_exclusion`.
- Narrate a semantic footnote body or its attached reference marker. `note_relocation`
  is legacy-only and must not be created for new audiobook editions.
- Narrate bibliography/references, glossaries, indexes, further-reading/source lists,
  or colophons after they have been classified as non-content supplementary back matter.
- Use `supplementary_matter_exclusion` for an interior passage, an appendix, or any
  non-trailing span. It is restricted to contiguous back matter at the end of one base
  output; complete excluded outputs use `mapped_exclusion`.
- Reuse EPUB alt text or a generated restoration prompt as narrator content. EPUB accessibility annotations and image derivatives are separate artifacts.
- Leave an unreviewed digit, common abbreviation, URL, email address, or bracketed
  instruction for Chatterbox to interpret.
- Invent a character voice, emotional cue, dramatic instruction, or other performance
  direction inside narrator text.

## Required Change Records

Record a change when it expands a number, uses a pronunciation form, modernizes an
archaic spoken form, describes a figure, removes approved page furniture, or changes
punctuation beyond a trivial whitespace fix. Keep each record tied to the base edition,
base ledger, source pages, output files, hashes, exact evidence, and review state.
When a locutor chapter filename differs from its selected base chapter, declare its
exact `locutor_file` in that `base_outputs` entry instead of relying on filename
inference.
For a complete map-backed base-output omission, cite all of the omitted output's pages,
use a unique identifying base span, and set `locutor_span` to the empty string.
Use `editorial_correction` for a proven correction of source wording in the locutor and
`footnote_exclusion` for each omitted note body or attached marker span. Every
`footnote_exclusion` must carry the semantic `note_id`, set `note_part` to `content` or
`marker`, and use an empty `locutor_span` whenever the exact base span disappears
entirely. Record an isolated printed numeric folio with
`page_furniture_exclusion` and an empty `locutor_span`; it is not a substitute for
removing meaningful text. `note_relocation` remains accepted only for already approved
legacy metadata.
For partial-output back matter, use `supplementary_matter_exclusion`, an empty
`locutor_span`, the exact unique trailing `base_span`, its logical pages, and one of:
`bibliography`, `references`, `glossary`, `index`, `further_reading`, `source_list`,
`colophon`, or `mixed_back_matter`.

Write `metadata/narrator-review.json` for the selected full-book or chapter output. It
must bind the final locutor hash, record every remaining reviewed finding, capture the
pronunciation review, and finish with no unresolved entries. Its `review_scope.logical_pages`
must exactly cover the selected narrator output's declared base-output pages; every finding
and pronunciation entry must stay inside that scope.
