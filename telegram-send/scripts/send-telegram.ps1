[CmdletBinding(DefaultParameterSetName = 'File')]
param(
    [Parameter(ParameterSetName = 'File', Mandatory)]
    [string]$FilePath,
    [Parameter(ParameterSetName = 'File')]
    [string]$Caption,
    [Parameter(ParameterSetName = 'Message', Mandatory)]
    [string]$Message,
    [string]$ChatId,
    [string]$CredentialName = 'Telegram delivery (Orimise)',
    [string]$CollectionName = 'Agent Drop'
)

$ErrorActionPreference = 'Stop'
$base = Join-Path $env:USERPROFILE 'vaultwarden'
$credFile = Join-Path $base 'agent-cred.xml'
$caFile = Join-Path $base 'caddy-local-root.crt'
$server = 'https://localhost:8443'

function Fail([string]$Text) { throw $Text }

if (-not (Get-Command bw -ErrorAction SilentlyContinue)) { Fail 'Bitwarden CLI is not installed.' }
if (-not (Test-Path -LiteralPath $credFile)) { Fail 'Vaultwarden agent credential is not configured.' }
if (-not (Test-Path -LiteralPath $caFile)) { Fail 'Vaultwarden local CA is missing.' }

if ($PSCmdlet.ParameterSetName -eq 'File') {
    $resolvedFile = (Resolve-Path -LiteralPath $FilePath).Path
    $file = Get-Item -LiteralPath $resolvedFile
    if ($file.PSIsContainer) { Fail 'FilePath must refer to a file.' }
    if ($file.Length -gt 2GB) { Fail 'File is too large for automatic Telegram delivery.' }
}

$env:NODE_EXTRA_CA_CERTS = $caFile
$credential = Import-Clixml -LiteralPath $credFile
$env:BW_AGENT_PASSWORD = $credential.GetNetworkCredential().Password
try {
    $status = (& bw status 2>$null | ConvertFrom-Json).status
    if ($status -eq 'unauthenticated') {
        & bw config server $server 2>$null | Out-Null
        $session = (& bw login $credential.UserName --passwordenv BW_AGENT_PASSWORD --raw 2>$null | Out-String).Trim()
    } else {
        $session = (& bw unlock --passwordenv BW_AGENT_PASSWORD --raw 2>$null | Out-String).Trim()
    }
    if (-not $session) { Fail 'Could not unlock Vaultwarden.' }
} finally {
    $env:BW_AGENT_PASSWORD = $null
}

& bw sync --session $session 2>$null | Out-Null
$collections = & bw list collections --session $session 2>$null | ConvertFrom-Json
$collection = $collections | Where-Object { $_.name -eq $CollectionName } | Select-Object -First 1
if (-not $collection) { Fail "Vault collection '$CollectionName' was not found." }
$items = & bw list items --collectionid $collection.id --session $session 2>$null | ConvertFrom-Json
$item = $items | Where-Object { $_.name -eq $CredentialName } | Select-Object -First 1
if (-not $item) { Fail "Vault item '$CredentialName' was not found." }

$token = $item.login.password
if (-not $ChatId) { $ChatId = $item.login.username }
if (-not $token -or -not $ChatId) { Fail 'Telegram token or chat ID is missing from the vault item.' }
try {
    if ($PSCmdlet.ParameterSetName -eq 'Message') {
        $result = Invoke-RestMethod -Method Post -Uri "https://api.telegram.org/bot$token/sendMessage" -Body @{ chat_id = $ChatId; text = $Message }
    } else {
        $curlArgs = @(
            '--silent', '--show-error', '--fail-with-body',
            '-X', 'POST', "https://api.telegram.org/bot$token/sendDocument",
            '-F', "chat_id=$ChatId",
            '-F', "document=@$resolvedFile"
        )
        if ($Caption) { $curlArgs += @('-F', "caption=$Caption") }
        $raw = & curl.exe @curlArgs
        if ($LASTEXITCODE -ne 0) { Fail 'Telegram document upload failed. Files above the Bot API limit must be split before sending.' }
        $result = $raw | ConvertFrom-Json
    }
} finally {
    $token = $null
    $session = $null
}
if (-not $result.ok) { Fail 'Telegram API did not accept the request.' }
[pscustomobject]@{
    Ok = $true
    MessageId = $result.result.message_id
    ChatId = $result.result.chat.id
    FileName = if ($PSCmdlet.ParameterSetName -eq 'File') { $file.Name } else { $null }
}