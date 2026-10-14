# 后台静默启动 GoldAlert（关掉终端也会继续跑）
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$exe = Join-Path $root ".venv\Scripts\pythonw.exe"
$pidFile = Join-Path $root "data\agent.pid"
New-Item -ItemType Directory -Force -Path (Join-Path $root "data") | Out-Null

if (Test-Path $pidFile) {
    $existing = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($existing -and (Get-Process -Id ([int]$existing) -ErrorAction SilentlyContinue)) {
        Write-Host "GoldAlert 已经在运行 (PID $existing)，无需重复启动。" -ForegroundColor Yellow
        exit 0
    }
}

if (-not (Test-Path $exe)) {
    Write-Host "找不到 $exe，请确认虚拟环境已创建。" -ForegroundColor Red
    exit 1
}

$process = Start-Process -FilePath $exe -ArgumentList "run.py", "--no-display" `
    -WorkingDirectory $root -WindowStyle Hidden -PassThru
$process.Id | Set-Content -Path $pidFile -Encoding ascii

Write-Host "GoldAlert 已在后台启动，PID = $($process.Id)" -ForegroundColor Green
Write-Host "日志文件: $(Join-Path $root 'logs\monitor.log')"