# Codex Audiobook

Native-only audiobook workflow for Codex. It maps PDF or EPUB sources, inventories original
visual assets, produces faithful source text, optionally creates contextual whole-book PT-BR
translations with auditable ambiguity research, derives PT-BR narrator text, exports semantic
EPUB and paired non-facsimile PDF editions, optionally creates faithful fluid PT-BR
reading editions, and renders local Chatterbox PT-BR audio.

Open `E:\Repositories\codex-audiobook` in Codex before processing a book. Attach the PDF or EPUB
to the task, then use the AHK shortcuts with Scroll Lock enabled. The generic
`codex_prompt_pad.ahk` remains the sole owner of the Scroll Lock toggle.

`NUM0`, `NUM0+1`, `NUM0+2`, and `NUM0+3` depend on the shared global
`codex-workflows` skill installed by `codex-workflows-prompt-pad`.

## Install

```powershell
.\scripts\install.ps1 -RestartAhk
```

Register the local marketplace with a command-scoped `xhigh` compatibility override.
The installer does not rewrite the global Codex/PowerProfile configuration:

```powershell
.\scripts\install.ps1 -RegisterMarketplace
```

The marketplace source is local, so do not run `codex plugin marketplace upgrade` for
it. After a plugin cachebuster change, open a new Codex task to load the updated files.

## Library

Book artifacts are stored outside Git under the default library root
`E:\Pessoal\Library`. Each public book root is named `Nome do Livro - Ano - Autor`.
The public root contains `assembly/`, the canonical EPUB/PDF pair, any separately
named published fluid EPUB/PDF pair, and the final MP3. All working manifests and
provenance paths are assembly-relative; `--book-root` means the public root.

```text
E:\Pessoal\Library\Nome do Livro - Ano - Autor\
|- assembly\
|  |- assets\
|  |  |- images\original\
|  |  `- restoration\
|  |     |- candidates\
|  |     `- approved\
|  |- audio\
|  |- exports\
|  |  |- epub\
|  |  |  `- <book>-fiel.epub.json
|  |  `- pdf\
|  |     `- <book>-fiel.pdf.json
|  |- metadata\
|  |  |- book-map.json
|  |  |- assets-manifest.json
|  |  |- text-ledger.json
|  |  |- epub-layout.json
|  |  |- translation-ledger.json
|  |  |- fluid-style.json
|  |  |- fluid-edition-ledger.json
|  |  |- epub-layout.fluid.json
|  |  |- epub-manifest.json
|  |  |- epub-manifest.pt-br.json
|  |  |- epub-manifest.fluid.json
|  |  |- audio-chapters-manifest.json
|  |  |- audio-manifest.json
|  |  `- publication-manifest.json
|  |- pages\
|  |- source\original.pdf
|  `- text\
|     |- source\
|     |- translation\pt-BR\
|     |- fluid\pt-BR\
|     `- locutor\
|- Nome do Livro - Ano - Autor.mp3
|- Nome do Livro - Ano - Autor.epub (complete or `both`)
|- Nome do Livro - Ano - Autor.pdf (complete or `both`)
|- <titulo>.epub (selected `fluid`)
|- <titulo>.pdf (selected `fluid`)
|- <titulo>-fluida.epub (optional `both`)
`- <titulo>-fluida.pdf (optional `both`)
```

## Shortcuts

```text
NUM0   $codex-workflows mode=PLAN.AUTO no-edits route{PLAN|P.DEEP} earned-rework? parallel-ready?
NUM0+1 $codex-workflows mode=P.DEEP repo no-edits deep-plan parallel-ready earned-rework
NUM0+2 $codex-workflows mode=IMPL.PHASE approved-roadmap goal-managed phased parallel-safe earned-rework-approved
NUM0+3 $codex-workflows mode=RESEARCH.DEEP
NUM0+7 $audiobook-codex stage=PHASE-1
NUM0+9 $audiobook-codex stage=PHASE-2
```

The two audiobook shortcuts are stable stage selectors. Their paths, defaults, role
routing, outputs, and validation gates are owned internally by
`plugins/audiobook-codex/skills/audiobook-codex/SKILL.md`; pipeline changes must update
that internal contract without expanding the AHK prompts.

Internally, the stages use schema `1.0` claim maps, exclusive worker staging, ledger
and review shards under `assembly/metadata/work`, independent claim verification, and
deterministic single-owner promotion. Role pools stay warm and receive new work through
a sliding queue with backpressure rather than waiting for a whole batch:

- `PHASE-1` runs one serial preflight, then parallel read-only structure/asset claims
  and pipelines page-range transcribers directly into separate verifiers;
- `PHASE-2` pipelines chapter narrator claims for the selected publication edition,
  keeps Chatterbox inference single-lane,
  overlaps one CPU chapter-assembly worker, and exports/validates EPUB and PDF in
  parallel after the textual snapshot freezes.

Canonical whole-book coverage, consistency, media, and publication gates still run
after incremental claim validation.

Use direct invocation, not a shortcut, for a whole-book translation:

```text
$audiobook-codex stage=TRANSLATE
```

Whole-book translation uses `faithful-contextual-ptbr-v1`: pages remain immutable
lineage units, while the translator reads the complete chapter and neighboring scene
context, follows one reviewed book brief and glossary, and performs semantic-fidelity
then literary-PT-BR passes. Material ambiguity is recorded instead of guessed.
Codex-native web research is allowed only after local context is insufficient; evidence
and access dates are stored in schema `1.1` `translation-ledger.json`. Browser chat,
external LLMs, and published online translations are not translation sources. Any
`needs-review` or `unresolved` item blocks translated EPUB/PDF export.

Use one direct invocation for a modern, fluent PT-BR reading edition:

```text
$audiobook-codex stage=FLUID
```

The agent automatically uses the approved translated PT-BR edition when available,
otherwise a Portuguese source. It freezes one whole-book style and glossary, rewrites
each ordered base paragraph as included or explicitly excluded, modernizes every
genuinely archaic surface form—including inside historical quotations, documentary
excerpts, captions, and notes—removes parenthetical author-year-page references, omits
a paragraph only when it is a proven translation of the immediately preceding
paragraph, removes its `Tradução livre` label, reduces expendable redundancy, and
improves clarity without adding or silently omitting semantic content. The
result stays under `text/fluid/pt-BR` and exports as separate `fluid-pt-br` EPUB/PDF
files. Publication copies those separately named files beside the canonical pair in
the public root; the faithful editions remain unchanged.

`NUM0+7` is source-faithful: it never corrects, modernizes, translates, or normalizes
the source. It records EPUB presentation separately in `metadata/epub-layout.json`,
using verified page-line spans for paragraphs, dialogues, verses, quotations, and
headings.

`NUM0+9` is the selected-publication route. Its internal per-book flag is
`assembly/metadata/publication-selection.json`, with `target` set to `complete`,
`fluid`, or `both`. It defaults to `complete`; change it only after asking the agent to
update that flag. `complete` publishes the approved complete edition, `fluid` publishes
only the approved fluid edition, and `both` runs the two edition tracks. Audio always
follows the same selected reading edition as the EPUB/PDF pair. For Pajelança, set the
flag to `fluid`.

Every new `NUM0+9` run applies `faithful-natural-v1`: the locutor text is reviewed
for natural PT-BR speech, semantic line boundaries, headings, dialogue, quotations,
verse, punctuation, numeric forms, and pronunciation-sensitive terms. Semantic footnotes
remain in the EPUB/PDF but their bodies and attached reference markers are excluded from
the locutor through reviewed `footnote_exclusion` records. The pipeline records the
result in `metadata/narrator-review.json` and renders with `--require-quality`.
After the final audio, EPUB, and PDF pass validation, run `publish_artifacts.py`.
For `target: both`, the complete edition uses the book-folder filename and the fluid
pair keeps its distinct `-fluida` export filenames. For `target: fluid`, the fluid
pair uses the unsuffixed title filename and does not require a complete pair. Its
title remains the book title; put a reading label, such as `Versão de audiolivro`, only
in the subtitle. The provenance copies remain in `assembly/audio/`, `assembly/exports/epub/`, and
`assembly/exports/pdf/`; each PDF keeps its `.pdf.json` sidecar.

New EPUB/PDF exports use only the ABNT-style generated title page (title, subtitle when
present, author, place, and year). They do not add an editorial cover image; the original
source cover remains in the reading order. The former `antique-paper` profile is accepted
only for historical-manifest compatibility. The paired PDF is generated locally
with the Codex bundled Python and ReportLab, writes to `assembly/exports/pdf/`, and is validated
with `validate_pdf_export.py`. A restored EPUB/PDF pair uses `assembly/assets/restoration/approved/`, remains separate, and requires a
recorded visual approval for every generated derivative.

`text/translation/pt-BR` is optional and exists only when the whole source book is in
another language. A Portuguese book with intentional isolated English or other foreign
words remains a Portuguese source and is not translated. A translated EPUB/PDF pair is a separate
semantic PT-BR edition with PT-BR text and metadata; it keeps source image pixels
unchanged unless an already approved restored edition is explicitly selected.

## Validate

```powershell
.\scripts\validate.ps1
```

Routine validation uses voice-evidence `provenance` mode: it verifies the immutable
promotion declarations, hashes, report consistency, bundled references, and renderer
bindings without pretending that deleted historical workspaces still exist. This mode
does not authorize a new or replacement production profile.

Before shipping a Chatterbox renderer or calibrated voice-profile change, restore the
retained calibration workspace (or perform a new calibration), then run both the strict
external-evidence and CUDA reproducibility gates:

```powershell
.\scripts\validate.ps1 -FullVoiceEvidence -ChatterboxSmoke
```

## Voice Calibration

Use `$voice-calibration` before changing the local voice reference, a local TTS
engine, or an official narrator profile. It creates a dedicated external workspace,
freezes the three-prompt PT-BR corpus and manually imported target audio with hashes,
then requires cross-prompt ranking, listening review, DSP comparison, and a production
smoke render before promotion.

The approved decisions are documented in
[`docs/voice-calibration/feminina-v1.md`](docs/voice-calibration/feminina-v1.md) and
[`docs/voice-calibration/masculina-v1.md`](docs/voice-calibration/masculina-v1.md). Historical
promotion manifests and dated reports may still contain `E:\Pessoal\e-books` paths;
treat them as immutable provenance, not current workspace defaults.
No separate AHK shortcut is registered: `NUM0+9` remains the production Phase 2 route
with `masculina-v1` as the default profile.

## Chatterbox PT-BR

The dedicated local runtime is installed at:

```text
E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe
```

It uses the locally downloaded `ResembleAI/Chatterbox-Multilingual-pt-br` V3 model.
The default `masculina-v1` profile uses
`plugins\audiobook-codex\assets\voices\Masculina.mp3`; the approved alternative
`feminina-v1` profile uses `plugins\audiobook-codex\assets\voices\Feminina.mp3`
and is selected with `--voice-profile feminina-v1`. Each named profile freezes its
reference hash, seed strategy, and sampling parameters. `masculina-v1` starts every
segment with its calibrated seed `54321`; `feminina-v1` retains its existing
per-segment indexed strategy. Its narrator input is one
complete spoken locution per non-empty line, with a 320-character maximum; the renderer
rejects digits, common abbreviations, URLs, email addresses, and bracketed audio markup.
Book renders additionally require `metadata/narration-plan.json`: it preserves semantic
paragraphs where they fit, records provenance, and applies 60 ms continuation, 170 ms
sentence, 420 ms paragraph, and 1 s heading pauses during assembly.
Completed units are assembled under `audio\chatterbox-pt-br\chapters\`: immutable
matrices in `original\`, final-speed WAV/MP3 files in `final\`, and listening variants
in `temp\`.
After reviewing a chapter, render just it with `--chapters chapter-01`; the complete
audiobook remains a separate artifact until it is remounted from valid segments.
The Chatterbox process forces Hugging Face and Transformers offline so missing local
assets fail instead of being fetched.

```powershell
$book = 'E:\Pessoal\Library\Nome do Livro - Ano - Autor'
$python = 'E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe'

& $python .\plugins\audiobook-codex\scripts\render_chatterbox.py `
  --book-root $book `
  --input-file "$book\assembly\text\locutor\book.txt" `
  --output-dir "$book\assembly\audio\chatterbox-pt-br" `
  --format mp3 `
  --require-lineage `
  --require-quality

# Use the Codex bundled Python for paired EPUB/PDF export so ReportLab is available.
$runtimePython = 'C:\Users\mathe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$epub = "$book\assembly\exports\epub\<book>-fiel.epub"
$pdf = "$book\assembly\exports\pdf\<book>-fiel.pdf"

& $runtimePython .\plugins\audiobook-codex\scripts\export_reader_pair.py `
  --book-root $book `
  --epub-output $epub `
  --pdf-output $pdf `
  --image-edition original

python .\plugins\audiobook-codex\scripts\publish_artifacts.py `
  --book-root $book `
  --audio "$book\assembly\audio\chatterbox-pt-br\audiobook.mp3" `
  --epub $epub `
  --pdf $pdf
```
