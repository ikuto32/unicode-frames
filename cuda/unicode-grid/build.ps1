$ErrorActionPreference = 'Stop'
$cudaRoot = $env:CUDA_PATH
if (-not $cudaRoot) { $cudaRoot = 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v13.4' }
$vsWhere = 'C:\Program Files (x86)\Microsoft Visual Studio\Installer\vswhere.exe'
$vsRoot = (& $vsWhere -latest -products '*' -property installationPath | Select-Object -First 1).Trim()
$setup = Join-Path $vsRoot 'VC\Auxiliary\Build\vcvars64.bat'
$compiler = Join-Path $cudaRoot 'bin\nvcc.exe'
$source = Join-Path $PSScriptRoot 'match.cu'
$buildRoot = Join-Path $PSScriptRoot 'build'
New-Item -ItemType Directory -Path $buildRoot -Force | Out-Null
$identity = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLower().Substring(0,16)
$library = Join-Path $buildRoot "unicode_match_$identity.dll"
if (-not (Test-Path -LiteralPath $library)) {
    $compile = 'call "{0}" >nul && "{1}" -std=c++17 -O3 -arch=sm_89 --shared -Xcompiler=/MD -Xcompiler=/EHsc -Xcompiler=/utf-8 -DWIN32_LEAN_AND_MEAN -DNOMINMAX "{2}" -o "{3}" -lcublas' -f $setup,$compiler,$source,$library
    Push-Location $buildRoot
    try { & cmd.exe /d /s /c $compile; if ($LASTEXITCODE -ne 0) { throw 'Unicode CUDA compilation failed' } }
    finally { Pop-Location }
}
@{library=$library;cuda_root=$cudaRoot;architecture='sm_89';integer_distances=$true;source_sha256=(Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToLower()} | ConvertTo-Json | Set-Content (Join-Path $buildRoot 'current.json') -Encoding utf8
Get-Content (Join-Path $buildRoot 'current.json')
