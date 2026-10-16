# 一次把本地模型準備好(Windows PowerShell)。
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup-ollama.ps1
#
# Windows 上 Ollama 安裝完會自己常駐在工具列,通常不用手動開 serve。

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not (Get-Command ollama -ErrorAction SilentlyContinue)) {
    Write-Error "找不到 ollama 指令。先去 https://ollama.com/download 安裝。"
    exit 1
}

try {
    Invoke-RestMethod -Uri "http://localhost:11434/api/version" -TimeoutSec 5 | Out-Null
} catch {
    Write-Error "連不上 Ollama。檢查工具列有沒有 Ollama 的圖示,沒有的話從開始選單打開它。"
    exit 1
}

Write-Host "==> 下載基礎模型(第一次會很久,10GB + 1.9GB)"
ollama pull qwen3.5:9b-q8_0
ollama pull qwen3.5:2b

Write-Host ""
Write-Host "==> 建立調好 context 的版本"
ollama create personabot-chat -f ollama\chat.Modelfile
ollama create personabot-utility -f ollama\utility.Modelfile

if (-not (Test-Path ".env")) {
    Copy-Item ".env.ollama.example" ".env"
    Write-Host ""
    Write-Host "==> 已建立 .env(從 .env.ollama.example 複製)"
} else {
    Write-Host ""
    Write-Host "==> .env 已經存在,沒有覆蓋。要用本地模型的話確認裡面有 LLM_BACKEND=local"
}

Write-Host ""
Write-Host "好了。接著跑:"
Write-Host "    python -m personabot.cli --selftest"
