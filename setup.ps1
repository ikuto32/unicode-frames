param([string]$Python = '')
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
if (-not (Test-Path -LiteralPath '.venv\Scripts\python.exe')) {
    if (-not $Python) {
        if (Get-Command py -ErrorAction SilentlyContinue) { $Python = 'py' }
        else { $Python = 'python' }
    }
    & $Python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Failed to create .venv; pass -Python with a Python 3.11+ executable.' }
}
& '.\.venv\Scripts\python.exe' -m ensurepip --upgrade
if ($LASTEXITCODE -ne 0) { throw 'Failed to install pip.' }
& '.\.venv\Scripts\python.exe' -m pip install -e '.[cpu,test]'
if ($LASTEXITCODE -ne 0) { throw 'Failed to install dependencies.' }
Write-Output 'Ready. Run .\run.ps1 to fetch fonts, build the dictionary and encode the video.'
