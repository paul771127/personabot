#!/usr/bin/env bash
# 一次把本地模型準備好(Linux / macOS)。Windows 請用 setup-ollama.ps1。
set -euo pipefail

cd "$(dirname "$0")/.."

if ! command -v ollama >/dev/null 2>&1; then
    echo "找不到 ollama 指令。先去 https://ollama.com/download 安裝。" >&2
    exit 1
fi

if ! curl -sf http://localhost:11434/api/version >/dev/null; then
    echo "Ollama 沒在跑。另開一個終端機執行 ollama serve,再回來跑這個。" >&2
    exit 1
fi

echo "==> 下載基礎模型(第一次會很久,10GB + 1.9GB)"
ollama pull qwen3.5:9b-q8_0
ollama pull qwen3.5:2b

echo
echo "==> 建立調好 context 的版本"
ollama create personabot-chat -f ollama/chat.Modelfile
ollama create personabot-utility -f ollama/utility.Modelfile

if [ ! -f .env ]; then
    cp .env.ollama.example .env
    echo
    echo "==> 已建立 .env(從 .env.ollama.example 複製)"
else
    echo
    echo "==> .env 已經存在,沒有覆蓋。要用本地模型的話確認裡面有 LLM_BACKEND=local"
fi

echo
echo "好了。接著跑:"
echo "    python -m personabot.cli --selftest"
