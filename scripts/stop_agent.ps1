# 停止后台运行的 GoldAlert
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root "data\agent.pid"

if (-not (Test-Path $pidFile)) {
    Write-Host "没有找到运行记录，GoldAlert 似乎没在后台运行。" -ForegroundColor Yellow
    exit 0
}

$agentPid = [int](Get-Content $pidFile)
$process = Get-Process -Id $agentPid -ErrorAction SilentlyContinue
if ($process) {
    Stop-Process -Id $agentPid -Force
    Write-Host "已停止 GoldAlert (PID $agentPid)。" -ForegroundColor Green
} else {
    Write-Host "进程 $agentPid 已经不存在了。" -ForegroundColor Yellow
}
Remove-Item $pidFile -Force -ErrorAction SilentlyContinue