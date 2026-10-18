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



def test_rules_tell_her_not_to_be_glib(persona):
    rules = persona.chat_rules(max_bubbles=2)
    assert "不要油嘴滑舌" in rules
    assert "語助詞一句最多一個" in rules


def test_strengths_are_read_but_kept_out_of_the_card():
    persona = Persona.from_dict({"name": "小葵", "strengths": ["很會挑書", "很會煮"]})
    assert persona.strengths == ["很會挑書", "很會煮"]
    assert "很會挑書" not in persona.system_prompt(3)


def test_rules_forbid_inventing_shared_memories(persona):
    rules = persona.chat_rules(max_bubbles=2)
    assert "記憶裡沒有的事,不要說成他講過" in rules
    assert "不要主動約見面" in rules


def test_rules_ask_for_varied_energy_and_frame_control(persona):
    rules = persona.chat_rules(max_bubbles=2)
    assert "不要每則都同一個溫度" in rules


def test_rules_allow_cold_reading_but_not_fortune_telling(persona):
    rules = persona.chat_rules(max_bubbles=2)
    assert "你對他這個人的感覺" in rules
    assert "不要假裝你能通靈" in rules


def test_rules_forbid_inventing_concrete_facts(persona):
    rules = persona.chat_rules(max_bubbles=2)
    assert "書名、作者、店名" in rules
    assert "不要掰一個出來" in rules


# ---- 對話目標、自訂段落、預覽 ----


def test_goal_is_read_from_the_card():
    persona = Persona.from_dict({"name": "小葵", "goal": "讓他願意固定找她聊"})
    assert persona.goal == "讓他願意固定找她聊"


def test_a_custom_section_becomes_a_card_section():
    persona = Persona.from_dict({"name": "小葵", "交往經驗": ["談過兩次", "都不長"]})
    card = persona.card()
    assert "## 交往經驗" in card
    assert "- 談過兩次" in card


def test_a_custom_section_as_plain_text():
    persona = Persona.from_dict({"name": "小葵", "愛情觀": "慢熱,看重誠實"})
    assert "## 愛情觀\n慢熱,看重誠實" in persona.card()


def test_a_custom_section_as_a_mapping():
    persona = Persona.from_dict({"name": "小葵", "學歷": {"學校": "東華中文", "狀態": "已畢業"}})
    card = persona.card()
    assert "- 學校:東華中文" in card
    assert "- 狀態:已畢業" in card


def test_the_field_report_lists_filled_empty_and_custom():
    persona = Persona.from_dict(
        {"name": "小葵", "goal": "", "personality": ["話少"], "書": ["兒子的大玩偶"]}
    )
    report = {label: status for label, status, _ in persona.field_report()}
    assert report["個性"] == "有"
    assert report["對話目標"].startswith("—")
    assert report["書（自訂）"] == "有"


def test_rules_forbid_filling_in_his_details(persona):
    """他只講大概,她不該替他補細節(木頭杯子、肉圓)。"""
    rules = persona.chat_rules(max_bubbles=2)
    assert "不要替他補細節" in rules
    assert "想知道細節就問他" in rules
