# 安装 / 取消 GoldAlert 开机自启（放到"启动"文件夹）
param([switch]$Remove)

$root = Split-Path -Parent $PSScriptRoot
$startupDir = [Environment]::GetFolderPath("Startup")
$linkPath = Join-Path $startupDir "GoldAlert.lnk"

if ($Remove) {
    if (Test-Path $linkPath) {
        Remove-Item $linkPath -Force
        Write-Host "已取消开机自启。" -ForegroundColor Green
    } else {
        Write-Host "本来就没有设置开机自启。" -ForegroundColor Yellow
    }
    exit 0
}

$scriptPath = Join-Path $root "scripts\start_background.ps1"
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($linkPath)
$shortcut.TargetPath = "powershell.exe"
$shortcut.Arguments = "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$scriptPath`""
$shortcut.WorkingDirectory = $root
$shortcut.WindowStyle = 7
$shortcut.Description = "GoldAlert 现货黄金监控（开机自动后台运行）"

try {
    $shortcut.Save()
} catch {
    Write-Host "创建快捷方式失败：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "目标位置：$linkPath"
    Write-Host "如果是一直失败，可以手动做：把下面这个文件的快捷方式放进上面那个目录"
    Write-Host "  $scriptPath"
    exit 1
}

if (-not (Test-Path $linkPath)) {
    Write-Host "保存后没有找到快捷方式，设置未生效。" -ForegroundColor Red
    exit 1
}

Write-Host "已设置开机自启。" -ForegroundColor Green
Write-Host "快捷方式: $linkPath"
Write-Host "下次登录 Windows 后会自动在后台启动。"