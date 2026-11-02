# Publish web/ (including the generated web/data) to the gh-pages branch.
# The data files are build output, so they live only on gh-pages, never on main.
param([string]$Remote = '', [string]$Branch = 'gh-pages')
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$web = Join-Path $root 'web'
if (-not (Test-Path -LiteralPath (Join-Path $web 'data\meta.json'))) {
    throw 'web/data is missing. Run: python -m badapple_unicode web-export'
}
if (-not $Remote) { $Remote = (git -C $root remote get-url origin).Trim() }
$stage = Join-Path ([IO.Path]::GetTempPath()) ("unicode-frames-pages-" + [guid]::NewGuid().ToString('N'))
Copy-Item -LiteralPath $web -Destination $stage -Recurse
# Local test files never go online.
foreach ($name in @('.gitignore', 'test-video.mp4')) {
    $path = Join-Path $stage $name
    if (Test-Path -LiteralPath $path) { Remove-Item -LiteralPath $path -Force }
}
# Skip Jekyll: the site is plain static files.
New-Item -ItemType File -Path (Join-Path $stage '.nojekyll') | Out-Null
try {
    git -C $stage init -q -b $Branch
    # Fetch the published tip (if any) so unchanged data files are not uploaded again.
    git -C $stage fetch -q --depth 1 $Remote "${Branch}:refs/remotes/origin/${Branch}" 2>$null
    git -C $stage add -A
    git -C $stage commit -q -m 'Publish Unicode Frames'
    if ($LASTEXITCODE -ne 0) { throw 'git commit failed' }
    # A single fresh commit each time keeps the 250 MB of data out of the branch history.
    git -C $stage push --force $Remote "${Branch}:${Branch}"
    if ($LASTEXITCODE -ne 0) { throw 'git push failed' }
}
finally {
    Remove-Item -LiteralPath $stage -Recurse -Force
}
Write-Output "Pushed to $Branch. On GitHub: Settings > Pages > Build and deployment > Deploy from a branch > $Branch / (root)."
