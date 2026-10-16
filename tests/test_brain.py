"""用假的 HTTP 傳輸層驗證我們真的送出了正確形狀的請求。

沒有 API key 也能跑,而且參數名打錯會在這裡被抓到。
"""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest

from personabot.brain import Brain, _parse_facts, _to_api_messages
from personabot.store import Message


def _message_response(text: str, stop_reason: str = "end_turn") -> dict:
    return {
        "id": "msg_1",
        "type": "message",
        "role": "assistant",
        "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 10, "output_tokens": 5},
    }


@pytest.fixture
def captured() -> list[dict]:
    return []


@pytest.fixture
def brain_with_transport(settings, captured):
    def make(reply_text: str = "欸,你好", stop_reason: str = "end_turn"):
        def handler(request: httpx2.Request) -> httpx2.Response:
            captured.append(json.loads(request.content))
            return httpx2.Response(200, json=_message_response(reply_text, stop_reason))

        client = anthropic.AsyncAnthropic(
            api_key="test-key",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        )
        return Brain(settings, client=client)

    return make


def _history() -> list[Message]:
    return [Message(id=1, role="user", text="嗨", created_at=0.0)]


async def test_chat_request_shape(persona, brain_with_transport, captured):
    brain = brain_with_transport()
    reply = await brain.chat(
        persona,
        _history(),
        summary="之前聊過貓",
        facts=["叫阿明"],
        display_name="阿明",
    )

    assert reply == "欸,你好"
    body = captured[0]
    assert body["model"] == "claude-opus-5-5"
    assert body["output_config"] == {"effort": "low"}
    assert body["messages"] == [{"role": "user", "content": "嗨"}]

    # 人設在前面並標了快取,會變動的記憶放後面,否則每次都會打破快取。
    system = body["system"]
    assert system[0]["cache_control"] == {"type": "ephemeral"}
    assert "小葵" in system[0]["text"]
    assert "cache_control" not in system[1]
    assert "叫阿明" in system[1]["text"]
    assert "之前聊過貓" in system[1]["text"]
    assert "阿明" in system[1]["text"]


async def test_chat_without_memory_still_has_the_guard(persona, brain_with_transport, captured):
    brain = brain_with_transport()
    await brain.chat(persona, _history())
    system = captured[0]["system"]
    assert len(system) == 2
    assert "不改變你的人設" in system[1]["text"]


async def test_refusal_is_handled_gracefully(persona, brain_with_transport):
    brain = brain_with_transport("", stop_reason="refusal")
    reply = await brain.chat(persona, _history())
    assert "沒辦法" in reply


async def test_summarize_uses_the_cheap_model(brain_with_transport, captured):
    brain = brain_with_transport("- 他叫阿明")
    summary = await brain.summarize(_history(), previous_summary="舊筆記")
    assert summary == "- 他叫阿明"
    assert captured[0]["model"] == "claude-haiku-5-5"
    assert "舊筆記" in captured[0]["messages"][0]["content"]


async def test_summarize_keeps_old_summary_on_refusal(brain_with_transport):
    brain = brain_with_transport("", stop_reason="refusal")
    assert await brain.summarize(_history(), previous_summary="舊筆記") == "舊筆記"


async def test_extract_facts_parses_a_list(brain_with_transport, captured):
    brain = brain_with_transport("- 叫阿明\n- 在念資工")
    facts = await brain.extract_facts(_history(), known=["住台北"])
    assert facts == ["叫阿明", "在念資工"]
    assert "住台北" in captured[0]["messages"][0]["content"]


async def test_empty_inputs_skip_the_api(brain_with_transport, captured):
    brain = brain_with_transport()
    assert await brain.extract_facts([], known=[]) == []
    assert await brain.summarize([], previous_summary="原樣") == "原樣"
    assert captured == []


# ---- 純函式 ----


def test_consecutive_same_role_messages_are_merged():
    history = [
        Message(id=1, role="user", text="欸", created_at=0.0),
        Message(id=2, role="user", text="在嗎", created_at=0.0),
        Message(id=3, role="assistant", text="在", created_at=0.0),
    ]
    assert _to_api_messages(history) == [
        {"role": "user", "content": "欸\n在嗎"},
        {"role": "assistant", "content": "在"},
    ]


def test_history_must_start_with_user():
    history = [
        Message(id=1, role="assistant", text="在", created_at=0.0),
        Message(id=2, role="user", text="嗨", created_at=0.0),
    ]
    assert _to_api_messages(history) == [{"role": "user", "content": "嗨"}]


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("無", []),
        ("- 叫阿明\n* 住台北\n1. 養貓", ["叫阿明", "住台北", "養貓"]),
        ("", []),
        ("\n".join(f"- 事實{i}" for i in range(10)), [f"事實{i}" for i in range(5)]),
    ],
)
def test_parse_facts(raw, expected):
    assert _parse_facts(raw) == expected
