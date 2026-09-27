[CmdletBinding()]
param(
    [Parameter(Mandatory)] [string]$FilePath,
    [string]$Caption,
    [int]$PartSizeMB = 45
)
$ErrorActionPreference = 'Stop'
$source = Get-Item -LiteralPath (Resolve-Path -LiteralPath $FilePath)
$sender = Join-Path $PSScriptRoot 'send-telegram.ps1'
$partSize = $PartSizeMB * 1MB
$tempDir = Join-Path ([IO.Path]::GetTempPath()) ("telegram-parts-" + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $tempDir | Out-Null
try {
    $buffer = New-Object byte[] $partSize
    $stream = [IO.File]::OpenRead($source.FullName)
    try {
        $index = 1
        $parts = @()
        while (($read = $stream.Read($buffer, 0, $buffer.Length)) -gt 0) {
            $partPath = Join-Path $tempDir ("{0}.part{1:D3}" -f $source.Name, $index)
            $out = [IO.File]::Create($partPath)
            try { $out.Write($buffer, 0, $read) } finally { $out.Dispose() }
            $parts += $partPath
            $index++
        }
    } finally { $stream.Dispose() }
    $hash = (Get-FileHash -LiteralPath $source.FullName -Algorithm SHA256).Hash
    $intro = "${Caption}`nFile duoc chia thanh $($parts.Count) phan. Tai du cac phan, ghep theo thu tu part001... roi doi ten thanh $($source.Name). SHA256: $hash"
    & powershell -File $sender -Message $intro | Out-Null
    $messageIds = @()
    foreach ($part in $parts) {
        $result = & powershell -File $sender -FilePath $part -Caption (Split-Path $part -Leaf)
        $messageIds += ($result | Out-String)
    }
    [pscustomobject]@{ Ok = $true; Parts = $parts.Count; FileName = $source.Name; Sha256 = $hash }
} finally {
    Remove-Item -LiteralPath $tempDir -Recurse -Force -ErrorAction SilentlyContinue
}