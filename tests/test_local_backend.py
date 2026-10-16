"""本地後端(Ollama 等 OpenAI 相容伺服器)。"""

from __future__ import annotations

import json
from dataclasses import replace

import httpx
import pytest

from personabot.brain import Brain, LocalBackend, strip_thinking
from personabot.config import LocalConfig, Settings, reset_settings
from personabot.store import Message


def _local_settings(tmp_path, **kwargs) -> Settings:
    base = {
        "backend": "local",
        "db_path": ":memory:",
        "chat_model": "qwen3.5:9b-q8_0",
        "utility_model": "qwen3.5:2b",
        "local": LocalConfig(base_url="http://localhost:11434/v1", api_key="ollama", timeout=5.0),
    }
    base.update(kwargs)
    return Settings(**base)


def _backend(settings, handler) -> LocalBackend:
    return LocalBackend(settings, httpx.AsyncClient(transport=httpx.MockTransport(handler)))


def _chat_response(text: str) -> dict:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": text}}],
    }


def _history() -> list[Message]:
    return [Message(id=1, role="user", text="嗨", created_at=0.0)]


# ---- 請求形狀 ----


async def test_request_is_openai_shaped(tmp_path, persona):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json=_chat_response("欸,你好"))

    settings = _local_settings(tmp_path)
    brain = Brain(settings, _backend(settings, handler))

    reply = await brain.chat(persona, _history(), facts=["叫阿明"], display_name="阿明")

    assert reply == "欸,你好"
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"
    assert captured["auth"] == "Bearer ollama"

    body = captured["body"]
    assert body["model"] == "qwen3.5:9b-q8_0"
    assert body["stream"] is False
    # OpenAI 相容端點沒有快取區塊,人設與記憶合併成單一 system 訊息
    assert body["messages"][0]["role"] == "system"
    system = body["messages"][0]["content"]
    assert "小葵" in system and "叫阿明" in system and "阿明" in system
    assert body["messages"][1:] == [{"role": "user", "content": "嗨"}]


async def test_utility_model_is_used_for_background_work(tmp_path):
    models = []

    def handler(request: httpx.Request) -> httpx.Response:
        models.append(json.loads(request.content)["model"])
        return httpx.Response(200, json=_chat_response("- 他叫阿明"))

    settings = _local_settings(tmp_path)
    brain = Brain(settings, _backend(settings, handler))

    await brain.extract_facts(_history(), known=[])
    assert models == ["qwen3.5:2b"]


# ---- 思考標籤 ----


async def test_thinking_tags_never_reach_the_user(tmp_path, persona):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, json=_chat_response("<think>這個人在問候我,我該親切一點</think>欸,你好啊")
        )

    settings = _local_settings(tmp_path)
    brain = Brain(settings, _backend(settings, handler))
    assert await brain.chat(persona, _history()) == "欸,你好啊"


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("<think>abc</think>回覆", "回覆"),
        ("<THINK>abc</THINK>回覆", "回覆"),
        ("<thinking>abc</thinking>回覆", "回覆"),
        ("推理被截斷</think>回覆", "回覆"),
        ("<think>整段都是推理沒回覆", ""),
        ("乾淨的回覆", "乾淨的回覆"),
        ("<think>a</think>第一段\n\n<think>b</think>第二段", "第一段\n\n第二段"),
    ],
)
def test_strip_thinking(raw, expected):
    assert strip_thinking(raw) == expected


# ---- 錯誤處理 ----


async def test_server_down_says_so_in_plain_words(tmp_path, persona):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    settings = _local_settings(tmp_path)
    brain = Brain(settings, _backend(settings, handler))

    with pytest.raises(ConnectionError, match="Ollama"):
        await brain.chat(persona, _history())


async def test_http_error_includes_the_servers_reason(tmp_path, persona):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, text='{"error":"model \'qwen9:999b\' not found"}')

    settings = _local_settings(tmp_path)
    brain = Brain(settings, _backend(settings, handler))

    with pytest.raises(RuntimeError, match="not found"):
        await brain.chat(persona, _history())


# ---- 設定 ----


def test_local_backend_picks_qwen_defaults(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "local")
    monkeypatch.delenv("CHAT_MODEL", raising=False)
    monkeypatch.delenv("UTILITY_MODEL", raising=False)
    reset_settings()
    settings = Settings()
    assert settings.backend == "local"
    assert settings.chat_model.startswith("qwen")
    assert settings.utility_model.startswith("qwen")


def test_local_backend_needs_no_api_key(monkeypatch, tmp_path):
    from personabot.config import missing_credentials

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    assert missing_credentials(_local_settings(tmp_path)) == ""
    assert "ANTHROPIC_API_KEY" in missing_credentials(replace(_local_settings(tmp_path), backend="anthropic"))


def test_trailing_slash_in_base_url_is_trimmed(monkeypatch):
    monkeypatch.setenv("LOCAL_BASE_URL", "http://localhost:11434/v1/")
    assert LocalConfig().base_url == "http://localhost:11434/v1"


def test_local_backend_uses_a_shorter_memory_window(monkeypatch):
    """小模型塞太滿會出戲,所以本地預設比雲端保守。"""
    monkeypatch.delenv("WINDOW_MESSAGES", raising=False)
    monkeypatch.delenv("SUMMARIZE_AFTER", raising=False)

    monkeypatch.setenv("LLM_BACKEND", "anthropic")
    reset_settings()
    assert (Settings().window_messages, Settings().summarize_after) == (24, 40)

    monkeypatch.setenv("LLM_BACKEND", "local")
    reset_settings()
    assert (Settings().window_messages, Settings().summarize_after) == (16, 30)


def test_explicit_env_beats_the_backend_default(monkeypatch):
    monkeypatch.setenv("LLM_BACKEND", "local")
    monkeypatch.setenv("WINDOW_MESSAGES", "8")
    reset_settings()
    assert Settings().window_messages == 8
