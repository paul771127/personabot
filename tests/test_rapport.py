"""興趣度、戒心、回訊節奏。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from personabot import rapport
from personabot.store import Conversation


def _conv(**kw) -> Conversation:
    base = dict(
        id=1,
        platform="cli",
        user_id="u",
        persona_id="aoi",
        display_name=None,
        summary="",
        summarized_upto=0,
        created_at=0.0,
        last_active_at=0.0,
    )
    base.update(kw)
    return Conversation(**base)


def _signals(settings, text="嗯", gap=0.0, **flags):
    signals = rapport.read_message(text, gap, settings)
    for key, value in flags.items():
        setattr(signals, key, value)
    return signals


# ---- 興趣度 ----


def test_fast_reply_raises_interest(settings):
    text = "欸你今天還好嗎?"
    assert rapport.next_interest(50, _signals(settings, text, gap=5), settings, text) == 55


def test_caring_raises_interest_even_when_slow(settings):
    text = "剛剛在忙,你今天還好嗎"
    signals = _signals(settings, text, gap=600, warm=True)
    assert rapport.next_interest(50, signals, settings, text) == 55


def test_a_curt_reply_lowers_interest(settings):
    assert rapport.next_interest(50, _signals(settings, "喔", gap=600), settings, "喔") == 45


def test_being_left_hanging_lowers_interest(settings):
    text = "抱歉我剛剛在忙,今天真的好累喔"
    signals = _signals(settings, text, gap=settings.slow_reply_seconds + 1)
    assert rapport.next_interest(50, signals, settings, text) == 45


def test_haha_is_not_a_cold_reply(settings):
    """被逗笑的短回覆是熱的。算成冷淡的話,她會愈聊愈客氣。"""
    signals = _signals(settings, "哈哈哈", gap=600)
    assert signals.amused
    assert rapport.next_interest(50, signals, settings, "哈哈哈") == 55


def test_interest_is_clamped(settings):
    assert rapport.next_interest(100, _signals(settings, "哈哈", gap=1), settings, "哈哈") == 100
    cold = _signals(settings, "嗯", gap=settings.slow_reply_seconds + 1)
    assert rapport.next_interest(0, cold, settings, "嗯") == 0


def test_one_message_moves_it_one_step_only(settings):
    """秒回 + 關心 + 問你的事 全中也只加一格,不然幾句就頂到一百。"""
    text = "你今天還好嗎?有沒有好一點"
    signals = _signals(settings, text, gap=2, warm=True, asked_about_her=True)
    assert rapport.next_interest(50, signals, settings, text) == 55


# ---- 戒心 ----


@pytest.mark.parametrize(
    "text",
    ["你是不是AI", "你是機器人吧", "你是真人嗎", "傳張自拍給我看", "證明你是真的"],
)
def test_probing_raises_guard(settings, text):
    signals = _signals(settings, text, gap=10)
    assert signals.probing, text
    assert rapport.next_guard(0, 0, signals, settings) == (5, 0)


def test_ordinary_talk_does_not_raise_guard(settings):
    signals = _signals(settings, "你今天在幹嘛", gap=10)
    assert not signals.probing
    assert rapport.next_guard(10, 0, signals, settings) == (10, 1)


def test_guard_cools_down_after_enough_quiet_turns(settings):
    signals = _signals(settings, "今天天氣不錯", gap=10)
    quiet = settings.guard_cooldown_turns - 1
    assert rapport.next_guard(20, quiet, signals, settings) == (15, 0)


def test_guard_never_goes_below_zero(settings):
    signals = _signals(settings, "今天天氣不錯", gap=10)
    quiet = settings.guard_cooldown_turns - 1
    assert rapport.next_guard(0, quiet, signals, settings) == (0, 0)


# ---- 進 prompt 的那段話 ----


def test_describe_translates_numbers_into_words(settings):
    text = rapport.describe(_conv(interest=80, guard=0), settings)
    assert "聊得滿熱的" in text
    assert "80/100" in text


def test_a_cold_conversation_gets_told_to_open_a_topic(settings):
    assert "鉤子" in rapport.describe(_conv(interest=20), settings)


def test_high_guard_still_forbids_lying_about_being_an_ai(settings):
    text = rapport.describe(_conv(guard=60), settings)
    assert "誠實回答" in text
    assert "不要閃躲" in text


def test_low_guard_is_not_mentioned_at_all(settings):
    assert "戒心" not in rapport.describe(_conv(guard=5), settings)


def test_a_short_message_asks_for_a_short_reply(settings):
    assert "簡短回" in rapport.describe(_conv(last_chars=3), settings)


def test_everything_is_off_when_rapport_is_off(settings):
    assert rapport.describe(_conv(interest=90), replace(settings, rapport=False)) == ""


# ---- 節奏 ----


def test_she_waits_longer_when_he_took_longer(settings):
    quick = rapport.lead_delay(_conv(), _signals(settings, gap=60), settings)
    slow = rapport.lead_delay(_conv(), _signals(settings, gap=600), settings)
    assert slow > quick > 0


def test_a_keen_conversation_gets_a_faster_reply(settings):
    keen = rapport.lead_delay(_conv(interest=90), _signals(settings, gap=120), settings)
    cold = rapport.lead_delay(_conv(interest=10), _signals(settings, gap=120), settings)
    assert keen < cold


def test_the_wait_is_capped(settings):
    """對方隔六小時才回,她也隔六小時就不是矜持是當機了。"""
    delay = rapport.lead_delay(_conv(interest=0), _signals(settings, gap=86400), settings)
    assert delay == settings.max_lead_delay


def test_she_does_not_play_hard_to_get_when_he_made_her_laugh(settings):
    signals = _signals(settings, "哈哈哈", gap=600)
    assert rapport.lead_delay(_conv(interest=0), signals, settings) <= 3.0


def test_no_wait_on_the_first_message(settings):
    assert rapport.lead_delay(_conv(), _signals(settings, gap=0), settings) == 0.0


def test_a_greeting_is_not_a_cold_reply(settings):
    """「嗨」是來找你聊天,不是敷衍。當成冷淡的話,久沒聊的人一開口就被扣分。"""
    for text in ("嗨", "哈囉~", "在嗎?", "hi", "欸"):
        signals = _signals(settings, text, gap=3600)
        assert signals.greeting, text
        assert rapport.next_interest(50, signals, settings, text) >= 50, text


def test_the_wait_is_short_enough_that_nobody_thinks_it_crashed(settings):
    """對方剛送出訊息、正看著螢幕等。超過半分鐘的靜默看起來不像矜持,像當機。"""
    assert settings.max_lead_delay <= 30


# ---- 每輪回幾則 ----


def _counts(conv, settings, draws=400, text="今天去了公館一趟"):
    import random
    from collections import Counter

    rng = random.Random(0)
    signals = _signals(settings, text, gap=10)
    return Counter(rapport.pick_bubbles(conv, signals, settings, rng) for _ in range(draws))


def test_one_and_two_bubbles_come_up_about_equally(settings):
    """儘量一句話與兩句話各半。"""
    for interest in (20, 50):
        counts = _counts(_conv(interest=interest), replace(settings, max_bubbles=4))
        assert set(counts) == {1, 2}
        share = counts[1] / sum(counts.values())
        assert 0.4 < share < 0.6, (interest, counts)


def test_one_and_two_stay_balanced_even_when_it_runs_longer(settings):
    counts = _counts(_conv(interest=95), replace(settings, max_bubbles=4))
    assert abs(counts[1] - counts[2]) < sum(counts.values()) * 0.1


def test_an_ordinary_conversation_stays_at_one_or_two(settings):
    counts = _counts(_conv(interest=50), replace(settings, max_bubbles=4))
    assert set(counts) == {1, 2}


def test_a_great_conversation_can_run_longer(settings):
    """聊到氣氛很好,句數才慢慢變多。"""
    counts = _counts(_conv(interest=95), replace(settings, max_bubbles=4))
    assert counts[3] > 0 and counts[4] > 0
    assert counts[1] > 0  # 但還是會有只回一則的時候,不是每次都講一大串


def test_the_count_never_exceeds_max_bubbles(settings):
    counts = _counts(_conv(interest=95), replace(settings, max_bubbles=2))
    assert set(counts) <= {1, 2}


def test_it_is_not_always_the_same_number(settings):
    """固定回兩則很奇怪 —— 要有多有少。"""
    counts = _counts(_conv(interest=60), replace(settings, max_bubbles=4))
    assert len(counts) >= 2


def test_a_curt_reply_gets_a_single_bubble_back(settings):
    import random

    conv = _conv(interest=50, last_chars=1)
    signals = _signals(settings, "喔", gap=10)
    assert rapport.pick_bubbles(conv, signals, settings, random.Random(0)) == 1


def test_haha_does_not_force_a_single_bubble(settings):
    conv = _conv(interest=95, last_chars=3)
    counts = _counts(conv, replace(settings, max_bubbles=4), text="哈哈哈")
    assert len(counts) > 1


def test_the_bubble_count_is_written_into_the_prompt(settings):
    assert "這次回一則就好" in rapport.describe(_conv(), settings, bubbles=1)
    assert "3 則左右" in rapport.describe(_conv(interest=90), settings, bubbles=3)


def test_haha_is_not_told_to_reply_curtly(settings):
    conv = _conv(last_chars=3)
    signals = _signals(settings, "哈哈哈", gap=10)
    assert "簡短回" not in rapport.describe(conv, settings, signals)


# ---- 她的目標 ----


def test_the_goal_is_spelled_out(settings):
    text = rapport.describe(_conv(), settings)
    assert "愈聊愈開心" in text
    assert "放心、自在" in text


def test_being_probed_makes_her_relax_not_go_cold(settings):
    """一被試探就變冷淡、話變短,興趣度只會一路掉。"""
    text = rapport.describe(_conv(guard=60), settings)
    assert "不要防衛" in text
    assert "話可以短一點" not in text
    assert "拉回他身上" in text
