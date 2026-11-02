# Deploy web/ (including the generated web/data) to Cloudflare as a Worker with static assets,
# served only on a custom domain (the domain must be a zone in the same Cloudflare account).
# Static asset requests are not billed or limited. First time: run `npx wrangler@4.151.2 login` once.
param([string]$Domain = 'unicode-frames.ikutotest.xyz', [string]$Name = 'unicode-frames', [string]$Wrangler = 'wrangler@4.151.2')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$web = Join-Path $root 'web'
if (-not (Test-Path -LiteralPath (Join-Path $web 'data\meta.json'))) {
    throw 'web/data is missing. Run: python -m badapple_unicode web-export'
}
$stage = Join-Path ([IO.Path]::GetTempPath()) ("unicode-frames-cloudflare-" + [guid]::NewGuid().ToString('N'))
$site = Join-Path $stage 'site'
New-Item -ItemType Directory -Path $stage | Out-Null
Copy-Item -LiteralPath $web -Destination $site -Recurse
foreach ($file in @('.gitignore', 'test-video.mp4')) {
    $path = Join-Path $site $file
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
# Cloudflare rejects asset files over 25 MiB.
$large = Get-ChildItem -LiteralPath $site -Recurse -File | Where-Object { $_.Length -gt 25MB }
if ($large) { throw "Files over 25 MiB: $($large.Name -join ', '). Re-run web-export." }
# meta.json names the current build and must always be re-checked. The app keeps the
# data files in Cache Storage itself, so they need no long HTTP caching.
@'
/data/meta.json
  Cache-Control: no-cache
'@ | Set-Content -LiteralPath (Join-Path $site '_headers') -Encoding utf8
@"
{
  "name": "$Name",
  "compatibility_date": "2026-10-15",
  "assets": { "directory": "./site" },
  "routes": [{ "pattern": "$Domain", "custom_domain": true }],
  "workers_dev": false,
  "preview_urls": false
}
"@ | Set-Content -LiteralPath (Join-Path $stage 'wrangler.jsonc') -Encoding utf8
try {
    npx -y $Wrangler deploy --config (Join-Path $stage 'wrangler.jsonc')
    if ($LASTEXITCODE -ne 0) { throw 'wrangler deploy failed' }
}
finally {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
