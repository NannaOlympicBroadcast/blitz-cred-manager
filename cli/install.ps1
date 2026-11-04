# Blitz Credential Manager CLI Installer (Windows PowerShell)
$ErrorActionPreference = "Stop"

$ServerUrl = if ($env:CRED_SERVER_URL) { $env:CRED_SERVER_URL } else { "https://cred-manager.blitz48.blitz.cloud" }
$GithubRaw = "https://raw.githubusercontent.com/NannaOlympicBroadcast/blitz-cred-manager/main/cli"

$InstallDir = Join-Path $HOME ".local\bin"
if (-not (Test-Path $InstallDir)) {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
}

$ExePath = Join-Path $InstallDir "cred-cli.exe"
$PyPath = Join-Path $InstallDir "cred-cli.py"
$CmdWrapper = Join-Path $InstallDir "cred-cli.cmd"

Write-Host "[cred-cli] Installing Blitz Credential Manager CLI to $InstallDir ..." -ForegroundColor Cyan

$Downloaded = $false
if ($ServerUrl -and (-not $ServerUrl.StartsWith("__"))) {
    try {
        Invoke-WebRequest -Uri "$ServerUrl/cli/cred-cli-windows-amd64.exe" -OutFile $ExePath -UseBasicParsing
        $Downloaded = $true
    } catch {
        try {
            Invoke-WebRequest -Uri "$ServerUrl/cli/cred-cli.py" -OutFile $PyPath -UseBasicParsing
            Set-Content -Path $CmdWrapper -Value "@echo off`r`npython `"$PyPath`" %*" -Encoding ASCII
            $Downloaded = $true
        } catch {}
    }
}

if (-not $Downloaded) {
    try {
        Invoke-WebRequest -Uri "$GithubRaw/bin/cred-cli-windows-amd64.exe" -OutFile $ExePath -UseBasicParsing
        $Downloaded = $true
    } catch {
        Invoke-WebRequest -Uri "$GithubRaw/cred-cli.py" -OutFile $PyPath -UseBasicParsing
        Set-Content -Path $CmdWrapper -Value "@echo off`r`npython `"$PyPath`" %*" -Encoding ASCII
        $Downloaded = $true
    }
}

if ($env:PATH -notlike "*$InstallDir*") {
    $env:PATH = "$InstallDir;$env:PATH"
    $UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
    if ($UserPath -notlike "*$InstallDir*") {
        [Environment]::SetEnvironmentVariable("Path", "$InstallDir;$UserPath", "User")
    }
}

Write-Host "[cred-cli] Installed successfully to $InstallDir! Run 'cred-cli --help' to get started." -ForegroundColor Green
