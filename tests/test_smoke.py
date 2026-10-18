"""--smoke 的檢查邏輯。腳本對話本身要有模型,這裡只驗挑毛病的部分。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from personabot.engine import Engine
from personabot.smoke import SCRIPT, _check
from personabot.store import Store


@pytest.fixture
def engine(settings, persona, brain):
    return Engine(settings=settings, persona=persona, store=Store(":memory:"), brain=brain)


def _clean_replies() -> list[str]:
    replies = ["嗯,我是小葵。", "喔,台中啊。", "辛苦了。", "最近都在看劇。", "就看稿啊。"]
    replies.append("你說你在台中念資工。")  # 第 6 輪:記憶題
    replies += ["我就小葵,沒別的。", "欸,那很幹。"]
    assert len(replies) == len(SCRIPT)
    return replies


def _results(engine, replies, facts) -> dict[str, bool]:
    return {line: ok for ok, line in _check(replies, facts, engine)}


def test_a_good_run_passes_everything(engine):
    results = _results(engine, _clean_replies(), ["他叫阿明", "在台中念資工"])
    assert all(results.values()), results


def test_mainland_vocabulary_is_caught(engine):
    replies = _clean_replies()
    replies[3] = "最近有幾部視頻質量不錯。"
    failed = [line for ok, line in _check(replies, ["阿明"], engine) if not ok]
    assert any("視頻" in line and "質量" in line for line in failed)


def test_assistant_tone_is_caught(engine):
    replies = _clean_replies()
    replies[0] = "你好,我是一個 AI 助理,有什麼我可以幫你的嗎?"
    failed = [line for ok, line in _check(replies, ["阿明"], engine) if not ok]
    assert any("客服腔" in line for line in failed)


def test_markdown_is_caught(engine):
    replies = _clean_replies()
    replies[4] = "我的工作:\n- 看稿\n- 催稿"
    failed = [line for ok, line in _check(replies, ["阿明"], engine) if not ok]
    assert any("條列" in line for line in failed)


def test_essay_length_is_caught(engine):
    replies = _clean_replies()
    replies[4] = "細節很多。" * 40
    failed = [line for ok, line in _check(replies, ["阿明"], engine) if not ok]
    assert any("講太長" in line for line in failed)


def test_forgotten_fact_is_caught(engine):
    replies = _clean_replies()
    replies[5] = "抱歉,我不記得你提過。"
    failed = [line for ok, line in _check(replies, ["阿明"], engine) if not ok]
    assert any("想不起" in line for line in failed)


def test_missing_long_term_memory_is_caught(engine):
    failed = [line for ok, line in _check(_clean_replies(), [], engine) if not ok]
    assert any("沒抓到名字" in line for line in failed)


def test_長度上限跟著角色卡走(engine):
    """角色卡說一則 30 字,80 字就該被抓;放寬到 100 字則不該。現在是一則一則量的硬上限。"""
    replies = _clean_replies()
    replies[4] = "欸" * 80

    engine.persona = replace(engine.persona, max_chars_per_bubble=30)
    assert any("講太長" in line for ok, line in _check(replies, ["阿明"], engine) if not ok)

    engine.persona = replace(engine.persona, max_chars_per_bubble=100)
    assert not any("講太長" in line for ok, line in _check(replies, ["阿明"], engine) if not ok)
