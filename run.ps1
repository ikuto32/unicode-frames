param(
    [string]$InputVideo = 'assets\badapple.mp4',
    [string]$OutputDirectory = 'output',
    [ValidateSet('auto','numpy','faiss','cuda')][string]$Backend = 'auto',
    [ValidateSet('auto','stretch','fit')][string]$Normalization = 'auto',
    [switch]$AllStyles,
    [switch]$SkipFetch
)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$taskPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $taskPython)) { throw 'Run .\setup.ps1 first.' }
if (-not $SkipFetch) {
    $fetchArgs = @('-m', 'badapple_unicode', 'fetch')
    if ($AllStyles) { $fetchArgs += '--all-styles' }
    if ($InputVideo -ne 'assets\badapple.mp4') { $fetchArgs += '--no-video' }
    & $taskPython @fetchArgs
    if ($LASTEXITCODE -ne 0) { throw 'Asset download failed. See assets\manifest.json.' }
}
$dictionaryPath = if ($AllStyles) { 'cache\all-styles-dictionary' } else { 'cache\full-dictionary' }
if (-not (Test-Path -LiteralPath (Join-Path $dictionaryPath 'manifest.json'))) {
    & $taskPython -m badapple_unicode build --dictionary $dictionaryPath
    if ($LASTEXITCODE -ne 0) { throw 'Dictionary generation failed.' }
}
& $taskPython -m badapple_unicode run --input $InputVideo --dictionary $dictionaryPath --output $OutputDirectory --backend $Backend --normalization $Normalization
if ($LASTEXITCODE -ne 0) { throw 'Matching or video rendering failed.' }
