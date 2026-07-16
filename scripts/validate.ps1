param(
    [switch]$ChatterboxSmoke
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$plugin = Join-Path $repo 'plugins\audiobook-codex'
$skill = Join-Path $plugin 'skills\audiobook-codex'
$voiceCalibrationSkill = Join-Path $plugin 'skills\voice-calibration'
$voiceCalibrationReport = Join-Path $repo 'docs\voice-calibration\feminina-v1.md'
$femininaProfileValidator = Join-Path $plugin 'scripts\validate_feminina_profile.py'
$femininaPromotion = Join-Path $plugin 'assets\voices\feminina-v1.promotion.json'
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

foreach ($profile in 'audiobook-structure.toml', 'audiobook-transcriber.toml', 'audiobook-verifier.toml') {
    $path = Join-Path $repo "agents\$profile"
    $text = Get-Content -Raw $path
    if ($text -notmatch 'name\s*=' -or $text -notmatch 'developer_instructions\s*=') {
        throw "Invalid audiobook agent profile: $profile"
    }
}

$readme = Get-Content -Raw (Join-Path $repo 'README.md')
if (!(Test-Path $voiceCalibrationReport)) {
    throw "Missing voice-calibration report: $voiceCalibrationReport"
}
$ahkText = Get-Content -Raw $ahk
$bindings = @(
    'Numpad0::PastePrompt("$codex-workflows mode=PLAN.AUTO no-edits route{PLAN|P.DEEP} earned-rework? parallel-ready?")',
    'Numpad0 & Numpad1::PastePrompt("$codex-workflows mode=P.DEEP repo no-edits deep-plan parallel-ready earned-rework")',
    'Numpad0 & Numpad2::PastePrompt("$codex-workflows mode=IMPL.PHASE approved-roadmap goal-managed phased parallel-safe earned-rework-approved")',
    'Numpad0 & Numpad3::PastePrompt("$codex-workflows mode=RESEARCH.DEEP scope{web|github|repo?} no-edits fanout=adaptive evidence{primary|official|repo} synthesize{solution|roadmap} topic: ")',
    'Numpad0 & Numpad7::PastePrompt("$audiobook-codex stage=MAP native-only source{PDF|EPUB} library-root{E:\Pessoal\e-books} output{book-map.json|assets-manifest.json} visual-fallback{pdf|computer} swarm{bounded}")',
    'Numpad0 & Numpad8::PastePrompt("$audiobook-codex stage=TRANSCRIBE native-only input{book-map.json|assets-manifest.json} output{text/source|epub-manifest.json} fidelity=strict ledger=required epub-profile{antique-paper}")',
    'Numpad0 & Numpad9::PastePrompt("$audiobook-codex stage=RENDER native-only input{text/source|text/translation/pt-BR?|epub-manifest*.json} output{text/locutor|audio|epub|publish-root} narrator{faithful|archaic-modernized|translated-pt-br} tts{chatterbox-pt-br} voice-profile{feminina-v1} locutor{faithful-natural-v1|line-delimited-v1|max=320|quality-review=required|narration-plan=paragraph-pauses-v1} language=pt-BR text-edition{original|translated-pt-br} epub-profile{antique-paper} epub-images{original|approved-restored} restoration=review-required")'
)
$mapPrompt = '$audiobook-codex stage=MAP native-only source{PDF|EPUB} library-root{E:\Pessoal\e-books} output{book-map.json|assets-manifest.json} visual-fallback{pdf|computer} swarm{bounded}'

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
if ($ahkText -match '(?m)^ScrollLock::') {
    throw 'Codex Audiobook AHK must not register ScrollLock.'
}
if ($readme -notmatch [regex]::Escape('NUM0+7 ' + $mapPrompt)) {
    throw 'README.md does not document the NUM0+7 audiobook map binding.'
}
if ($readme -notmatch [regex]::Escape('docs/voice-calibration/feminina-v1.md')) {
    throw 'README.md does not link the feminina-v1 calibration report.'
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

& $runtimePython $femininaProfileValidator --renderer (Join-Path $plugin 'scripts\render_chatterbox.py') --promotion $femininaPromotion --report $voiceCalibrationReport
if ($LASTEXITCODE -ne 0) {
    throw 'Feminina-v1 promotion validation failed.'
}

& $runtimePython (Join-Path $plugin 'scripts\test_tools.py')
if ($LASTEXITCODE -ne 0) {
    throw 'Audiobook plugin script validation failed.'
}

$focusedScriptTests = @(
    'test_narration_plan.py',
    'test_narrator_quality.py',
    'test_epub_notes.py'
)
foreach ($testScript in $focusedScriptTests) {
    & $runtimePython (Join-Path $plugin "scripts\$testScript")
    if ($LASTEXITCODE -ne 0) {
        throw "Audiobook focused script validation failed: $testScript"
    }
}

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

& $skillValidationPython (Join-Path $skillCreator 'scripts\quick_validate.py') $skill
if ($LASTEXITCODE -ne 0) {
    throw 'Audiobook skill validation failed.'
}

& $skillValidationPython (Join-Path $skillCreator 'scripts\quick_validate.py') $voiceCalibrationSkill
if ($LASTEXITCODE -ne 0) {
    throw 'Voice-calibration skill validation failed.'
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
