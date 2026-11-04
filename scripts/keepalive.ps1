# Blitz Credential Manager — Windows PowerShell 保活脚本
param(
    [string]$Url = "https://cred-manager.blitz48.blitz.cloud/api/health",
    [int]$IntervalSeconds = 600,
    [switch]$Once
)

Write-Host "[KeepAlive] Target: $Url (Interval: ${IntervalSeconds}s)" -ForegroundColor Cyan

do {
    $Now = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
    $Sw = [System.Diagnostics.Stopwatch]::StartNew()
    try {
        $Resp = Invoke-RestMethod -Uri $Url -Headers @{"User-Agent"="Blitz-KeepAlive-PS/1.0"; "Cache-Control"="no-cache"} -TimeoutSec 30
        $Sw.Stop()
        Write-Host "[$Now] OK ($($Sw.ElapsedMilliseconds)ms) -> $($Resp | ConvertTo-Json -Compress)" -ForegroundColor Green
    } catch {
        $Sw.Stop()
        Write-Host "[$Now] ERROR ($($Sw.ElapsedMilliseconds)ms) -> $($_.Exception.Message)" -ForegroundColor Red
    }
    if ($Once) { break }
    Start-Sleep -Seconds $IntervalSeconds
} while ($true)
