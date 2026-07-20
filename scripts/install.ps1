param(
    [switch]$RegisterMarketplace,
    [switch]$RestartAhk
)

$ErrorActionPreference = 'Stop'

$repo = Split-Path -Parent (Split-Path -Parent $PSCommandPath)
$agentsSource = Join-Path $repo 'agents'
$ahkSource = Join-Path $repo 'ahk\codex_audiobook.ahk'
$marketplaceSource = $repo

$agentsDest = 'C:\Users\mathe\.codex\agents'
$ahkDest = 'C:\Users\mathe\Documents\Codex\2026-07-01\pod\outputs\codex_audiobook.ahk'
$ahkExe = 'E:\Programs\AHK\v2\AutoHotkey64.exe'
$workflowSkill = 'C:\Users\mathe\.agents\skills\codex-workflows\SKILL.md'
$codexReasoningOverride = 'model_reasoning_effort="xhigh"'
$agentEfforts = @('low', 'high', 'xhigh', 'max')

function Install-AgentEffortVariants {
    param(
        [Parameter(Mandatory)]
        [string]$Source,
        [Parameter(Mandatory)]
        [string]$Destination
    )

    $profile = Get-Content -Raw -LiteralPath $Source
    $nameMatch = [regex]::Match($profile, '(?m)^name\s*=\s*"(?<name>[^"]+)"\s*$')
    if (!$nameMatch.Success) {
        throw "Agent profile has no name: $Source"
    }

    foreach ($effort in $agentEfforts) {
        $variantName = "$($nameMatch.Groups['name'].Value)-$effort"
        $variant = [regex]::Replace(
            $profile,
            '(?m)^name\s*=\s*"[^"]+"\s*$',
            "name = `"$variantName`""
        )
        $variant = [regex]::Replace(
            $variant,
            '(?m)^model_reasoning_effort\s*=\s*"[^"]+"\s*$',
            "model_reasoning_effort = `"$effort`""
        )
        Set-Content -LiteralPath (Join-Path $Destination "$variantName.toml") -Value $variant -NoNewline
    }
}

New-Item -ItemType Directory -Force $agentsDest, (Split-Path -Parent $ahkDest) | Out-Null

if (!(Test-Path $workflowSkill)) {
    throw "Missing shared Codex Workflows skill: $workflowSkill"
}

foreach ($profile in 'audiobook-structure.toml', 'audiobook-transcriber.toml', 'audiobook-translator.toml', 'audiobook-editor.toml', 'audiobook-narrator.toml', 'audiobook-verifier.toml') {
    $source = Join-Path $agentsSource $profile
    Copy-Item -LiteralPath $source -Destination (Join-Path $agentsDest $profile) -Force
    Install-AgentEffortVariants -Source $source -Destination $agentsDest
}
Copy-Item -LiteralPath $ahkSource -Destination $ahkDest -Force

Write-Host "Installed Codex Audiobook."
Write-Host "Agents: $agentsDest"
Write-Host "AHK: $ahkDest"

if ($RegisterMarketplace) {
    $codex = Get-Command codex -ErrorAction Stop
    & $codex.Source -c $codexReasoningOverride plugin marketplace add $marketplaceSource
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to register Codex Audiobook marketplace: $marketplaceSource"
    }
    Write-Host "Registered Codex Audiobook marketplace."
    Write-Host "The command-scoped reasoning override did not modify the global Codex configuration."
    Write-Host "Local marketplaces do not need marketplace upgrade; open a new task after plugin updates."
}

if ($RestartAhk) {
    if (!(Test-Path $ahkExe)) {
        throw "AHK executable not found: $ahkExe"
    }

    $running = Get-CimInstance Win32_Process | Where-Object {
        $_.Name -eq 'AutoHotkey64.exe' -and $_.CommandLine -match [regex]::Escape($ahkDest)
    }
    foreach ($entry in $running) {
        $process = Get-Process -Id $entry.ProcessId -ErrorAction SilentlyContinue
        if ($process) {
            Stop-Process -Id $process.Id -ErrorAction Stop
            $process.WaitForExit(5000) | Out-Null
        }
    }

    $process = Start-Process -FilePath $ahkExe -ArgumentList @($ahkDest) -WindowStyle Hidden -PassThru
    Start-Sleep -Milliseconds 750
    if (!(Get-Process -Id $process.Id -ErrorAction SilentlyContinue)) {
        throw "Failed to start Codex Audiobook AHK."
    }
    Write-Host "Restarted Codex Audiobook AHK with PID $($process.Id)."
}
