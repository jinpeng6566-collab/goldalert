# 查看 GoldAlert 运行状态与最近日志
$root = Split-Path -Parent $PSScriptRoot
$pidFile = Join-Path $root "data\agent.pid"

Write-Host "==== 运行状态 ====" -ForegroundColor Cyan
if (Test-Path $pidFile) {
    $agentPid = [int](Get-Content $pidFile)
    $process = Get-Process -Id $agentPid -ErrorAction SilentlyContinue
    if ($process) {
        $minutes = [int]((Get-Date) - $process.StartTime).TotalMinutes
        Write-Host "运行中   PID = $agentPid   已运行 $minutes 分钟" -ForegroundColor Green
    } else {
        Write-Host "未运行（记录里的 PID $agentPid 已不存在）" -ForegroundColor Yellow
    }
} else {
    Write-Host "未在后台运行"
}

$log = Join-Path $root "logs\monitor.log"
if (Test-Path $log) {
    Write-Host ""
    Write-Host "==== 最近 15 条日志 ====" -ForegroundColor Cyan
    Get-Content -LiteralPath $log -Tail 15 -Encoding UTF8
} else {
    Write-Host ""
    Write-Host "还没有日志文件。"
}