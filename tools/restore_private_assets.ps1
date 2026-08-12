param(
    [switch]$Force
)

$ErrorActionPreference = 'Stop'

$repo = 'lyh-123283/video-drama-agent-assets'
$tag = 'v2026.08.12-assets'
$assetName = 'video-drama-agent-assets-2026-08-12.zip'
$expectedSha256 = 'B147F54998427D5D1379A4F1CCE17C5FC57DD033A4247EB8DE18CE322888B707'

$projectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$workDir = Join-Path ([System.IO.Path]::GetTempPath()) ('video-drama-assets-' + [guid]::NewGuid().ToString('N'))
$archivePath = Join-Path $workDir $assetName
$extractDir = Join-Path $workDir 'extracted'

if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
    throw 'GitHub CLI (gh) is required. Install it, run gh auth login, and retry.'
}

if (-not $Force) {
    $answer = Read-Host 'This will merge the private .env and output assets into this checkout. Continue? (y/N)'
    if ($answer -notmatch '^(?i)y(es)?$') {
        Write-Host 'Cancelled.'
        exit 0
    }
}

New-Item -ItemType Directory -Path $workDir -Force | Out-Null
try {
    & gh auth status --hostname github.com 2>&1 | Out-Host
    if ($LASTEXITCODE -ne 0) {
        throw 'GitHub CLI is not authenticated. Run gh auth login with an account that can read the private assets repository.'
    }

    Write-Host "Downloading private asset bundle from $repo ..."
    & gh release download $tag --repo $repo --pattern $assetName --dir $workDir --clobber
    if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $archivePath)) {
        throw 'The private release could not be downloaded. Confirm repository access and the release tag.'
    }

    $actualSha256 = (Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actualSha256 -ne $expectedSha256) {
        throw "Asset bundle checksum mismatch. Expected $expectedSha256, got $actualSha256."
    }
    Write-Host 'Checksum verified.'

    Expand-Archive -LiteralPath $archivePath -DestinationPath $extractDir -Force
    $sourceEnv = Join-Path $extractDir '.env'
    $sourceOutput = Join-Path $extractDir 'output'
    if (-not (Test-Path -LiteralPath $sourceEnv) -or -not (Test-Path -LiteralPath $sourceOutput)) {
        throw 'The asset bundle is missing .env or output/. Refusing to merge an incomplete bundle.'
    }

    Copy-Item -LiteralPath $sourceEnv -Destination (Join-Path $projectRoot '.env') -Force

    $projectOutput = Join-Path $projectRoot 'output'
    Get-ChildItem -LiteralPath $sourceOutput -Recurse -File | ForEach-Object {
        $relative = $_.FullName.Substring($sourceOutput.Length).TrimStart('\\', '/')
        $destination = Join-Path $projectOutput $relative
        New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
        Copy-Item -LiteralPath $_.FullName -Destination $destination -Force
    }

    Write-Host 'Private assets restored.'
    Write-Host "Project root: $projectRoot"
    Write-Host 'The existing output/ directory was merged; files outside the bundle were not removed.'
    Write-Host 'Start with start_api_server.bat or 启动.bat.'
}
finally {
    if (Test-Path -LiteralPath $workDir) {
        Remove-Item -LiteralPath $workDir -Recurse -Force -ErrorAction SilentlyContinue
    }
}
