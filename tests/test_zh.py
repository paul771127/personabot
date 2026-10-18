"""繁簡偵測:寧可漏抓,也不要把正常的繁體句子判成簡體。"""

from __future__ import annotations

from personabot.zh import SIMPLIFIED_ONLY, find_simplified


def test_finds_simplified_characters():
    assert find_simplified("今天过得如何?有看到什麼好看的书吗?") == ["过", "书", "吗"]


def test_leaves_traditional_alone():
    clean = (
        "今天過得如何?有看到什麼好看的書嗎?還是又被作者氣得想煮泡麵算了?"
        "欸,你好。我是小葵,二十四歲,在台北一間小出版社做書籍編輯。"
        "影片、品質、程度、訊息、軟體、螢幕、網路。"
    )
    assert find_simplified(clean) == []


def test_characters_both_scripts_use_are_not_evidence():
    """「裡面」「後來」「只有」這些字兩邊都在用,抓了就會天天誤判。"""
    for ch in "里后面干只几台范松谷系占党布丑像划污凌":
        assert ch not in SIMPLIFIED_ONLY, ch


def test_order_is_kept_and_repeats_collapse():
    assert find_simplified("说说话话过") == ["说", "话", "过"]


def test_empty_text_is_clean():
    assert find_simplified("") == []
    assert find_simplified("hello, world 123") == []


def test_shared_characters_that_slipped_into_the_table_are_gone():
    """手打的表混進過「趣」「千」這些繁體也在用的字,害它白白重講。"""
    clean = "真有趣,一千塊,温柔,去游泳,養猫,扎實,北斗,感慨,紊亂"
    assert find_simplified(clean) == []
