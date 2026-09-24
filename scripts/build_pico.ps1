$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$cmake = Join-Path $env:USERPROFILE '.pico-sdk/cmake/v4.3.4/bin/cmake.exe'
if (-not (Test-Path -LiteralPath $cmake)) {
    $cmake = (Get-Command cmake -ErrorAction Stop).Source
}

Push-Location (Join-Path $projectRoot 'firmware')
try {
    & $cmake --preset pico
    if ($LASTEXITCODE -ne 0) { throw 'Pico CMake configuration failed.' }
    & $cmake --build --preset pico --parallel
    if ($LASTEXITCODE -ne 0) { throw 'Pico firmware build failed.' }
    Write-Host "UF2: $projectRoot/build/pico/i2c_gate.uf2"
} finally {
    Pop-Location
}
