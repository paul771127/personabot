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


# ---- 關係狀態 ----


async def test_the_relationship_block_reaches_the_model(settings, persona, brain):
    store = Store(":memory:")
    engine = _engine(settings, persona, brain, store)
    await engine.handle(_msg("嗨"))
    conv = store.get_or_create_conversation("cli", "u1", persona.id)
    store.update_rapport(conv.id, interest=85)

    await engine.handle(_msg("你今天在幹嘛"))

    assert "聊得滿熱的" in brain.chat_calls[-1]["rapport"]


async def test_the_first_message_gets_no_artificial_wait(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    reply = await engine.handle(_msg("嗨"))
    assert reply.lead_delay == 0.0


async def test_she_waits_before_replying_to_someone_who_kept_her_waiting(
    settings, persona, brain
):
    store = Store(":memory:")
    engine = _engine(settings, persona, brain, store)
    await engine.handle(_msg("嗨"))

    conv = store.get_or_create_conversation("cli", "u1", persona.id)
    last = store.last_message(conv.id, "assistant")
    late = _msg("在嗎", received_at=last.created_at + 600)

    reply = await engine.handle(late)
    assert reply.lead_delay > 0


async def test_how_long_he_took_and_how_much_he_wrote_are_recorded(settings, persona, brain):
    store = Store(":memory:")
    engine = _engine(settings, persona, brain, store)
    await engine.handle(_msg("嗨"))
    conv = store.get_or_create_conversation("cli", "u1", persona.id)
    last = store.last_message(conv.id, "assistant")

    await engine.handle(_msg("今天好累喔", received_at=last.created_at + 120))

    fresh = store.get_conversation(conv.id)
    assert fresh.last_chars == len("今天好累喔")
    assert 100 < fresh.last_gap < 200


async def test_rapport_can_be_turned_off(settings, persona, brain):
    engine = _engine(settings, persona, brain, rapport=False)
    reply = await engine.handle(_msg("嗨"))
    assert reply.lead_delay == 0.0
    assert brain.chat_calls[-1]["rapport"] == ""


# ---- 每則字數的硬上限 ----


def test_strict_mode_keeps_every_bubble_under_the_limit():
    raw = "我整天對著一堆書籍跟印刷廠的進度表，反而懶得換螢幕了。你呢？"
    bubbles = split_bubbles(raw, 2, max_chars=20)
    assert bubbles
    assert all(len(b) <= 20 for b in bubbles), bubbles


def test_strict_mode_packs_short_sentences_together():
    """「嗯。」「好。」這種短句不該各自佔一則;但這輪能回兩則,就不要硬擠成一則。"""
    assert split_bubbles("嗯。好。那你呢?", 2, max_chars=20) == ["嗯。好。", "那你呢?"]
    assert split_bubbles("嗯。好。那你呢?", 1, max_chars=20) == ["嗯。好。那你呢?"]


def test_two_sentences_on_one_line_become_two_bubbles_when_the_budget_allows():
    """實測十二輪只有一輪回兩則:兩句加起來沒超過字數,就被併成一則了。"""
    raw = "太惡心了啦，那是甩鍋欸…快關門躲一下？"
    assert split_bubbles(raw, 2, max_chars=20) == ["太惡心了啦，那是甩鍋欸…", "快關門躲一下？"]
    assert split_bubbles(raw, 1, max_chars=20) == [raw]


def test_strict_mode_drops_what_does_not_fit_instead_of_cramming():
    """塞不下的就不送,不要把後面全部黏進最後一則。"""
    raw = "第一句話講完了。第二句話也講完了。第三句話還在講。第四句話還沒完。"
    bubbles = split_bubbles(raw, 2, max_chars=10)
    assert len(bubbles) == 2
    assert all(len(b) <= 10 for b in bubbles)


def test_strict_mode_does_not_end_a_bubble_with_a_comma():
    raw = "我今天跑了三間書店找那本書，最後在公館的舊書攤找到了"
    bubbles = split_bubbles(raw, 2, max_chars=20)
    assert not any(b.endswith(("，", ",")) for b in bubbles), bubbles


def test_without_max_chars_the_old_behaviour_is_unchanged():
    assert split_bubbles("a\n\nb\n\nc\n\nd", 3) == ["a", "b", "c\nd"]


async def test_the_engine_enforces_the_persona_length_limit(settings, persona, brain):
    brain.replies = ["今天去朋友家看到一隻貴賓狗，臉長得超級好笑，我笑到現在還停不下來。你今天呢？"]
    short = replace(persona, max_chars_per_bubble=20)
    engine = _engine(settings, short, brain, max_bubbles=2)
    reply = await engine.handle(_msg("嗨"))
    assert 1 <= len(reply.bubbles) <= 2
    assert all(len(b) <= 20 for b in reply.bubbles), reply.bubbles


def test_strict_mode_treats_single_line_breaks_as_bubble_boundaries():
    raw = "我是小葵\n二十四歲在北區做編輯的南部人啦🙃\n住在公館老公寓五樓"
    bubbles = split_bubbles(raw, 2, max_chars=20)
    assert bubbles == ["我是小葵", "二十四歲在北區做編輯的南部人啦🙃"]


def test_strict_mode_does_not_glue_a_fragment_onto_the_previous_sentence():
    """「欸,」是下一句的開頭,不能黏到上一句後面變成「...資工科啊。欸」。"""
    raw = "喔，原來是資工科啊。欸，那個要改不完的程式碼真的很痛耶，校稿就常這樣踩鍵盤"
    bubbles = split_bubbles(raw, 3, max_chars=20)
    assert bubbles[0] == "喔，原來是資工科啊。"
    assert all(len(b) <= 20 for b in bubbles), bubbles


def test_strict_mode_treats_an_ellipsis_as_a_sentence_end():
    raw = "那個要改不完的程式碼真的很痛耶...校稿就常這樣踩鍵盤出不來 😂"
    bubbles = split_bubbles(raw, 2, max_chars=20)
    assert bubbles[0] == "那個要改不完的程式碼真的很痛耶..."


async def test_the_chosen_bubble_count_reaches_the_model_and_the_splitter(
    settings, persona, brain
):
    import random

    brain.replies = ["嗯。\n\n好啊。\n\n那你呢?\n\n我剛下班。"]
    engine = _engine(settings, persona, brain, max_bubbles=4)
    engine.rng = random.Random(0)
    reply = await engine.handle(_msg("今天去公館"))

    budget = brain.chat_calls[-1]["max_bubbles"]
    assert 1 <= budget <= 4
    assert len(reply.bubbles) <= budget


def test_an_unsplittable_extra_bubble_over_the_limit_is_dropped():
    """第一則是回答,後面那則又長又切不開,就當成不重要的刪掉。"""
    raw = "沒特別追\n但剛剛在便利商店看到一個阿伯跟店員吵關東煮要不要加湯"
    assert split_bubbles(raw, 2, max_chars=20) == ["沒特別追"]


def test_a_trailing_interjection_is_not_left_hanging():
    assert split_bubbles("先休息一下，喝杯水也好。欸...", 2, max_chars=20) == ["先休息一下，喝杯水也好。"]


def test_an_interjection_goes_with_the_sentence_after_it():
    bubbles = split_bubbles("嗯。\n欸...你今天吃了嗎", 2, max_chars=20)
    assert bubbles == ["嗯。欸...你今天吃了嗎"] or bubbles[-1].startswith("欸...你今天")



# ---- 語氣收斂 ----


def test_only_one_emoji_survives_per_reply():
    from personabot.bubbles import tone_down

    assert tone_down(["慘啦😂 太常發生了 🙃", "嗯 😂"], []) == ["慘啦😂 太常發生了", "嗯"]


def test_no_emoji_when_she_used_one_recently():
    """角色卡寫「一天用不到三次」,實測十二輪有八輪都帶表情。"""
    from personabot.bubbles import tone_down

    recent = ["剛下班", "校稿又踩鍵盤了 🙃", "嗯"]
    assert tone_down(["你快去睡吧 😂"], recent, emoji_gap=5) == ["你快去睡吧"]


def test_emoji_comes_back_after_enough_plain_replies():
    from personabot.bubbles import tone_down

    recent = ["校稿又踩鍵盤了 🙃", "嗯", "好", "是喔", "對啊", "晚安"]
    assert tone_down(["哈哈 😂"], recent, emoji_gap=5) == ["哈哈 😂"]


def test_the_drawn_out_tilde_is_removed():
    from personabot.bubbles import tone_down

    assert tone_down(["香喔\uff5e", "哈哈\uff5e\uff5e那我先去煮茶"], []) == ["香喔", "哈哈那我先去煮茶"]


def test_a_bubble_that_was_only_an_emoji_disappears():
    from personabot.bubbles import tone_down

    assert tone_down(["好啊", "😂"], ["🙃"]) == ["好啊"]


def test_everything_can_be_switched_off():
    from personabot.bubbles import tone_down

    assert tone_down(["好喔\uff5e😂🙃"], ["🙃"], emoji_gap=0, strip_tilde=False) == ["好喔\uff5e😂🙃"]


async def test_the_bubble_count_reaches_the_real_prompt(settings, persona):
    """從 engine 一路到真的 brain:這輪抽到的則數要出現在送給模型的 prompt 裡。"""
    import random

    from personabot.brain import Brain

    class _Capture:
        name = "capture"

        def __init__(self):
            self.prompts = []

        async def complete(self, prompt, model):
            self.prompts.append(prompt)
            return "嗯。"

        async def aclose(self): ...

    backend = _Capture()
    brain = Brain(settings=settings, backend=backend)
    engine = _engine(settings, persona, brain)
    engine.rng = random.Random(0)
    await engine.handle(_msg("今天去公館"))

    chat_prompt = backend.prompts[0]
    assert "你跟他現在的關係" in chat_prompt.dynamic_system
    assert "這次回" in chat_prompt.dynamic_system


async def test_the_stage_and_strengths_reach_the_real_prompt(settings, persona):
    from dataclasses import replace as dc_replace

    from personabot.brain import Brain

    class _Capture:
        name = "capture"

        def __init__(self):
            self.prompts = []

        async def complete(self, prompt, model):
            self.prompts.append(prompt)
            return "嗯。"

        async def aclose(self): ...

    store = Store(":memory:")
    backend = _Capture()
    proud = dc_replace(persona, strengths=["很會挑書"])
    engine = _engine(settings, proud, Brain(settings=settings, backend=backend), store)

    await engine.handle(_msg("嗨"))
    first = backend.prompts[0].dynamic_system
    assert "第 1 階段" in first
    assert "很會挑書" not in first

    conv = store.get_or_create_conversation("cli", "u1", persona.id)
    store.update_rapport(conv.id, stage=3)
    await engine.handle(_msg("今天去公館"))
    later = [p for p in backend.prompts if "第 3 階段" in p.dynamic_system]
    assert later and "很會挑書" in later[-1].dynamic_system


async def test_the_current_time_reaches_the_model(settings, persona, brain):
    engine = _engine(settings, persona, brain)
    await engine.handle(_msg("嗨"))
    assert "現在是星期" in brain.chat_calls[-1]["now"]


async def test_the_clock_can_be_turned_off(settings, persona, brain):
    engine = _engine(settings, persona, brain, clock=False)
    await engine.handle(_msg("嗨"))
    assert brain.chat_calls[-1]["now"] == ""
