param([switch]$NoPause)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$log = Join-Path $root 'collection_clear.log'

try {
    Set-Location -LiteralPath $root
    $python = Join-Path $root '.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw '未找到 .venv\Scripts\python.exe。请先双击“安装依赖并登录.bat”。'
    }
    $runArgs = @('embedded_auto.py', 'clear-collection')
    & $python @runArgs
    $code = $LASTEXITCODE
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] exit=$code" | Set-Content -LiteralPath $log -Encoding utf8
    if ($code -ne 0) { throw "清空程序退出，错误码：$code。请查看 $log" }
    Write-Host "清空完成。日志：$log" -ForegroundColor Green
} catch {
    "[$(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')] ERROR $($_.Exception.Message)" | Set-Content -LiteralPath $log -Encoding utf8
    Write-Host $_.Exception.Message -ForegroundColor Red
} finally {
    if (-not $NoPause) { Read-Host '按回车关闭窗口' }
}
