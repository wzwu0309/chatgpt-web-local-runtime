# Boot the whole ChatGPT -> DSH bridge stack on Windows:
#   DSH web host :3081  ->  cloudflared quick tunnel  ->  gateway :8766
# If a Cloudflare Worker facade is configured in env.conf, the fresh tunnel
# URL is published to it and the public origin stays fixed.
#
# Usage:  powershell -ExecutionPolicy Bypass -File .\start.ps1
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

# --- env.conf (KEY=VALUE lines, # comments) ------------------------------------
$confPath = Join-Path $PSScriptRoot 'env.conf'
if (Test-Path $confPath) {
    Get-Content $confPath | ForEach-Object {
        if ($_ -match '^\s*([^#\s][^=]*)=(.*)$') {
            [Environment]::SetEnvironmentVariable($Matches[1].Trim(), $Matches[2].Trim(), 'Process')
        }
    }
}

$GwHome        = $PSScriptRoot
$DSH_HOME_DIR  = if ($env:DSH_HOME_DIR)  { $env:DSH_HOME_DIR }  else { "$GwHome\home" }
$DSH_HOST_PORT = if ($env:DSH_HOST_PORT) { $env:DSH_HOST_PORT } else { 3081 }
$GATEWAY_PORT  = if ($env:GATEWAY_PORT)  { $env:GATEWAY_PORT }  else { 8766 }
$WORKSPACE     = if ($env:WORKSPACE)     { $env:WORKSPACE }     else { "$env:USERPROFILE\data\workspace" }
$WORKSPACE_ALLOWED_ROOT = if ($env:WORKSPACE_ALLOWED_ROOT) { $env:WORKSPACE_ALLOWED_ROOT } else { "$env:USERPROFILE\data" }
$CLOUDFLARED   = if ($env:CLOUDFLARED)   { $env:CLOUDFLARED }   else { "$GwHome\bin\cloudflared.exe" }
$DSH_BIN       = "$GwHome\deploy\dsh-runtime\node_modules\.bin\dsh.cmd"
$PATCH_TMPL    = "$GwHome\deploy\dsh\chatgpt-bridge.windows.cordis.yml"
$STATE_DIR     = "$GwHome\state"
$LOG_DIR       = $env:TEMP

if (-not $env:DSH_MCP_GATEWAY_ADMIN_PIN) {
    Write-Error 'env.conf must set DSH_MCP_GATEWAY_ADMIN_PIN'; exit 1
}
if (-not (Test-Path $WORKSPACE_ALLOWED_ROOT -PathType Container)) {
    Write-Error "WORKSPACE_ALLOWED_ROOT '$WORKSPACE_ALLOWED_ROOT' does not exist - create it (New-Item -ItemType Directory -Force '$WORKSPACE_ALLOWED_ROOT') or set WORKSPACE_ALLOWED_ROOT in env.conf"; exit 1
}
if (-not (Test-Path $WORKSPACE -PathType Container)) {
    Write-Error "WORKSPACE '$WORKSPACE' does not exist - create it (New-Item -ItemType Directory -Force '$WORKSPACE') or set WORKSPACE in env.conf"; exit 1
}

# Materialize the patch: plugin `name:` fields need real absolute paths.
$patchYml = "$GwHome\deploy\dsh\chatgpt-bridge.windows.resolved.cordis.yml"
(Get-Content -Raw $PATCH_TMPL) -replace '<DSH_GW_HOME>', ($GwHome -replace '\\', '/') |
    Set-Content -NoNewline $patchYml

function Test-Bridge {
    try {
        Invoke-RestMethod -TimeoutSec 2 "http://127.0.0.1:$DSH_HOST_PORT/api/chatgpt-bridge/revision" | Out-Null
        return $true
    } catch { return $false }
}

function Stop-ByCommandLine([string]$pattern) {
    Get-CimInstance Win32_Process |
        Where-Object { $_.CommandLine -match $pattern } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

Write-Host "[1/4] DSH web host :$DSH_HOST_PORT"
if (-not (Test-Bridge)) {
    $env:DSH_HOME = $DSH_HOME_DIR
    $env:DSH_TELEMETRY_DISABLED = '1'
    $env:DSH_MCP_PUBLIC_BASE_URL = if ($env:WORKER_PUBLIC_URL) { $env:WORKER_PUBLIC_URL } else { 'https://placeholder.invalid' }
    $env:WORKSPACE_ALLOWED_ROOT = $WORKSPACE_ALLOWED_ROOT
    $env:PATH = "$(Split-Path $DSH_BIN);$env:PATH"
    Start-Process -FilePath $DSH_BIN -WindowStyle Hidden `
        -ArgumentList '--profile','web','--patch',$patchYml,'--host','127.0.0.1','--port',"$DSH_HOST_PORT",'--no-open' `
        -WorkingDirectory $WORKSPACE `
        -RedirectStandardOutput "$LOG_DIR\dsh-web-host.log" -RedirectStandardError "$LOG_DIR\dsh-web-host.err.log"
    for ($i = 0; $i -lt 30; $i++) { if (Test-Bridge) { break }; Start-Sleep 2 }
}
if (Test-Bridge) { Write-Host '  ok' } else { Write-Error "  FAILED - see $LOG_DIR\dsh-web-host.log"; exit 1 }

Write-Host "[2/4] cloudflared quick tunnel -> :$GATEWAY_PORT"
Stop-ByCommandLine "cloudflared.*--url http://127\.0\.0\.1:$GATEWAY_PORT"
Start-Sleep 1
Clear-Content "$LOG_DIR\cloudflared.log" -ErrorAction SilentlyContinue
# http2 (TCP 7844) survives networks that throttle UDP/QUIC.
Start-Process -FilePath $CLOUDFLARED -WindowStyle Hidden `
    -ArgumentList 'tunnel','--url',"http://127.0.0.1:$GATEWAY_PORT",'--protocol','http2','--no-autoupdate' `
    -RedirectStandardOutput "$LOG_DIR\cloudflared.log" -RedirectStandardError "$LOG_DIR\cloudflared.err.log"
$TUNNEL_URL = ''
for ($i = 0; $i -lt 30; $i++) {
    $m = Select-String -Path "$LOG_DIR\cloudflared.log" -Pattern 'https://[a-z0-9-]+\.trycloudflare\.com' -AllMatches -ErrorAction SilentlyContinue |
        Select-Object -First 1
    if ($m) { $TUNNEL_URL = $m.Matches[0].Value; break }
    Start-Sleep 2
}
if (-not $TUNNEL_URL) { Write-Error "  FAILED - see $LOG_DIR\cloudflared.log"; exit 1 }
Write-Host "  $TUNNEL_URL"

Write-Host '[3/4] public origin'
$curlProxy = @()
if ($env:PROXY) { $curlProxy = @('--proxy', $env:PROXY) }
if ($env:WORKER_PUBLIC_URL -and $env:UPSTREAM_ADMIN_SECRET) {
    $PUBLIC_URL = $env:WORKER_PUBLIC_URL
    & curl.exe -sf --max-time 15 @curlProxy -X PUT "$PUBLIC_URL/_upstream" `
        -H "Authorization: Bearer $env:UPSTREAM_ADMIN_SECRET" -d $TUNNEL_URL | Out-Null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "  published to worker facade: $PUBLIC_URL"
    } else {
        Write-Error '  FAILED: worker upstream registration (check WORKER_PUBLIC_URL/PROXY/ADMIN_SECRET)'; exit 1
    }
} else {
    $PUBLIC_URL = $TUNNEL_URL
    Write-Host '  no facade configured - using tunnel URL directly'
}

Write-Host "[4/4] gateway :$GATEWAY_PORT (public=$PUBLIC_URL)"
Stop-ByCommandLine "dsh-mcp-gateway.*--dsh-harness-url http://127\.0\.0\.1:$DSH_HOST_PORT"
Start-Sleep 1
$gatewayExe = "$GwHome\.venv\Scripts\dsh-mcp-gateway.exe"
$tunnelHost = $TUNNEL_URL -replace '^https://', ''
Start-Process -FilePath $gatewayExe -WindowStyle Hidden `
    -ArgumentList '--dsh-harness-url',"http://127.0.0.1:$DSH_HOST_PORT",'--public-base-url',$PUBLIC_URL,'--allowed-host',$tunnelHost,'--state-dir',$STATE_DIR `
    -RedirectStandardOutput "$LOG_DIR\dsh-mcp-gateway.log" -RedirectStandardError "$LOG_DIR\dsh-mcp-gateway.err.log"
Start-Sleep 4
& curl.exe -sf --max-time 15 @curlProxy "$PUBLIC_URL/.well-known/oauth-authorization-server" | Out-Null
if ($LASTEXITCODE -eq 0) { Write-Host '  ok' } else { Write-Host '  warn: public metadata not reachable yet (propagation)' }

Write-Host ''
Write-Host "MCP connector URL : $PUBLIC_URL/mcp"
Write-Host "Owner PIN         : $env:DSH_MCP_GATEWAY_ADMIN_PIN"
