from __future__ import annotations


def test_conversation_is_created_once(store):
    a = store.get_or_create_conversation("telegram", "1", "aoi")
    b = store.get_or_create_conversation("telegram", "1", "aoi")
    assert a.id == b.id


def test_same_user_on_two_platforms_is_two_conversations(store):
    a = store.get_or_create_conversation("telegram", "1", "aoi")
    b = store.get_or_create_conversation("line", "1", "aoi")
    assert a.id != b.id


def test_display_name_is_updated_on_later_messages(store):
    store.get_or_create_conversation("line", "1", "aoi")
    conv = store.get_or_create_conversation("line", "1", "aoi", display_name="阿明")
    assert conv.display_name == "阿明"


def test_recent_messages_are_oldest_first_and_capped(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    for i in range(5):
        store.add_message(conv.id, "user", f"m{i}")
    recent = store.recent_messages(conv.id, limit=3)
    assert [m.text for m in recent] == ["m2", "m3", "m4"]


def test_facts_dedupe_and_respect_cap(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    assert store.add_facts(conv.id, ["喜歡貓", "喜歡貓"], cap=10) == 1
    assert store.add_facts(conv.id, ["喜歡貓"], cap=10) == 0
    store.add_facts(conv.id, [f"事實{i}" for i in range(20)], cap=5)
    assert len(store.get_facts(conv.id)) == 5


def test_summary_roundtrip(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    mid = store.add_message(conv.id, "user", "哈囉")
    store.set_summary(conv.id, "聊過哈囉", mid)
    reloaded = store.get_conversation(conv.id)
    assert reloaded.summary == "聊過哈囉"
    assert reloaded.summarized_upto == mid


def test_reset_clears_memory_but_keeps_conversation(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_message(conv.id, "user", "嗨")
    store.add_facts(conv.id, ["叫阿明"], cap=10)
    store.set_summary(conv.id, "有聊過", 1)

    store.reset_conversation(conv.id)

    assert store.recent_messages(conv.id, 10) == []
    assert store.get_facts(conv.id) == []
    assert store.get_conversation(conv.id).summary == ""


def test_messages_between_is_inclusive_of_upper_bound(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    ids = [store.add_message(conv.id, "user", f"m{i}") for i in range(4)]
    got = store.messages_between(conv.id, ids[0], ids[2])
    assert [m.text for m in got] == ["m1", "m2"]
