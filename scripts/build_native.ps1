$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$vswhere = Join-Path ${env:ProgramFiles(x86)} 'Microsoft Visual Studio\Installer\vswhere.exe'
$vsPath = & $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath
if (-not $vsPath) { throw 'Visual Studio C++ build tools were not found.' }
$vcvars = Join-Path $vsPath 'VC\Auxiliary\Build\vcvars64.bat'
Push-Location $projectRoot
try {
    New-Item -ItemType Directory -Force -Path build | Out-Null
    # No file operations are passed across shells; cmd is only used for the VS compiler environment.
    $compileCommand = '"' + $vcvars + '" >nul && cl /nologo /W4 /WX /LD /I firmware firmware\gate_config.c firmware\echo_fifo.c tests\native_gate.c /Fo:build\ /link /OUT:build\gate_native.dll /IMPLIB:build\gate_native.lib'
    & cmd /c $compileCommand
    if ($LASTEXITCODE -ne 0) { throw 'Native filter build failed.' }
} finally {
    Pop-Location
}
