from __future__ import annotations

import textwrap

import pytest

from personabot.persona import Persona


def test_card_contains_every_section(persona):
    card = persona.card()
    assert "小葵" in card
    assert "話不多" in card
    assert "短句" in card


def test_chat_rules_mention_bubble_limit(persona):
    rules = persona.chat_rules(max_bubbles=2)
    assert "最多 2 則" in rules


def test_unknown_yaml_keys_become_extra_sections():
    persona = Persona.from_dict({"name": "阿寬", "口頭禪": ["是喔", "對啊"]})
    card = persona.card()
    assert "## 口頭禪" in card
    assert "- 是喔" in card


def test_load_from_file(tmp_path):
    path = tmp_path / "x.yaml"
    path.write_text(
        textwrap.dedent(
            """
            name: 小明
            personality:
              - 很吵
            """
        ),
        encoding="utf-8",
    )
    persona = Persona.load(path)
    assert persona.name == "小明"
    assert persona.id == "x"  # 沒寫 id 就用檔名
    assert persona.personality == ["很吵"]


def test_load_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError):
        Persona.load(tmp_path / "nope.yaml")


def test_multiline_string_becomes_list():
    persona = Persona.from_dict({"name": "a", "personality": "一\n二\n"})
    assert persona.personality == ["一", "二"]


def test_traditional_chinese_personas_get_the_taiwan_vocabulary_rule():
    """開源小模型最常漏的就是這個,所以不靠角色卡自己寫,由系統帶上。"""
    persona = Persona.from_dict({"name": "小葵", "language": "繁體中文(台灣用語)"})
    rules = persona.chat_rules(max_bubbles=3)
    assert "只用台灣的說法" in rules
    assert "視頻" in rules and "質量" in rules


def test_traditional_chinese_personas_also_get_the_character_rule():
    """用詞對了字還是會漏簡體,所以用詞和字形要分成兩條規則講。"""
    persona = Persona.from_dict({"name": "小葵", "language": "繁體中文(台灣用語)"})
    rules = persona.chat_rules(max_bubbles=3)
    assert "只能用繁體字" in rules
    assert "一個簡體字都不要出現" in rules


def test_other_languages_do_not_get_it():
    persona = Persona.from_dict({"name": "Aoi", "language": "English"})
    rules = persona.chat_rules(max_bubbles=3)
    assert "只用台灣的說法" not in rules
    assert "只能用繁體字" not in rules


def test_rules_still_contain_the_basics(persona):
    rules = persona.chat_rules(max_bubbles=3)
    for expected in ("最多 3 則", "不要用條列", "客服腔"):
        assert expected in rules


def test_rules_put_answering_him_before_talking_about_herself(persona):
    """字數壓短之後,模型很容易只講自己的事、把對方的問題漏掉。"""
    rules = persona.chat_rules(max_bubbles=2)
    assert "第一則先回答他問的" in rules
    assert "放不下就不要講" in rules
