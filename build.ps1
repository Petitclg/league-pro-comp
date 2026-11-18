param(
    [string]$Version = "",       # e.g. 1.2.0 (the tag without "v"); default: the version in backend/app/_version.py
    [string]$OutDir = "",        # where LoLProComp.exe (and LoLProComp-Setup.exe) is written; default: backend/dist
    [switch]$SkipFrontend,       # reuse frontend/dist
    [switch]$Installer           # also build LoLProComp-Setup.exe with Inno Setup 6 (installer\LoLProComp.iss)
)
# Builds dist\LoLProComp.exe: the front-end bundled with the API and the desktop launcher.
# With -Installer, also wraps it in dist\LoLProComp-Setup.exe (per-user install, see installer\LoLProComp.iss).
$ErrorActionPreference = "Stop"
$root = $PSScriptRoot
$versionFile = Join-Path $root "backend\app\_version.py"
$original = [IO.File]::ReadAllText($versionFile)
if (-not $OutDir) { $OutDir = Join-Path $root "backend\dist" }
$OutDir = [IO.Path]::GetFullPath($ExecutionContext.SessionState.Path.GetUnresolvedProviderPathFromPSPath($OutDir))  # relative to the caller
$work = Join-Path ([IO.Path]::GetTempPath()) ("lolpro-build-" + [Guid]::NewGuid().ToString("N"))

function Find-Iscc {
    $command = Get-Command "ISCC.exe" -ErrorAction SilentlyContinue
    if ($command) { return $command.Source }
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
        "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
    )
    foreach ($key in "HKLM:\SOFTWARE\WOW6432Node", "HKLM:\SOFTWARE", "HKCU:\SOFTWARE") {
        $entry = Get-ItemProperty "$key\Microsoft\Windows\CurrentVersion\Uninstall\Inno Setup 6_is1" -ErrorAction SilentlyContinue
        if ($entry -and $entry.InstallLocation) { $candidates += Join-Path $entry.InstallLocation "ISCC.exe" }
    }
    return $candidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
}

if ($Installer) {  # fail before the long build rather than after it
    $iscc = Find-Iscc
    if (-not $iscc) {
        throw "ISCC.exe (Inno Setup 6) not found: install it from https://jrsoftware.org/isdl.php (or 'choco install innosetup') or add it to PATH."
    }
    $appVersion = if ($Version) { $Version } else { [regex]::Match($original, '__version__\s*=\s*"([^"]+)"').Groups[1].Value }
}

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
    & $python -m PyInstaller --noconfirm --clean --onefile --noconsole --name LoLProComp --icon "$root\backend\assets\icon.ico" `
        --add-data "$root\frontend\dist;static" `
        --exclude-module pytest --exclude-module matplotlib --exclude-module numpy `
        --exclude-module sklearn --exclude-module scipy --exclude-module pandas `
        --distpath $OutDir --workpath "$work\build" --specpath "$work\spec" launcher.py
    $exit = $LASTEXITCODE
    $ErrorActionPreference = "Stop"
    if ($exit) { throw "PyInstaller failed" }
    Pop-Location
    Write-Host "Built $OutDir\LoLProComp.exe"

    if ($Installer) {
        $ErrorActionPreference = "Continue"
        & $iscc /Qp "/DAppVersion=$appVersion" "/DSourceExe=$OutDir\LoLProComp.exe" "/O$OutDir" `
            (Join-Path $root "installer\LoLProComp.iss")
        $exit = $LASTEXITCODE
        $ErrorActionPreference = "Stop"
        if ($exit) { throw "Inno Setup failed" }
        Write-Host "Built $OutDir\LoLProComp-Setup.exe"
    }
}
finally {
    [IO.File]::WriteAllText($versionFile, $original)  # a build never leaves a modified version behind
    if (Test-Path $work) { Remove-Item -Recurse -Force $work }
}
