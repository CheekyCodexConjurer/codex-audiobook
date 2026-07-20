# Fluid PT-BR Edition Policy

`fluid-faithful-ptbr-v1` creates one optional modern, fluent PT-BR reading edition
without replacing the verified source or approved translation.

## Automatic Base Selection

Use the approved `translated-pt-br` edition when it exists. Otherwise use `source`
only when the verified source language is Portuguese. The selected base and its ledger
hash are frozen for the whole fluid edition.

## Fixed Whole-Book Voice

Create `metadata/fluid-style.json` before rewriting. Keep one profile, register, tone,
cadence, terminology policy, title policy, and reviewed glossary for every chapter.
Editing intensity may vary by paragraph; voice and terminology may not.

Freeze the selected base, style, glossary revision, complete chapter hash, and
neighboring-context hashes in one schema `1.0` claim per chapter. Editors write only
exclusive fluid chapter files and one shard under `metadata/work/fluid-ledger.d`; they
never update the canonical style or ledger directly.

## Allowed Editing

- Modernize every genuinely archaic surface form into natural contemporary PT-BR,
  including orthography, diacritics, inflection, contractions, pronouns, syntax, and
  obsolete vocabulary.
- Remove expendable repetition without removing an argument, emphasis, qualification,
  example, warning, contrast, or rhetorical effect.
- Untangle prolix syntax and improve sentence order.
- Clarify pronoun or referent relationships only when the base meaning is unambiguous.
- Improve punctuation and paragraph rhythm for contemporary silent reading.

## Mandatory Comprehensive Modernization

Apply archaic modernization to every readable textual layer: authorial prose, dialogue,
historical quotations, documentary excerpts, letters, epigraphs, captions, and
footnotes. Quotation marks, attribution, meaning, historical bias, authorial stance,
and evidentiary function remain intact; archaic spelling or grammar does not remain
literal merely because the passage is quoted.

Modernize form rather than viewpoint. Do not sanitize a historical judgment, euphemize
a charged term, replace a proper name, or erase intentional characterization under the
pretext of modernization. A technical term, proper name, intentionally foreign
fragment, or semantically material historical label may remain only through a specific
reviewed glossary decision—not through a blanket rule preserving old quotations.

Translate every complete foreign-language quotation, sentence sequence, or paragraph into
PT-BR in the fluid edition. Retain only isolated foreign words, titles, proper names,
trademarks, code, or a reviewed material expression; a citation's foreign-language status
is never by itself a reason to leave a complete paragraph untranslated. Record this as
`foreign_quotation_translation` in the fluid ledger.

New editions use `fluid-style.json` schema `1.2` and must set
`modernize_all_archaic_language`, `modernize_historical_quotations`, and
`modernize_orthography_and_diacritics` to `true`. Schemas `1.0` and `1.1` are legacy
compatibility for already approved editions and must not be used to create a new one.

## Required Editorial Exclusions

The fluid edition is a listener-oriented reading edition, not an academic apparatus
edition. Apply these exclusions throughout the book:

- Remove inline parenthetical bibliographic references such as
  `(PARÉS, 2011, p. 125)` or `(EDUARDO, 1948, p. 102)`. Preserve the quoted or
  paraphrased content and any author name that is part of the sentence; remove only the
  citation apparatus and repair the surrounding punctuation.
- When a complete base block contains only bibliographic citation apparatus and no
  semantic prose, omit that block from the fluid edition and record it as an excluded
  `citation_reference_exclusion` with null fluid position and hash.
- When one complete paragraph is explicitly a translation of the immediately preceding
  paragraph, preserve the preceding original-language paragraph and omit the translated
  duplicate from the fluid edition.
- Omit standalone or attached labels such as `Tradução livre`, `(Tradução livre)` or
  equivalent wording that identifies the excluded duplicate translation.
- Do not publish terminal references, bibliography, glossary, index, source list, colophon,
  or comparable post-book apparatus in a fluid PDF. The faithful textual artifacts remain
  intact; the fluid PDF exporter stops at the first such semantic heading and skips a
  supplementary terminal document.

## Figure Captions

When the source repeats a figure number, correct the fluid caption sequence without
changing the caption's meaning. Number standalone figure captions monotonically in reading
order (`Imagem 1`, `Imagem 2`, ...), record the repair as
`figure_caption_numbering` in the fluid ledger, and preserve the source artifact unchanged.

Do not treat an ordinary paraphrase, commentary, continuation, or independently useful
PT-BR explanation as a duplicate translation. When equivalence is uncertain, block
approval instead of deleting the paragraph.

Schema `1.2` must set `omit_parenthetical_citation_references`,
`omit_immediate_duplicate_translations`, and `omit_translation_labels` to `true`.

## Prohibited Editing

- No new facts, explanations, examples, conclusions, transitions, or interpretations.
- No summaries, censorship, simplification of technical distinctions, or free adaptation.
- No omitted claim, caveat, implication, cited passage, proper name, intentional
  ambiguity, authorial stance, or structural label except the required bibliographic
  apparatus, duplicate-translation, and translation-label exclusions above.
- No speech-only expansion or pronunciation notation; those remain under `text/locutor`.
- No changes to `text/source` or `text/translation/pt-BR`.
- No blanket instruction to preserve historical quotations, documentary excerpts, or
  footnotes in archaic surface form.

## Block and Review Contract

Each base chapter is split on blank lines. The schema `1.2` ledger must cover every base
block exactly once and in order. Mark a block as `included` with its sequential
`fluid_position` and fluid hash, or as `excluded` with a null fluid position/hash and
an approved `citation_reference_exclusion`, `duplicate_translation_exclusion`, or
`translation_label_exclusion`. A full-block `citation_reference_exclusion` is allowed
only when the entire base block is citation apparatus. Included blocks must cover every
actual fluid chapter block exactly once and in order. Inline bibliographic removal
stays inside an included block and is also recorded as
`citation_reference_exclusion`.

Legacy schemas `1.0` and `1.1` retain their exact one-base-block-to-one-fluid-block
contract.

If an approved base boundary is proven to be only a page-break continuation inside the
same paragraph, preserve both ledger blocks and mark the later fluid EPUB-layout
paragraph with `join_with_previous: true`. The EPUB and PDF exporters then render the
fragments as one semantic paragraph. Contiguous semantic `note` blocks may remain
between the fragments in raw layout order; presentation completes the paragraph first
and then renders those notes in their original relative order. No other block kind may
be crossed. Never use this flag to collapse real paragraph, heading, dialogue, verse,
list, quotation, or diagram boundaries.

Approval requires all five gates:

1. semantic fidelity;
2. no unsupported additions;
3. no unsupported omissions;
4. fluent contemporary PT-BR, with no unreviewed archaic surface forms anywhere in the
   readable edition;
5. whole-book voice and terminology consistency.

Record the editor in `edited_by`. The final `reviewed_by` must identify a different
independent verifier and match `review.reviewed_by`.

Verify each produced chapter claim independently for local semantic fidelity,
additions, omissions, modernization, editorial exclusions, fluency, and frozen-style
compliance. Accepted shards merge deterministically in claim order. Claim approval does
not grant whole-book approval: voice, terminology, ordered coverage, canonical
`book.txt`, and chapter-to-chapter consistency remain global gates after fan-in.

For schemas `1.1` and `1.2`, `review.archaic_modernization` must also be `approved`. The reviewer
must inspect quotations and documentary passages explicitly instead of assuming that
quotation status justifies literal archaic spelling.

For schema `1.2`, `review.editorial_exclusions` must also be `approved`. The reviewer
must confirm that every removed reference is bibliographic apparatus, every excluded
paragraph is a true translation of the immediately preceding paragraph, and every
translation label belongs to an excluded duplicate.

The independently reviewed edition is stored under `text/fluid/pt-BR`, exported as
`fluid-pt-br`, and published under its separate export filenames in the public book
root beside—not over—the faithful original or translated EPUB/PDF.
