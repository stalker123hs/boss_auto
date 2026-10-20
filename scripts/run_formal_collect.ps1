param([switch]$CheckOnly, [switch]$NoPause)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root 'collection_launcher.log'

try {
    Set-Location -LiteralPath $root
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw '未找到 .venv\Scripts\python.exe。请先双击“安装依赖并登录.bat”。'
    }

    if ($CheckOnly) {
        $runArgs = @('embedded_auto.py', 'check')
    } else {
        Write-Host '开始重新采集当前嵌入式配置的岗位数据。' -ForegroundColor Cyan
        Write-Host '旧采集 CSV 会先被清理；本步骤不会发送沟通。' -ForegroundColor Cyan
        $runArgs = @('embedded_auto.py', 'crawl-production', '--new-run')
    }
    & $python @runArgs
    $code = $LASTEXITCODE
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] exit=$code" | Set-Content -LiteralPath $log -Encoding utf8
    if ($code -ne 0) { throw "采集程序退出，错误码：$code。请查看 $log" }
    Write-Host "采集程序完成。日志：$log" -ForegroundColor Green
} catch {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ERROR $($_.Exception.Message)" | Set-Content -LiteralPath $log -Encoding utf8
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    if (-not $NoPause) { Read-Host '按回车关闭窗口' }
}
