param(
    [string]$Version = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$Python = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$Spec = Join-Path $ProjectRoot "TeamsInterpreter.spec"

if (-not (Test-Path -LiteralPath $Python)) {
    throw "Virtual environment not found. Run .\setup.ps1 first."
}

& $Python -m PyInstaller --version *> $null
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed. Run: .\.venv\Scripts\python.exe -m pip install -r requirements-build.txt"
}

if (-not $Version) {
    $Version = (& git -C $ProjectRoot rev-parse --short HEAD 2>$null)
    if (-not $Version) {
        $Version = Get-Date -Format "yyyyMMdd"
    }
}
$Version = $Version.Trim() -replace '[^0-9A-Za-z._-]', '-'

$BuildDir = Join-Path $ProjectRoot "build"
$DistDir = Join-Path $ProjectRoot "dist"
$ReleaseRoot = Join-Path $ProjectRoot "release"
$PackageName = "TeamsInterpreter-Windows-x64-$Version"
$PackageDir = Join-Path $ReleaseRoot $PackageName
$AppDir = Join-Path $PackageDir "app"
$ArchivePath = Join-Path $ReleaseRoot "$PackageName.zip"
$ChecksumPath = "$ArchivePath.sha256.txt"

foreach ($Path in @($BuildDir, $DistDir, $PackageDir, $ArchivePath, $ChecksumPath)) {
    $FullPath = [IO.Path]::GetFullPath($Path)
    if (-not $FullPath.StartsWith([IO.Path]::GetFullPath($ProjectRoot), [StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean a path outside the project: $FullPath"
    }
    if (Test-Path -LiteralPath $FullPath) {
        Remove-Item -LiteralPath $FullPath -Recurse -Force
    }
}

New-Item -ItemType Directory -Path $ReleaseRoot -Force | Out-Null

& $Python -m PyInstaller `
    --noconfirm `
    --clean `
    --workpath $BuildDir `
    --distpath $DistDir `
    $Spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE"
}

New-Item -ItemType Directory -Path $AppDir -Force | Out-Null
Copy-Item -Path (Join-Path $DistDir "TeamsInterpreter\*") -Destination $AppDir -Recurse -Force
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\Install.cmd") -Destination $PackageDir
Copy-Item -LiteralPath (Join-Path $ProjectRoot "packaging\User-Guide.txt") -Destination $PackageDir

$Executable = Join-Path $AppDir "TeamsInterpreter.exe"
$Hash = (Get-FileHash -LiteralPath $Executable -Algorithm SHA256).Hash
$BuildInfo = @(
    "Teams Interpreter Windows x64"
    "Build: $Version"
    "Built at: $((Get-Date).ToString('yyyy-MM-dd HH:mm:ss zzz'))"
    "Executable SHA256: $Hash"
)
Set-Content -LiteralPath (Join-Path $PackageDir "BUILD-INFO.txt") -Value $BuildInfo -Encoding utf8

$ArchiveCreated = $false
for ($Attempt = 1; $Attempt -le 5; $Attempt++) {
    try {
        if (Test-Path -LiteralPath $ArchivePath) {
            Remove-Item -LiteralPath $ArchivePath -Force
        }
        Compress-Archive `
            -LiteralPath $PackageDir `
            -DestinationPath $ArchivePath `
            -CompressionLevel Optimal `
            -ErrorAction Stop
        $ArchiveCreated = $true
        break
    }
    catch {
        if ($Attempt -eq 5) {
            throw
        }
        Write-Warning "Archive creation failed because a file is busy; retrying ($Attempt/5)..."
        Start-Sleep -Seconds 2
    }
}
if (-not $ArchiveCreated -or -not (Test-Path -LiteralPath $ArchivePath)) {
    throw "Release archive was not created: $ArchivePath"
}

$ArchiveHash = (Get-FileHash -LiteralPath $ArchivePath -Algorithm SHA256 -ErrorAction Stop).Hash
if (-not $ArchiveHash) {
    throw "Could not calculate the release archive checksum."
}
Set-Content `
    -LiteralPath $ChecksumPath `
    -Value "$ArchiveHash  $([IO.Path]::GetFileName($ArchivePath))" `
    -Encoding ascii

Write-Host ""
Write-Host "Release package created:" -ForegroundColor Green
Write-Host $ArchivePath
Write-Host "SHA256: $ArchiveHash"
Write-Host "Checksum file: $ChecksumPath"
