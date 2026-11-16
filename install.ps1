# Install (or refresh) the chatgpt-web-local-runtime deployment home on Windows.
#
#   powershell -ExecutionPolicy Bypass -File .\install.ps1 [-Target DIR]
#                                                            default: %USERPROFILE%\.dsh-mcp-gateway
#
# Copies dsh-mcp-gateway\ to the target, then performs the standard install:
# Python venv + editable gateway, npm DSH runtime, env.conf seed.
#
# Idempotent — re-running refreshes code only. It never overwrites:
#   env.conf, state\, home\profiles\web\* (your profile edits),
#   bin\cloudflared.exe, .venv, node_modules, *.resolved.cordis.yml.
param([string]$Target = "$env:USERPROFILE\.dsh-mcp-gateway")
$ErrorActionPreference = 'Stop'
$Src = Join-Path $PSScriptRoot 'dsh-mcp-gateway'

New-Item -ItemType Directory -Force $Target | Out-Null
$Target = (Resolve-Path $Target).Path

function Sync-Dir([string]$from, [string]$to, [string[]]$extraArgs) {
    # robocopy exit codes <8 are success (bitfield of what was copied).
    & robocopy $from $to @extraArgs | Out-Null
    if ($LASTEXITCODE -ge 8) { throw "robocopy failed ($LASTEXITCODE): $from -> $to" }
    $script:LASTEXITCODE = 0
}

Write-Host "[1/4] sync $Src -> $Target"
# Pure-code dirs mirror with /MIR so removed files disappear. Artifacts
# (egg-info, __pycache__, node_modules, *.resolved) are excluded from both
# copy and purge, and rebuilt by the steps below.
foreach ($d in 'src','deploy','worker','devin',
              'dsh-bridge-plugin','dsh-remote-worker-plugin',
              'dsh-shell-session-plugin','dsh-task-state-plugin') {
    Sync-Dir "$Src\$d" "$Target\$d" @('/MIR','/XD','__pycache__','*.egg-info','node_modules','/XF','*.resolved.cordis.yml')
}
# Top-level files are all ours — always refresh.
foreach ($f in 'pyproject.toml','LICENSE','README.md','env.conf.example',
               'gateway-local-changes.patch','start.sh','start.ps1') {
    Copy-Item "$Src\$f" "$Target\$f"
}
# Profile files may carry user edits (devin merge) — /XC /XN /XO skips any
# file that exists at the target, so this seeds missing files only.
Sync-Dir "$Src\home" "$Target\home" @('/E','/XC','/XN','/XO')
New-Item -ItemType Directory -Force "$Target\bin","$Target\state" | Out-Null

Write-Host '[2/4] Python gateway (.venv + editable install)'
$py = Get-Command py -ErrorAction SilentlyContinue
if (-not (Test-Path "$Target\.venv\Scripts\pip.exe")) {
    if ($py) { & py -3.12 -m venv "$Target\.venv" }
    else {
        $python = Get-Command python -ErrorAction SilentlyContinue
        if (-not $python) { throw 'Python 3.12 not found (need py -3.12 or python on PATH)' }
        & python -m venv "$Target\.venv"
    }
}
Push-Location $Target; try { & "$Target\.venv\Scripts\pip.exe" install -q -e ".[server]" } finally { Pop-Location }

Write-Host '[3/4] DSH runtime (npm)'
Push-Location "$Target\deploy\dsh-runtime"; try { & npm install --no-fund --no-audit } finally { Pop-Location }

Write-Host '[4/4] env.conf'
if (Test-Path "$Target\env.conf") {
    Write-Host '  exists - kept'
} else {
    Copy-Item "$Target\env.conf.example" "$Target\env.conf"
    Write-Host '  seeded from env.conf.example - fill in the secrets'
}

Write-Host ''
Write-Host "Installed to $Target"
Write-Host ''
Write-Host 'Next:'
Write-Host "  1. edit $Target\env.conf            (PIN, WORKER_PUBLIC_URL, UPSTREAM_ADMIN_SECRET, PROXY?)"
Write-Host '  2. deploy worker\_worker.js on Cloudflare   (KV UPSTREAM + secret ADMIN_SECRET)'
Write-Host "  3. put cloudflared.exe at $Target\bin\cloudflared.exe  (or set CLOUDFLARED in env.conf)"
Write-Host "  4. powershell -ExecutionPolicy Bypass -File $Target\start.ps1"
