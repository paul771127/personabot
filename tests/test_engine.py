from __future__ import annotations

from dataclasses import replace

from personabot.engine import Engine, split_bubbles
from personabot.store import Store
from personabot.types import InboundMessage


def _engine(settings, persona, brain, store=None, **overrides) -> Engine:
    if overrides:
        settings = replace(settings, **overrides)
    return Engine(
        settings=settings, persona=persona, store=store or Store(":memory:"), brain=brain
    )


def _msg(text: str, **kwargs) -> InboundMessage:
    return InboundMessage(platform="cli", user_id="u1", text=text, **kwargs)


# ---- 拆泡泡 ----


def test_blank_lines_become_separate_bubbles():
    assert split_bubbles("欸真的假的\n\n那你還好嗎", 3) == ["欸真的假的", "那你還好嗎"]


def test_bubbles_over_the_limit_get_merged_into_the_last_one():
    assert split_bubbles("a\n\nb\n\nc\n\nd", 3) == ["a", "b", "c\nd"]


def test_long_single_paragraph_is_split_on_sentence_ends():
    long_text = "今天好累。" * 12
    bubbles = split_bubbles(long_text, 3, long_bubble_chars=20)
    assert 1 < len(bubbles) <= 3
    assert "".join(bubbles) == long_text


def test_short_paragraph_is_left_alone():
    assert split_bubbles("嗯。好。", 3, long_bubble_chars=120) == ["嗯。好。"]


def test_empty_reply_yields_nothing():
    assert split_bubbles("   \n  ", 3) == []


# ---- 流程 ----


async def test_reply_is_stored_and_history_grows(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    brain.replies = ["欸,你好"]

    reply = await engine.handle(_msg("嗨"))

    assert reply.bubbles == ["欸,你好"]
    conv = engine.store.get_or_create_conversation("cli", "u1", persona.id)
    stored = [(m.role, m.text) for m in engine.store.recent_messages(conv.id, 10)]
    assert stored == [("user", "嗨"), ("assistant", "欸,你好")]
    await engine.drain()


async def test_memory_is_passed_to_the_model(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    conv = engine.store.get_or_create_conversation("cli", "u1", persona.id)
    engine.store.add_facts(conv.id, ["叫阿明"], cap=10)
    engine.store.set_summary(conv.id, "之前聊過貓", 0)

    await engine.handle(_msg("在幹嘛", display_name="阿明"))

    call = brain.chat_calls[-1]
    assert call["facts"] == ["叫阿明"]
    assert call["summary"] == "之前聊過貓"
    assert call["display_name"] == "阿明"
    await engine.drain()


async def test_each_platform_keeps_its_own_history(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    await engine.handle(InboundMessage(platform="line", user_id="u1", text="嗨"))
    await engine.handle(InboundMessage(platform="telegram", user_id="u1", text="哈囉"))

    line_conv = engine.store.get_or_create_conversation("line", "u1", persona.id)
    tg_conv = engine.store.get_or_create_conversation("telegram", "u1", persona.id)
    assert line_conv.id != tg_conv.id
    assert len(engine.store.recent_messages(line_conv.id, 10)) == 2
    await engine.drain()


async def test_empty_message_is_ignored(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    reply = await engine.handle(_msg("   "))
    assert reply.bubbles == []
    assert brain.chat_calls == []


async def test_rate_limit_kicks_in(settings, persona, brain):
    engine = _engine(settings, persona, brain, rate_limit_per_minute=2)
    await engine.handle(_msg("1"))
    await engine.handle(_msg("2"))
    third = await engine.handle(_msg("3"))
    assert "太快" in third.text
    assert len(brain.chat_calls) == 2
    await engine.drain()


async def test_model_failure_still_returns_something(settings, persona, brain):
    engine = _engine(settings, persona, brain)

    async def boom(*args, **kwargs):
        raise RuntimeError("API 掛了")

    brain.chat = boom
    reply = await engine.handle(_msg("嗨"))
    assert reply.bubbles
    assert "問題" in reply.text


# ---- 指令 ----


async def test_reset_forgets_everything(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    await engine.handle(_msg("嗨"))
    await engine.drain()
    conv = engine.store.get_or_create_conversation("cli", "u1", persona.id)
    engine.store.add_facts(conv.id, ["叫阿明"], cap=10)

    reply = await engine.handle(_msg("/reset"))

    assert "忘" in reply.text
    assert engine.store.get_facts(conv.id) == []
    assert engine.store.recent_messages(conv.id, 10) == []


async def test_whoami_lists_facts(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    conv = engine.store.get_or_create_conversation("cli", "u1", persona.id)
    engine.store.add_facts(conv.id, ["叫阿明", "在念資工"], cap=10)

    reply = await engine.handle(_msg("/whoami"))

    assert "叫阿明" in reply.text and "在念資工" in reply.text


async def test_unknown_slash_command_is_treated_as_chat(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    await engine.handle(_msg("/不存在的指令"))
    assert brain.chat_calls, "不認得的指令應該照常丟給模型"
    await engine.drain()


async def test_start_includes_greeting(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    reply = await engine.handle(_msg("/start"))
    assert "欸,你好。" in reply.text


# ---- 背景記憶整理 ----


async def test_facts_are_extracted_after_the_exchange(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    brain.facts_to_return = ["叫阿明"]

    await engine.handle(_msg("我叫阿明"))
    await engine.drain()

    conv = engine.store.get_or_create_conversation("cli", "u1", persona.id)
    assert engine.store.get_facts(conv.id) == ["叫阿明"]


async def test_old_messages_get_folded_into_a_summary(settings, persona, brain):
    engine = _engine(settings, persona, brain, window_messages=4, summarize_after=8)
    brain.summary_to_return = "他們聊了很多"

    for i in range(6):
        await engine.handle(_msg(f"第 {i} 句"))
        await engine.drain()

    conv = engine.store.get_or_create_conversation("cli", "u1", persona.id)
    assert conv.summary == "他們聊了很多"
    assert conv.summarized_upto > 0
    # 摘要之後,送進模型的歷史只剩視窗內的訊息
    assert len(brain.chat_calls[-1]["history"]) <= 5


async def test_persona_name_reaches_the_memory_extractor(settings, persona, brain):
    """抽記憶時要能分辨誰是誰,否則會把機器人自己的背景記成使用者的事。"""
    engine = _engine(settings, persona, brain)
    await engine.handle(_msg("嗨"))
    await engine.drain()
    assert brain.last_assistant_name == persona.name
