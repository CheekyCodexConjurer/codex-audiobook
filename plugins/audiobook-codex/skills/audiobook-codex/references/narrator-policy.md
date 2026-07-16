# Narrator Policy

Apply this policy only after source text has passed its page ledger.

## Allowed

- Expand numbers, dates, abbreviations, and symbols when necessary for natural PT-BR speech.
- Add conservative pauses and punctuation that do not change meaning.
- Expand a Roman numeral only when its heading or surrounding label proves it is a
  number. Never use a global Roman-numeral substitution: words such as `vi`, `mil`,
  and `civil` are not Roman numerals.
- Remove page furniture already marked as excluded in `book-map.json`.
- Introduce short, objective descriptions for informative figures approved in the map.
- Modernize archaic spelling or inflection only in `text/locutor`, after exact SHA-bound source-page evidence and review show the spoken form is required. Each modernization must match its assessment evidence by normalized source span, one logical page, and that page's SHA-256. Publication date alone is never sufficient.
- Correct an unambiguous typographical or grammatical source error only in `text/locutor` after the exact source span, logical page, source hash, correction rationale, and reviewer are recorded as an `editorial_correction`. Never alter `text/source` or silently present that correction as source fidelity.
- Move a source footnote out of the enclosing spoken sentence only as a reviewed `note_relocation`; retain the full note content, identify its spoken start and end, and keep the source marker available to the EPUB as a semantic noteref.
- Translate a whole non-Portuguese source only through `text/translation/pt-BR` and its approved translation ledger; the locutor then derives from that translation.
- Use a reviewed spoken form for a pronunciation-sensitive acronym, name, foreign term,
  technical term, or religious term only when the book review records the decision.

## Faithful Natural Profile

Every new render uses `faithful-natural-v1` after its selected base edition is already
valid. This profile improves cadence and intelligibility without becoming a rewrite.

1. Review every locution in context and classify it as a heading, prose, dialogue,
   quotation, verse, note, list, or approved exclusion. Record that category on every
   remaining narrator-review finding. Draft suggestions are not decisions; the reviewer
   must choose the category explicitly.
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
- Modernize archaic wording based only on publication date, genre, or assumption.
- Repair an uncertain word by guessing.
- Merge unrelated fragments across a page boundary.
- Delete meaningful source text without a map-backed exclusion.
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
Use `editorial_correction` for a proven correction of source wording in the locutor and
`note_relocation` when a note moves to an intelligible spoken boundary.

Write `metadata/narrator-review.json` for the selected full-book or chapter output. It
must bind the final locutor hash, record every remaining reviewed finding, capture the
pronunciation review, and finish with no unresolved entries. Its `review_scope.logical_pages`
must exactly cover the selected narrator output's declared base-output pages; every finding
and pronunciation entry must stay inside that scope.
