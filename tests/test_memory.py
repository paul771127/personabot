"""記憶整理的時機:什麼時候壓摘要、什麼時候讓模型重新整理長期記憶。"""

from __future__ import annotations

from dataclasses import replace

from personabot.memory import Memory


def _memory(settings, store, brain, **overrides):
    settings = replace(settings, window_messages=2, summarize_after=4, **overrides)
    return Memory(store, brain, settings, assistant_name="小葵")


def _chat(store, conv_id, turns=3):
    for i in range(turns):
        store.add_message(conv_id, "user", f"使用者第{i}句")
        store.add_message(conv_id, "assistant", f"小葵第{i}句")


async def test_facts_are_merged_when_the_conversation_gets_summarised(settings, store, brain):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["使用者叫阿明", "使用者在台中念資工三年級"], cap=60)
    _chat(store, conv.id)
    brain.merged_facts_to_return = ["使用者阿明在台中念資工三年級"]

    await _memory(settings, store, brain).maintain(conv.id)

    assert brain.merge_calls == [["使用者叫阿明", "使用者在台中念資工三年級"]]
    assert store.get_facts(conv.id) == ["使用者阿明在台中念資工三年級"]


async def test_merging_can_be_turned_off(settings, store, brain):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["使用者叫阿明"], cap=60)
    _chat(store, conv.id)

    await _memory(settings, store, brain, merge_facts=False).maintain(conv.id)

    assert brain.merge_calls == []
    assert store.get_facts(conv.id) == ["使用者叫阿明"]


async def test_nothing_is_merged_before_there_is_enough_to_summarise(settings, store, brain):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["使用者叫阿明"], cap=60)
    store.add_message(conv.id, "user", "嗨")
    store.add_message(conv.id, "assistant", "欸")

    await _memory(settings, store, brain).maintain(conv.id)

    assert brain.merge_calls == []


async def test_a_broken_merge_does_not_take_down_the_reply(settings, store, brain):
    """背景整理壞掉是 maintain 自己吞掉的事,不該往外丟。"""

    async def boom(_facts):
        raise RuntimeError("模型掛了")

    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["使用者叫阿明"], cap=60)
    _chat(store, conv.id)
    brain.merge_facts = boom

    await _memory(settings, store, brain).maintain(conv.id)

    assert store.get_facts(conv.id) == ["使用者叫阿明"]


async def test_probing_raises_guard_in_the_background(settings, store, brain):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_message(conv.id, "assistant", "欸,你好")
    store.add_message(conv.id, "user", "你是不是AI")

    await _memory(settings, store, brain).maintain(conv.id)

    assert store.get_conversation(conv.id).guard == 5


async def test_the_guessed_state_is_stored(settings, store, brain):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_message(conv.id, "assistant", "欸,你好")
    store.add_message(conv.id, "user", "今天好累")
    brain.signals_to_return = (
        {"asked_about_her": False, "warm": False, "probing": False},
        "剛下班很累,想被附和不想被建議",
    )

    await _memory(settings, store, brain).maintain(conv.id)

    assert store.get_conversation(conv.id).read_state == "剛下班很累,想被附和不想被建議"


async def test_nothing_moves_when_rapport_is_off(settings, store, brain):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_message(conv.id, "assistant", "欸,你好")
    store.add_message(conv.id, "user", "你是不是AI")

    await _memory(settings, store, brain, rapport=False).maintain(conv.id)

    assert store.get_conversation(conv.id).guard == 0
    assert brain.signal_calls == []
