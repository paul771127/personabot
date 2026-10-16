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
