param(
    [string]$Version = "",       # e.g. 1.2.0 (the tag without "v"); default: the version in backend/app/_version.py
    [string]$OutDir = "",        # where LoLProComp.exe is written; default: backend/dist
    [switch]$SkipFrontend        # reuse frontend/dist
)
# Builds dist\LoLProComp.exe: the front-end bundled with the API and the desktop launcher.
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$versionFile = Join-Path $root "backend\app\_version.py"
$original = [IO.File]::ReadAllText($versionFile)
if (-not $OutDir) { $OutDir = Join-Path $root "backend\dist" }
$work = Join-Path ([IO.Path]::GetTempPath()) ("lolpro-build-" + [Guid]::NewGuid().ToString("N"))

try {
    if ($Version) { [IO.File]::WriteAllText($versionFile, "__version__ = `"$Version`"`n") }
    if (-not $SkipFrontend) {
        Push-Location (Join-Path $root "frontend")
        npm run build
        if ($LASTEXITCODE) { throw "front-end build failed" }
        Pop-Location
    }
    Push-Location (Join-Path $root "backend")
    $python = if (Test-Path ".\.venv\Scripts\python.exe") { ".\.venv\Scripts\python.exe" } else { "python" }
    $ErrorActionPreference = "Continue"  # PyInstaller logs to stderr; its exit code is what counts
    & $python -m PyInstaller --noconfirm --clean --onefile --noconsole --name LoLProComp `
        --add-data "$root\frontend\dist;static" `
        --exclude-module pytest --exclude-module matplotlib --exclude-module numpy `
        --distpath $OutDir --workpath "$work\build" --specpath "$work\spec" launcher.py
    $exit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($exit) { throw "PyInstaller failed" }
    Pop-Location
    Write-Host "Built $OutDir\LoLProComp.exe"
}
finally {
    [IO.File]::WriteAllText($versionFile, $original)  # a build never leaves a modified version behind
    if (Test-Path $work) { Remove-Item -Recurse -Force $work }
}
