"""用假的 HTTP 傳輸層驗證我們真的送出了正確形狀的請求。

沒有 API key 也能跑,而且參數名打錯會在這裡被抓到。
"""

from __future__ import annotations

import json

import anthropic
import httpx2
import pytest

from personabot.brain import AnthropicBackend, Brain, _parse_facts, _to_api_messages
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
        return Brain(settings, AnthropicBackend(settings, client=client))

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
    said = [Message(id=1, role="user", text="我叫阿明,在念資工", created_at=0.0)]
    facts = await brain.extract_facts(said, known=["住台北"])
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


def test_overlong_facts_are_truncated():
    """小模型偶爾把整段回覆當成事實吐回來;那段垃圾不能永久留在記憶裡。"""
    from personabot.brain import MAX_FACT_CHARS

    facts = _parse_facts("- " + "細節很多。" * 40)
    assert len(facts) == 1
    assert len(facts[0]) == MAX_FACT_CHARS


def test_normal_length_facts_are_untouched():
    assert _parse_facts("- 他叫阿明,在台中念資工") == ["他叫阿明,在台中念資工"]


def test_prompt_headings_are_not_stored_as_facts():
    """小模型很愛把提示詞裡的分類標題也吐出來,那些不是事實。"""
    raw = "「以後還值得記得」的事實：\n- 對方身份：\n- 他叫阿明\n要記的:\n- 在台中念資工"
    assert _parse_facts(raw) == ["他叫阿明", "在台中念資工"]


def test_short_chinese_facts_survive():
    assert _parse_facts("- 叫阿明\n- 養貓\n- 住台北") == ["叫阿明", "養貓", "住台北"]


def test_transcript_echoes_are_not_facts():
    """小模型常常直接把對話逐字抄回來當事實,那是紀錄不是記憶。"""
    raw = (
        "使用者：今天好累喔\n"
        "小葵：做書籍編輯啦,看稿子跟催印刷廠\n"
        "使用者的身分是什麼？\n"
        "他在台中念資工三年級"
    )
    assert _parse_facts(raw, assistant_name="小葵") == ["他在台中念資工三年級"]


# ---- 讓模型合併記憶 ----


class _StubBackend:
    name = "stub"

    def __init__(self, text: str) -> None:
        self.text = text
        self.prompts: list = []

    async def complete(self, prompt, model):
        self.prompts.append((prompt, model))
        return self.text

    async def aclose(self) -> None: ...


_SIX = [
    "使用者叫阿明",
    "使用者在台中念資工三年級",
    "使用者阿明在台中市就讀資訊工程學系三年級",
    "使用者喜歡喝冰飲",
    "使用者養了一隻貓",
    "使用者怕蟑螂",
    "使用者下週一要面試",
    "使用者的媽媽住台南",
]


async def _merge(settings, reply: str, facts=None):
    backend = _StubBackend(reply)
    brain = Brain(settings=settings, backend=backend)
    return await brain.merge_facts(list(facts if facts is not None else _SIX)), backend


async def test_merge_accepts_a_result_that_only_combines_what_was_there(settings):
    reply = "\n".join(
        [
            "使用者阿明在台中市就讀資訊工程學系三年級",
            "使用者喜歡喝冰飲",
            "使用者養了一隻貓",
            "使用者怕蟑螂",
            "使用者下週一要面試",
            "使用者的媽媽住台南",
        ]
    )
    merged, _ = await _merge(settings, reply)
    assert len(merged) == 6
    assert "使用者阿明在台中市就讀資訊工程學系三年級" in merged


async def test_merge_rejects_a_result_with_something_nobody_said(settings):
    reply = "\n".join(["使用者叫阿明", "使用者是個很努力的好學生,未來一定會成功", *_SIX[3:]])
    merged, _ = await _merge(settings, reply)
    assert merged == _SIX


async def test_merge_rejects_a_result_that_threw_too_much_away(settings):
    merged, _ = await _merge(settings, "使用者叫阿明")
    assert merged == _SIX


async def test_merge_does_not_call_the_model_for_a_short_list(settings):
    facts = ["使用者叫阿明", "使用者養了一隻貓"]
    merged, backend = await _merge(settings, "不該被用到", facts)
    assert merged == facts
    assert backend.prompts == []


async def test_merge_uses_the_utility_model(settings):
    _, backend = await _merge(settings, "\n".join(_SIX))
    assert backend.prompts[0][1] == settings.utility_model


async def test_merge_rejects_a_result_just_past_the_shrink_limit(settings):
    """8 則收到 5 則 = 砍掉 37%。實測模型砍到這個份上時,丟掉的已經不是重複。"""
    merged, _ = await _merge(settings, "\n".join(_SIX[:5]))
    assert merged == _SIX


async def test_merge_accepts_a_result_just_inside_the_shrink_limit(settings):
    merged, _ = await _merge(settings, "\n".join(_SIX[:6]))
    assert merged == _SIX[:6]


# ---- 太長就請模型自己刪掉不重要的部分 ----


class _SequenceBackend:
    """照順序吐回覆,並記下收到的每個 prompt。"""

    name = "seq"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.prompts: list = []

    async def complete(self, prompt, model):
        self.prompts.append(prompt)
        return self.replies.pop(0)

    async def aclose(self) -> None: ...


_LONG = "校稿那傢伙剛才又在鍵盤上睡著了,真是拿我沒辦法。不過主要是改稿跟催作者交稿啦,還有跟印刷廠吵架。"
_SHORT = "主要是改稿跟催作者交稿啦\n\n還有跟印刷廠吵架 😂"


def _short_brain(settings, backend):
    from dataclasses import replace as dc_replace

    return Brain(settings=dc_replace(settings, max_bubbles=2), backend=backend)


async def test_a_reply_that_will_not_fit_gets_rewritten(settings, persona):
    from dataclasses import replace as dc_replace

    backend = _SequenceBackend(_LONG, _SHORT)
    brain = _short_brain(settings, backend)
    out = await brain.chat(dc_replace(persona, max_chars_per_bubble=20), _history_for_chat())

    assert out == _SHORT
    assert len(backend.prompts) == 2
    nudge = backend.prompts[1].messages[-1]["content"]
    assert "先回答他問的" in nudge


async def test_a_reply_that_fits_is_left_alone(settings, persona):
    from dataclasses import replace as dc_replace

    backend = _SequenceBackend("嗯，累了就休息一下嘛。")
    brain = _short_brain(settings, backend)
    out = await brain.chat(dc_replace(persona, max_chars_per_bubble=20), _history_for_chat())

    assert out == "嗯，累了就休息一下嘛。"
    assert len(backend.prompts) == 1


async def test_a_rewrite_that_is_not_shorter_is_thrown_away(settings, persona):
    from dataclasses import replace as dc_replace

    backend = _SequenceBackend(_LONG, _LONG + "而且")
    brain = _short_brain(settings, backend)
    out = await brain.chat(dc_replace(persona, max_chars_per_bubble=20), _history_for_chat())

    assert out == _LONG  # 交給 engine 硬切


def _history_for_chat():
    return [Message(id=1, role="user", text="你平常工作都在幹嘛", created_at=0.0)]


async def test_a_shorter_rewrite_that_says_something_else_is_rejected(settings, persona):
    """實測縮短時它會丟回前幾輪的某一句,短是短了,但完全接不上。"""
    from dataclasses import replace as dc_replace

    backend = _SequenceBackend(_LONG, "剛才是不是打錯字了?😂")
    brain = _short_brain(settings, backend)
    out = await brain.chat(dc_replace(persona, max_chars_per_bubble=20), _history_for_chat())
    assert out == _LONG


def test_angle_brackets_copied_from_the_template_are_stripped():
    from personabot.brain import _parse_signals

    _, state = _parse_signals("問你的事:無\n關心:無\n試探:無\n狀態:<心情低落,想聽安慰>")
    assert state == "心情低落,想聽安慰"


def test_facts_written_in_simplified_are_not_kept():
    """記憶整份送進 prompt,混著簡體她就會跟著寫簡體。"""
    facts = _parse_facts("阿明今天面试落选\n阿明在台中念資工")
    assert facts == ["阿明在台中念資工"]


# ---- 記憶要是使用者自己講的 ----


def _talk(*turns):
    return [Message(id=i, role=r, text=t, created_at=float(i)) for i, (r, t) in enumerate(turns, 1)]


_INTRO = _talk(
    ("user", "你是誰啊"),
    ("assistant", "我是小葵,二十四歲在公館做編輯的台南人\n\n剛剛校稿又被貓踩亂了"),
    ("user", "我叫阿明,在台中念資工三年級"),
    ("assistant", "你在台中唸資工嗎?"),
)


def test_what_he_said_about_himself_is_kept():
    from personabot.brain import _said_by_user

    assert _said_by_user("使用者叫阿明", _INTRO)
    assert _said_by_user("阿明在台中念資工三年級", _INTRO)


def test_her_own_background_is_not_pinned_on_him():
    from personabot.brain import _said_by_user

    assert not _said_by_user("使用者是二十四歲的編輯", _INTRO)
    assert not _said_by_user("阿明養了一隻叫校稿的貓", _INTRO)
    assert not _said_by_user("使用者住在公館", _INTRO)


def test_her_suggestion_does_not_become_his_preference():
    from personabot.brain import _said_by_user

    talk = _talk(
        ("user", "欸我今天被當了"),
        ("assistant", "別太難過\n\n明天想吃便利商店熱咖哩還是蓋飯啊"),
    )
    assert _said_by_user("使用者今天被當了", talk)
    assert not _said_by_user("阿明喜歡吃便利商店的熱咖哩", talk)


def test_a_short_yes_to_her_question_counts_as_his():
    """「你喜歡貓嗎?」「喜歡啊」—— 不算的話這種事永遠記不起來。"""
    from personabot.brain import _said_by_user

    talk = _talk(("assistant", "你喜歡貓嗎?"), ("user", "喜歡啊"))
    assert _said_by_user("使用者喜歡貓", talk)


async def test_extract_facts_drops_what_she_said(settings):
    backend = _SequenceBackend("使用者叫阿明\n使用者是二十四歲的編輯\n阿明養了一隻叫校稿的貓")
    brain = Brain(settings=settings, backend=backend)
    facts = await brain.extract_facts(_INTRO, [], "小葵")
    assert facts == ["使用者叫阿明"]


def test_a_fact_that_glues_her_detail_onto_his_is_rejected():
    """實測抽出過「使用者叫阿明,是台南人」—— 名字是他的,台南人是她的。"""
    from personabot.brain import _said_by_user

    assert not _said_by_user("使用者叫阿明,是台南人", _INTRO)
    assert not _said_by_user("使用者叫阿明,在台中念資工三年級,是台南人", _INTRO)
