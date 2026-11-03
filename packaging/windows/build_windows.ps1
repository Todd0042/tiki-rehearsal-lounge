param(
    [string]$Version = ""
)
# Tiki Rehearsal Lounge - Windows Standalone Packaging Builder (PowerShell)
$ErrorActionPreference = "Continue"

$RawScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
if ($RawScriptDir -match '^[a-zA-Z]:' -or $RawScriptDir -match '^\\\\') {
    $ScriptDir = (Get-Item $RawScriptDir).FullName
} else {
    $ScriptDir = (Get-Item (Join-Path (Get-Location).Path $RawScriptDir)).FullName
}
$RepoDir = (Get-Item "$ScriptDir\..\..").FullName
Set-Location $ScriptDir

Write-Host "=================================================================" -ForegroundColor Cyan
Write-Host "TIKI REHEARSAL LOUNGE - WINDOWS STANDALONE PACKAGING BUILDER" -ForegroundColor Cyan
Write-Host "=================================================================" -ForegroundColor Cyan

# 0. Clean prior dist and build directories
Write-Host "Cleaning prior dist and build directories..." -ForegroundColor Yellow
Stop-Process -Name "TikiRehearsalLounge" -Force -ErrorAction SilentlyContinue
Start-Sleep -Milliseconds 300

if (Test-Path "$ScriptDir\dist") { Remove-Item -Path "$ScriptDir\dist" -Recurse -Force -ErrorAction SilentlyContinue }
if (Test-Path "$ScriptDir\build") { Remove-Item -Path "$ScriptDir\build" -Recurse -Force -ErrorAction SilentlyContinue }
Get-ChildItem -Path "$RepoDir" -Filter "__pycache__" -Recurse | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

# 1. Locate Python
$PythonBin = "python"
$HasSystemPython = $false
try {
    $ver = & python --version 2>$null
    if ($LASTEXITCODE -eq 0 -and $ver) { $HasSystemPython = $true }
} catch {
    $HasSystemPython = $false
}

Write-Host "`n[1/5] Verifying Python version..." -ForegroundColor Yellow
& $PythonBin --version

Write-Host "`n[2/5] Installing/Upgrading PyInstaller and build dependencies..." -ForegroundColor Yellow
& $PythonBin -m pip install --upgrade pip setuptools wheel
& $PythonBin -m pip install -r "$RepoDir\requirements.txt" pyinstaller
if ($LASTEXITCODE -ne 0) {
    Write-Host "`n[ERROR] Pip dependency installation failed." -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "`n[3/5] Staging Windows 64-bit helper binaries (ffmpeg, ffprobe, ffplay)..." -ForegroundColor Yellow
& $PythonBin "$ScriptDir\fetch_windows_binaries.py" "$ScriptDir\bin"

Write-Host "`n[4/5] Building TikiRehearsalLounge standalone bundle via PyInstaller..." -ForegroundColor Yellow

$LocalBuild = "C:\TikiRehearsalBuild"
$LocalDist = "$LocalBuild\dist"
$LocalWork = "$LocalBuild\work"

if (Test-Path $LocalBuild) { Remove-Item -Path $LocalBuild -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path $LocalDist | Out-Null
New-Item -ItemType Directory -Force -Path $LocalWork | Out-Null

& $PythonBin -m PyInstaller -y "$ScriptDir\tiki_rehearsal_lounge.spec" --distpath "$LocalDist" --workpath "$LocalWork"

if (-not (Test-Path "$LocalDist\TikiRehearsalLounge\TikiRehearsalLounge.exe")) {
    Write-Host "`n[ERROR] PyInstaller build failed! TikiRehearsalLounge.exe not found." -ForegroundColor Red
    exit 1
}

# Copy bundle into local script dist
if (Test-Path "$ScriptDir\dist") { Remove-Item -Path "$ScriptDir\dist" -Recurse -Force -ErrorAction SilentlyContinue }
New-Item -ItemType Directory -Force -Path "$ScriptDir\dist" | Out-Null
Copy-Item -Path "$LocalDist\TikiRehearsalLounge" -Destination "$ScriptDir\dist\" -Recurse -Force

# Stage helper binaries into both bin and _internal\bin
if (Test-Path "$ScriptDir\bin") {
    $targetBins = @(
        "$ScriptDir\dist\TikiRehearsalLounge\bin",
        "$ScriptDir\dist\TikiRehearsalLounge\_internal\bin"
    )
    foreach ($tb in $targetBins) {
        if (-not (Test-Path $tb)) { New-Item -ItemType Directory -Force -Path $tb | Out-Null }
        Copy-Item "$ScriptDir\bin\*.exe" "$tb\" -Force -ErrorAction SilentlyContinue
    }
}

Write-Host "`n[5/5] Packaging release archives..." -ForegroundColor Yellow

# Parse version
$AppVersion = ""
if ($Version) {
    $AppVersion = $Version.TrimStart('v')
} elseif ($env:STUDIO_VERSION) {
    $AppVersion = $env:STUDIO_VERSION.TrimStart('v')
} else {
    $AppVersion = "1.0.0"
}
Write-Host "Target Version: $AppVersion" -ForegroundColor Cyan

# Always generate the turnkey Portable release ZIP
$ZipSrcDir = "$ScriptDir\dist\TikiRehearsalLounge"
$ZipDst = "$ScriptDir\dist\TikiRehearsalLounge-Windows-v$AppVersion-Portable.zip"
if (Test-Path $ZipDst) { Remove-Item $ZipDst -Force }

$sevenZip = "C:\Program Files\7-Zip\7z.exe"
if (-not (Test-Path $sevenZip)) {
    $cmd7z = Get-Command 7z -ErrorAction SilentlyContinue
    if ($cmd7z) { $sevenZip = $cmd7z.Source }
}

if (Test-Path $sevenZip) {
    Write-Host "Compressing Portable ZIP via 7-Zip..." -ForegroundColor Yellow
    & $sevenZip a -tzip -mx=7 "$ZipDst" "$ZipSrcDir\*"
} else {
    Write-Host "Compressing Portable ZIP via PowerShell Compress-Archive..." -ForegroundColor Yellow
    Compress-Archive -Path "$ZipSrcDir\*" -DestinationPath "$ZipDst" -Force
}

if (Test-Path $ZipDst) {
    $zipSize = (Get-Item $ZipDst).Length / 1MB
    Write-Host "[OK] Created Portable ZIP: $ZipDst ($([math]::Round($zipSize, 2)) MB)" -ForegroundColor Green
}

# Compile turnkey Windows Installer via Inno Setup (ISCC)
$InnoComp = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if (-not (Test-Path $InnoComp)) {
    $cmdInno = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($cmdInno) { $InnoComp = $cmdInno.Source }
}

if (Test-Path $InnoComp) {
    Write-Host "`nCompiling Turnkey Installer via Inno Setup..." -ForegroundColor Yellow
    if (Test-Path "$ScriptDir\dist_installer") { Remove-Item -Path "$ScriptDir\dist_installer" -Recurse -Force -ErrorAction SilentlyContinue }
    New-Item -ItemType Directory -Force -Path "$ScriptDir\dist_installer" | Out-Null

    & $InnoComp "/DMyAppVersion=$AppVersion" "$ScriptDir\installer.iss"
    if ($LASTEXITCODE -eq 0) {
        Write-Host "[OK] Created Windows Installer in dist_installer\" -ForegroundColor Green
    } else {
        Write-Host "[WARN] Inno Setup compilation encountered exit code: $LASTEXITCODE" -ForegroundColor Yellow
    }
} else {
    Write-Host "`n[INFO] Inno Setup (ISCC.exe) not found on system. Portable ZIP generated." -ForegroundColor Cyan
}

Write-Host "=================================================================" -ForegroundColor Green
Write-Host "✅ TIKI REHEARSAL LOUNGE WINDOWS BUILD COMPLETED SUCCESSFULLY!" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
