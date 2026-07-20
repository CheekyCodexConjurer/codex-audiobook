param(
    [switch]$ChatterboxSmoke,
    [switch]$FullVoiceEvidence
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$plugin = Join-Path $repo 'plugins\audiobook-codex'
$skill = Join-Path $plugin 'skills\audiobook-codex'
$voiceCalibrationSkill = Join-Path $plugin 'skills\voice-calibration'
$pluginManifest = Join-Path $plugin '.codex-plugin\plugin.json'
$artifactContract = Join-Path $skill 'references\artifact-contract.md'
$swarmProtocol = Join-Path $skill 'references\swarm-protocol.md'
$translationPolicy = Join-Path $skill 'references\translation-policy.md'
$fluidEditionPolicy = Join-Path $skill 'references\fluid-edition-policy.md'
$voiceEvidenceContract = Join-Path $voiceCalibrationSkill 'references\evidence-contract.md'
$voiceInitWorkspace = Join-Path $voiceCalibrationSkill 'scripts\init_calibration_workspace.py'
$bookLayout = Join-Path $plugin 'scripts\book_layout.py'
$preflight = Join-Path $plugin 'scripts\preflight.py'
$voiceCalibrationReport = Join-Path $repo 'docs\voice-calibration\feminina-v1.md'
$femininaProfileValidator = Join-Path $plugin 'scripts\validate_feminina_profile.py'
$femininaPromotion = Join-Path $plugin 'assets\voices\feminina-v1.promotion.json'
$masculinaCalibrationReport = Join-Path $repo 'docs\voice-calibration\masculina-v1.md'
$masculinaProfileValidator = Join-Path $plugin 'scripts\validate_masculina_profile.py'
$masculinaPromotion = Join-Path $plugin 'assets\voices\masculina-v1.promotion.json'
$marketplace = Join-Path $repo '.agents\plugins\marketplace.json'
$ahk = Join-Path $repo 'ahk\codex_audiobook.ahk'
$installer = Join-Path $repo 'scripts\install.ps1'
$ahkExe = 'E:\Programs\AHK\v2\AutoHotkey64.exe'
$workflowSkill = 'C:\Users\mathe\.agents\skills\codex-workflows\SKILL.md'
$runtimePython = 'C:\Users\mathe\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
$chatterboxPython = if ($env:CHATTERBOX_PYTHON) {
    $env:CHATTERBOX_PYTHON
} else {
    'E:\Pessoal\tts\chatterbox-multilingual-v3\venv\Scripts\python.exe'
}
$pluginCreator = 'C:\Users\mathe\.codex\skills\.system\plugin-creator'
$skillCreator = 'C:\Users\mathe\.codex\skills\.system\skill-creator'
$voiceEvidenceMode = if ($FullVoiceEvidence) { 'full' } else { 'provenance' }

function Assert-Contains {
    param(
        [string]$Text,
        [string]$Needle,
        [string]$Label
    )
    if ($Text -notmatch [regex]::Escape($Needle)) {
        throw "$Label is missing: $Needle"
    }
}

function Assert-NotRegex {
    param(
        [string]$Text,
        [string]$Pattern,
        [string]$Message
    )
    if ($Text -match $Pattern) {
        throw $Message
    }
}

function Invoke-ParallelPythonChecks {
    param(
        [Parameter(Mandatory)]
        [array]$Checks,
        [int]$ThrottleLimit = 8
    )

    if ($Checks.Count -eq 0) {
        return
    }

    $python = $runtimePython
    $results = $Checks |
        ForEach-Object -Begin { $index = 0 } -Process {
            [pscustomobject]@{
                Index = $index++
                Label = $_.Label
                Arguments = @($_.Arguments)
            }
        } |
        ForEach-Object -Parallel {
            $arguments = @($_.Arguments)
            $output = & $using:python @arguments 2>&1
            [pscustomobject]@{
                Index = $_.Index
                Label = $_.Label
                ExitCode = $LASTEXITCODE
                Output = @($output | ForEach-Object { $_.ToString() })
            }
        } -ThrottleLimit ([Math]::Max(1, [Math]::Min($ThrottleLimit, $Checks.Count)))

    $failed = @()
    foreach ($result in $results | Sort-Object Index) {
        foreach ($line in $result.Output) {
            Write-Host $line
        }
        if ($result.ExitCode -ne 0) {
            $failed += $result.Label
        }
    }
    if ($failed.Count -gt 0) {
        throw "Parallel validation failed: $($failed -join ', ')"
    }
}

if (!(Test-Path $runtimePython)) {
    $runtimePython = (Get-Command python -ErrorAction Stop).Source
}
if ($ChatterboxSmoke) {
    if (!(Test-Path $chatterboxPython)) {
        throw "Chatterbox CUDA smoke runtime not found: $chatterboxPython"
    }
    $env:CHATTERBOX_REAL_SMOKE = '1'
    $env:CHATTERBOX_PYTHON = $chatterboxPython
}

foreach ($profile in 'audiobook-structure.toml', 'audiobook-transcriber.toml', 'audiobook-translator.toml', 'audiobook-editor.toml', 'audiobook-narrator.toml', 'audiobook-verifier.toml') {
    $path = Join-Path $repo "agents\$profile"
    $text = Get-Content -Raw $path
    if ($text -notmatch 'name\s*=' -or $text -notmatch 'developer_instructions\s*=') {
        throw "Invalid audiobook agent profile: $profile"
    }
    if ($text -notmatch '(?m)^model\s*=\s*"gpt-5\.6-sol"\s*$') {
        throw "Audiobook agent must use gpt-5.6-sol: $profile"
    }
    if ($text -notmatch '(?m)^model_reasoning_effort\s*=\s*"medium"\s*$') {
        throw "Base audiobook agent must use medium reasoning: $profile"
    }
}

$modelCatalogPath = 'C:\Users\mathe\.codex\super-app-manager\custom_model_catalog.json'
if (!(Test-Path $modelCatalogPath)) {
    throw "Missing Codex model catalog: $modelCatalogPath"
}
$modelCatalog = Get-Content -Raw $modelCatalogPath | ConvertFrom-Json
$solModel = @($modelCatalog.models | Where-Object { $_.slug -eq 'gpt-5.6-sol' })[0]
if ($null -eq $solModel) {
    throw 'Codex model catalog does not contain gpt-5.6-sol.'
}
$supportedEfforts = @($solModel.supported_reasoning_levels | ForEach-Object { [string]$_.effort })
foreach ($effort in 'low', 'medium', 'high', 'xhigh', 'max') {
    if ($supportedEfforts -notcontains $effort) {
        throw "gpt-5.6-sol does not support required reasoning effort: $effort"
    }
}

$installerText = Get-Content -Raw $installer
foreach ($fragment in '$agentEfforts = @(''low'', ''high'', ''xhigh'', ''max'')', 'function Install-AgentEffortVariants', 'model_reasoning_effort = `"$effort`"') {
    if ($installerText -notmatch [regex]::Escape($fragment)) {
        throw "Audiobook agent installer is missing effort-variant support: $fragment"
    }
}

$readme = Get-Content -Raw (Join-Path $repo 'README.md')
$agentsText = Get-Content -Raw (Join-Path $repo 'AGENTS.md')
$verifierAgentText = Get-Content -Raw (Join-Path $repo 'agents\audiobook-verifier.toml')
$skillText = Get-Content -Raw (Join-Path $skill 'SKILL.md')
$artifactText = Get-Content -Raw $artifactContract
$swarmText = Get-Content -Raw $swarmProtocol
$translationPolicyText = Get-Content -Raw $translationPolicy
$fluidEditionPolicyText = Get-Content -Raw $fluidEditionPolicy
$narratorPolicyText = Get-Content -Raw (Join-Path $skill 'references\narrator-policy.md')
$editorAgentText = Get-Content -Raw (Join-Path $repo 'agents\audiobook-editor.toml')
$voiceSkillText = Get-Content -Raw (Join-Path $voiceCalibrationSkill 'SKILL.md')
$voiceEvidenceText = Get-Content -Raw $voiceEvidenceContract
$voiceInitText = Get-Content -Raw $voiceInitWorkspace
$bookLayoutText = Get-Content -Raw $bookLayout
$preflightText = Get-Content -Raw $preflight
$pluginManifestText = Get-Content -Raw $pluginManifest
$pluginManifestJson = $pluginManifestText | ConvertFrom-Json
$legacyEbooksProvenanceFiles = @(
    $voiceCalibrationReport,
    $masculinaCalibrationReport,
    $femininaPromotion,
    $masculinaPromotion
)
if (!(Test-Path $voiceCalibrationReport)) {
    throw "Missing voice-calibration report: $voiceCalibrationReport"
}
if (!(Test-Path $masculinaCalibrationReport)) {
    throw "Missing voice-calibration report: $masculinaCalibrationReport"
}
$ahkText = Get-Content -Raw $ahk
$bindings = @(
    'Numpad0::PastePrompt("$codex-workflows mode=PLAN.AUTO no-edits route{PLAN|P.DEEP} earned-rework? parallel-ready?")',
    'Numpad0 & Numpad1::PastePrompt("$codex-workflows mode=P.DEEP repo no-edits deep-plan parallel-ready earned-rework")',
    'Numpad0 & Numpad2::PastePrompt("$codex-workflows mode=IMPL.PHASE approved-roadmap goal-managed phased parallel-safe earned-rework-approved")',
    'Numpad0 & Numpad3::PastePrompt("$codex-workflows mode=RESEARCH.DEEP")',
    'Numpad0 & Numpad7::PastePrompt("$audiobook-codex stage=PHASE-1")',
    'Numpad0 & Numpad9::PastePrompt("$audiobook-codex stage=PHASE-2")'
)
$stagePrompts = @(
    '$audiobook-codex stage=PHASE-1',
    '$audiobook-codex stage=PHASE-2'
)
$readmeStageBindings = @(
    'NUM0+7 $audiobook-codex stage=PHASE-1',
    'NUM0+9 $audiobook-codex stage=PHASE-2'
)

if (!(Test-Path $workflowSkill)) {
    throw "Missing shared Codex Workflows skill: $workflowSkill"
}
if ((Get-Content -Raw $workflowSkill) -notmatch '(?m)^name:\s*codex-workflows\s*$') {
    throw 'Shared Codex Workflows skill frontmatter is invalid.'
}
foreach ($binding in $bindings) {
    if ($ahkText -notmatch [regex]::Escape($binding)) {
        throw "Missing or invalid AHK binding: $binding"
    }
}
if ($ahkText -match '\$audiobook-codex stage=(?:PHASE-1|PHASE-2)\s+') {
    throw 'Audiobook AHK stage selectors must not contain inline workflow options.'
}
if ($ahkText -match '(?m)^ScrollLock::') {
    throw 'Codex Audiobook AHK must not register ScrollLock.'
}
foreach ($readmeBinding in $readmeStageBindings) {
    $pattern = '(?m)^' + [regex]::Escape($readmeBinding) + '\r?$'
    if ($readme -notmatch $pattern) {
        throw "README.md does not document the exact minimal audiobook binding: $readmeBinding"
    }
}
if ($readme -notmatch [regex]::Escape('docs/voice-calibration/feminina-v1.md')) {
    throw 'README.md does not link the feminina-v1 calibration report.'
}
if ($readme -notmatch [regex]::Escape('docs/voice-calibration/masculina-v1.md')) {
    throw 'README.md does not link the masculina-v1 calibration report.'
}

Assert-Contains $agentsText 'E:\Pessoal\Library' 'AGENTS.md'
Assert-Contains $agentsText 'Keep the public AHK and plugin entry prompts stable and minimal' 'AGENTS.md stage-selector maintenance rule'
Assert-Contains $agentsText 'Do not expand or duplicate those options in the AHK bindings, plugin default prompt, or README shortcut lines.' 'AGENTS.md prompt-drift rule'
Assert-Contains $readme 'E:\Pessoal\Library' 'README.md'
Assert-Contains $readme 'The two audiobook shortcuts are stable stage selectors.' 'README.md stage-selector contract'
Assert-Contains $readme 'The public root contains `assembly/`, the canonical EPUB/PDF pair' 'README.md library contract'
Assert-Contains $readme 'published fluid EPUB/PDF pair' 'README.md fluid publication contract'
Assert-Contains $readme 'Nome do Livro - Ano - Autor.mp3' 'README.md stable public audio name'
Assert-Contains $readme 'Nome do Livro - Ano - Autor.epub' 'README.md stable public EPUB name'
Assert-Contains $readme 'Nome do Livro - Ano - Autor.pdf' 'README.md stable public PDF name'
Assert-Contains $readme 'promotion manifests and dated reports may still contain `E:\Pessoal\e-books` paths' 'README.md historical provenance note'
Assert-Contains $readme '`faithful-contextual-ptbr-v1`' 'README.md translation profile'
Assert-Contains $readme 'command-scoped `xhigh` compatibility override' 'README.md Codex CLI compatibility note'
Assert-Contains $readme 'do not run `codex plugin marketplace upgrade`' 'README.md local marketplace update note'
Assert-Contains $readme '-FullVoiceEvidence -ChatterboxSmoke' 'README.md strict voice evidence gate'
Assert-Contains $readme '.\plugins\audiobook-codex\scripts\export_reader_pair.py' 'README.md paired reader-export example'
Assert-NotRegex $readme '(?m)^\s*& \$runtimePython \.\\plugins\\audiobook-codex\\scripts\\export_pdf\.py' 'README.md production example must not bypass paired EPUB/PDF export.'
Assert-Contains $skillText '--library-root "E:\Pessoal\Library" --title <title> --publication-year <year> --author <author>' 'audiobook skill preflight contract'
Assert-Contains $skillText 'The default public library root is `E:\Pessoal\Library`' 'audiobook skill library contract'
Assert-Contains $skillText '## Stable Stage Selectors' 'audiobook skill stage-selector contract'
Assert-Contains $skillText '`target: "complete" | "fluid" | "both"`' 'audiobook skill publication selection contract'
Assert-Contains $skillText 'scripts/export_reader_pair.py' 'audiobook skill paired export orchestration'
Assert-Contains $skillText 'shared exclusive book transaction lock' 'audiobook skill transaction lock contract'
foreach ($stagePrompt in $stagePrompts) {
    Assert-Contains $skillText $stagePrompt 'audiobook skill minimal stage selector'
}
Assert-Contains $skillText 'Treat omitted options as the canonical defaults below.' 'audiobook skill internal-default contract'
Assert-Contains $skillText 'Do not copy those details back into' 'audiobook skill prompt-drift rule'
Assert-Contains $artifactText 'Default library root: `E:\Pessoal\Library`.' 'artifact contract library root'
Assert-Contains $artifactText '`--book-root` always' 'artifact contract book-root semantics'
Assert-Contains $artifactText 'The `assembly/` directory contains exactly these top-level directories:' 'artifact contract assembly top-level contract'
Assert-Contains $artifactText '`assets`, `audio`, `exports`, `metadata`, `pages`, `source`, and `text`' 'artifact contract assembly directory set'
Assert-Contains $artifactText '`assets/restoration/approved`' 'artifact contract restoration location'
Assert-Contains $swarmText '`assets/restoration/approved/`' 'swarm protocol restoration boundary'
Assert-Contains $swarmText 'the digest excludes' 'swarm immutable claim digest contract'
Assert-Contains $swarmText 'ready_for_verification → verified → merged' 'swarm canonical claim lifecycle'
Assert-Contains $artifactText '`publication-selection.json`' 'artifact contract publication selection'
Assert-Contains $artifactText '`reader_pair_identity`' 'artifact contract reader pair identity'
Assert-Contains $artifactText 'counterparts block one-sided publication' 'artifact contract paired publication gate'
Assert-Contains $artifactText 'Newly supplied EPUB/PDF sidecars must contain the complete common lineage' 'artifact contract reader lineage gate'
Assert-Contains $agentsText '`faithful-contextual-ptbr-v1`' 'AGENTS.md translation profile'
Assert-Contains $agentsText '`fluid-faithful-ptbr-v1`' 'AGENTS.md fluid edition profile'
Assert-Contains $skillText '`faithful-contextual-ptbr-v1`' 'audiobook skill translation profile'
Assert-Contains $skillText '`fluid-faithful-ptbr-v1`' 'audiobook skill fluid edition profile'
Assert-Contains $translationPolicyText 'context-first-evidence-recorded-v1' 'translation research policy'
Assert-Contains $translationPolicyText 'Only `resolved` ambiguity entries may pass' 'translation ambiguity gate'
Assert-Contains $fluidEditionPolicyText 'ledger must cover every base' 'fluid edition block coverage'
Assert-Contains $fluidEditionPolicyText 'whole-book voice and terminology consistency' 'fluid edition consistency gate'
Assert-Contains $fluidEditionPolicyText 'modernize_historical_quotations' 'fluid edition quoted-archaism policy'
Assert-Contains $fluidEditionPolicyText 'no unreviewed archaic surface forms' 'fluid edition comprehensive modernization gate'
Assert-Contains $fluidEditionPolicyText 'citation_reference_exclusion' 'fluid edition citation-reference exclusion contract'
Assert-Contains $fluidEditionPolicyText 'duplicate_translation_exclusion' 'fluid edition duplicate-translation exclusion contract'
Assert-Contains $fluidEditionPolicyText 'translation_label_exclusion' 'fluid edition translation-label exclusion contract'
Assert-Contains $artifactText 'ledger files use schema `1.2`' 'artifact contract current fluid schema'
Assert-Contains $artifactText 'no unsupported omissions' 'artifact contract authorized fluid exclusions'
Assert-Contains $editorAgentText 'never preserve archaic spelling or grammar merely because the passage is quoted' 'audiobook editor quoted-archaism rule'
Assert-Contains $verifierAgentText 'fluid-faithful-ptbr-v1' 'audiobook verifier fluid profile'
foreach ($fluidGate in 'semantic_fidelity', 'no_additions', 'no_omissions', 'fluency', 'whole_book_consistency') {
    Assert-Contains $verifierAgentText $fluidGate "audiobook verifier fluid gate $fluidGate"
}
Assert-Contains $verifierAgentText 'archaic_modernization' 'audiobook verifier archaic modernization gate'
Assert-Contains $verifierAgentText 'editorial_exclusions' 'audiobook verifier editorial exclusion gate'
Assert-Contains $narratorPolicyText '`footnote_exclusion`' 'narrator footnote exclusion policy'
Assert-Contains $narratorPolicyText 'not a spoken note' 'narrator footnotes must not be spoken'
Assert-Contains $voiceSkillText 'E:\Pessoal\Library\_voice-calibration-<profile-id>' 'voice-calibration default workspace'
Assert-Contains $voiceSkillText 'Historical promotion manifests and dated reports may retain `E:\Pessoal\e-books` paths only as immutable provenance' 'voice-calibration historical provenance note'
Assert-Contains $voiceSkillText '## Repository Evidence Modes' 'voice-calibration evidence-mode contract'
Assert-Contains $voiceSkillText 'scripts\validate.ps1 -FullVoiceEvidence' 'voice-calibration full-evidence gate'
Assert-Contains $voiceEvidenceText 'immutable-provenance allowlist for old `E:\Pessoal\e-books` paths' 'voice evidence historical allowlist'
Assert-Contains $voiceEvidenceText '`provenance` verifies' 'voice evidence provenance mode'
Assert-Contains $voiceEvidenceText '`full` reopens every declared external path' 'voice evidence full mode'
Assert-Contains $voiceInitText 'DEFAULT_LIBRARY_ROOT = Path(r"E:\Pessoal\Library")' 'voice calibration init default root'
Assert-Contains $bookLayoutText 'DEFAULT_LIBRARY_ROOT = Path(r"E:\Pessoal\Library")' 'book layout default root'
Assert-Contains $preflightText 'DEFAULT_LIBRARY_ROOT,' 'preflight shared library-root import'
if ($pluginManifestJson.interface.defaultPrompt -ne '$audiobook-codex stage=PHASE-1') {
    throw 'Plugin default prompt must remain the minimal PHASE-1 stage selector.'
}
Assert-NotRegex $ahkText 'library-root\{E:\\Pessoal\\e-books\}' 'AHK map binding still points at the legacy e-books library.'
Assert-NotRegex $pluginManifestText 'library-root\{E:\\\\Pessoal\\\\e-books\}' 'Plugin default prompt still points at the legacy e-books library.'
Assert-NotRegex $artifactText '(?m)^\|- restoration[\\/]' 'Artifact contract still documents top-level restoration.'

foreach ($legacyPath in $legacyEbooksProvenanceFiles) {
    if (!(Test-Path $legacyPath)) {
        throw "Missing immutable historical provenance file: $legacyPath"
    }
}

try {
    [scriptblock]::Create((Get-Content -Raw $installer)) | Out-Null
} catch {
    throw "Invalid installer script: $($_.Exception.Message)"
}

& $runtimePython (Join-Path $plugin 'scripts\validate_plugin_local.py') --plugin-root $plugin --marketplace $marketplace
if ($LASTEXITCODE -ne 0) {
    throw 'Audiobook local plugin validation failed.'
}

Write-Host "Voice-profile evidence mode: $voiceEvidenceMode"

$focusedScriptTests = @(
    'test_book_layout.py',
    'test_voice_profile_validation.py',
    'test_narration_plan.py',
    'test_narrator_quality.py',
    'test_epub_notes.py',
    'test_pdf_export.py',
    'test_fluid_edition_ledger.py',
    'test_fluid_exports.py',
    'test_narrator_exclusions.py',
    'test_pdf_outline.py',
    'test_swarm_workflow.py',
    'test_claim_scoped_validation.py',
    'test_audio_pipeline.py',
    'test_publication_selection.py',
    'test_export_validation_efficiency.py',
    'test_export_idempotence.py',
    'test_export_cache_contract.py',
    'test_publication_lineage.py',
    'test_transaction_recovery.py',
    'test_translated_reader_flow.py'
)
Invoke-ParallelPythonChecks @(
    @{
        Label = 'feminina-v1 promotion'
        Arguments = @(
            $femininaProfileValidator,
            '--renderer', (Join-Path $plugin 'scripts\render_chatterbox.py'),
            '--promotion', $femininaPromotion,
            '--report', $voiceCalibrationReport,
            '--evidence-mode', $voiceEvidenceMode
        )
    },
    @{
        Label = 'masculina-v1 promotion'
        Arguments = @(
            $masculinaProfileValidator,
            '--renderer', (Join-Path $plugin 'scripts\render_chatterbox.py'),
            '--promotion', $masculinaPromotion,
            '--report', $masculinaCalibrationReport,
            '--evidence-mode', $voiceEvidenceMode
        )
    }
) 2

$scriptChecks = @(
    @{
        Label = 'test_tools.py'
        Arguments = @((Join-Path $plugin 'scripts\test_tools.py'))
    }
)
$scriptChecks += $focusedScriptTests | ForEach-Object {
    @{
        Label = $_
        Arguments = @((Join-Path $plugin "scripts\$_"))
    }
}
Invoke-ParallelPythonChecks $scriptChecks 8

& $runtimePython -B (Join-Path $voiceCalibrationSkill 'scripts\test_voice_calibration.py')
if ($LASTEXITCODE -ne 0) {
    throw 'Voice-calibration skill script validation failed.'
}

$skillValidationPython = $runtimePython
$yamlAvailable = ((& $skillValidationPython -c "import importlib.util; print('1' if importlib.util.find_spec('yaml') else '0')").Trim() -eq '1')
if (!$yamlAvailable -and (Test-Path $chatterboxPython)) {
    $skillValidationPython = $chatterboxPython
    $yamlAvailable = ((& $skillValidationPython -c "import importlib.util; print('1' if importlib.util.find_spec('yaml') else '0')").Trim() -eq '1')
}
if (!$yamlAvailable) {
    throw 'PyYAML is required for upstream skill and plugin validation.'
}

$runtimePythonForParallel = $runtimePython
$runtimePython = $skillValidationPython
try {
    Invoke-ParallelPythonChecks @(
        @{
            Label = 'Audiobook skill validation'
            Arguments = @((Join-Path $skillCreator 'scripts\quick_validate.py'), $skill)
        },
        @{
            Label = 'Voice-calibration skill validation'
            Arguments = @((Join-Path $skillCreator 'scripts\quick_validate.py'), $voiceCalibrationSkill)
        }
    ) 2
} finally {
    $runtimePython = $runtimePythonForParallel
}

& $skillValidationPython (Join-Path $pluginCreator 'scripts\validate_plugin.py') $plugin
if ($LASTEXITCODE -ne 0) {
    throw 'Audiobook plugin validation failed.'
}

if (Test-Path $ahkExe) {
    & $ahkExe /ErrorStdOut /Validate $ahk
} else {
    Write-Warning "AHK executable not found: $ahkExe"
}

Write-Host 'Validation OK.'
