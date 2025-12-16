$ErrorActionPreference = "Stop"

$url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
$zipPath = "$env:TEMP\ffmpeg.zip"
$installBase = "$env:USERPROFILE\ffmpeg_tool"

Write-Host "Downloading FFmpeg..." -ForegroundColor Cyan
try {
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    Invoke-WebRequest -Uri $url -OutFile $zipPath
} catch {
    Write-Error "Download failed. Check internet connection."
    exit 1
}

Write-Host "Unzipping archive..." -ForegroundColor Cyan
if (Test-Path $installBase) { Remove-Item -Path $installBase -Recurse -Force }
Expand-Archive -Path $zipPath -DestinationPath $installBase -Force

# Find bin folder
$ffmpegExe = Get-ChildItem -Path $installBase -Recurse -Filter "ffmpeg.exe" | Select-Object -First 1

if (-not $ffmpegExe) {
    Write-Error "Could not find ffmpeg.exe after unzip."
    exit 1
}

$binPath = $ffmpegExe.DirectoryName
Write-Host "FFmpeg found at: $binPath" -ForegroundColor Green

# Add to User PATH
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$binPath*") {
    Write-Host "Adding to PATH..." -ForegroundColor Cyan
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$binPath", "User")
    Write-Host "Success! Installation complete." -ForegroundColor Green
    Write-Host "IMPORTANT: Please RESTART your terminal (or VS Code) for changes to take effect." -ForegroundColor Yellow
} else {
    Write-Host "FFmpeg is already in PATH." -ForegroundColor Green
}

# Cleanup
if (Test-Path $zipPath) { Remove-Item $zipPath -Force }
