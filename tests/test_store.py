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


def test_a_more_detailed_restatement_replaces_the_old_fact(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["在台中念資工三年級"], cap=10)
    # 同一件事講得更清楚,不該變成第二則。
    assert store.add_facts(conv.id, ["使用者在台中念資工三年級"], cap=10) == 0
    assert store.get_facts(conv.id) == ["使用者在台中念資工三年級"]


def test_a_vaguer_restatement_does_not_overwrite_the_detailed_one(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["使用者在台中念資工三年級"], cap=10)
    assert store.add_facts(conv.id, ["在台中念資工三年級"], cap=10) == 0
    assert store.get_facts(conv.id) == ["使用者在台中念資工三年級"]


def test_merging_keeps_the_original_position(store):
    """清單順序要穩定,後端的 prompt 快取才有得用。"""
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["使用者養了一隻貓", "在台中念資工三年級", "使用者怕蟑螂"], cap=10)
    store.add_facts(conv.id, ["使用者在台中念資工三年級"], cap=10)
    assert store.get_facts(conv.id) == [
        "使用者養了一隻貓",
        "使用者在台中念資工三年級",
        "使用者怕蟑螂",
    ]


def test_two_different_things_about_the_same_topic_both_survive(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["使用者在台中念書"], cap=10)
    assert store.add_facts(conv.id, ["使用者在台中打工"], cap=10) == 1
    assert len(store.get_facts(conv.id)) == 2


def test_short_facts_are_never_fuzzy_matched(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["叫阿明"], cap=10)
    assert store.add_facts(conv.id, ["叫阿明哥"], cap=10) == 1
    assert len(store.get_facts(conv.id)) == 2


def test_compact_cleans_up_a_database_written_before_dedupe_existed(store):
    """add_facts 現在寫入當下就擋掉重複,所以 compact 真正要收拾的是舊資料。"""
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    with store._conn() as conn:
        for content in ("使用者養了一隻貓", "使用者怕蟑螂", "使用者養了一隻貓叫校稿"):
            conn.execute(
                "INSERT INTO facts (conversation_id, content, created_at) VALUES (?, ?, 0)",
                (conv.id, content),
            )
    assert store.compact_facts(conv.id) == 1
    assert store.get_facts(conv.id) == ["使用者養了一隻貓叫校稿", "使用者怕蟑螂"]


def test_compact_is_a_no_op_when_nothing_repeats(store):
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.add_facts(conv.id, ["使用者養了一隻貓", "使用者怕蟑螂"], cap=10)
    assert store.compact_facts(conv.id) == 0
    assert len(store.get_facts(conv.id)) == 2


def test_an_old_database_gets_the_new_columns(tmp_path):
    """CREATE TABLE IF NOT EXISTS 不會幫既有的表加欄位,所以要自己補。
    漏了的話,舊的 data/personabot.db 一升級就整個打不開。"""
    import sqlite3

    from personabot.store import Store

    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        """CREATE TABLE conversations (
               id              INTEGER PRIMARY KEY AUTOINCREMENT,
               platform        TEXT NOT NULL,
               user_id         TEXT NOT NULL,
               persona_id      TEXT NOT NULL,
               display_name    TEXT,
               summary         TEXT NOT NULL DEFAULT '',
               summarized_upto INTEGER NOT NULL DEFAULT 0,
               created_at      REAL NOT NULL,
               last_active_at  REAL NOT NULL,
               UNIQUE (platform, user_id, persona_id)
           )"""
    )
    conn.execute(
        "INSERT INTO conversations (platform, user_id, persona_id, summary, created_at,"
        " last_active_at) VALUES ('cli', 'u1', 'aoi', '聊過了', 0, 0)"
    )
    conn.commit()
    conn.close()

    store = Store(path)
    conv = store.get_conversation(1)

    assert conv.summary == "聊過了"  # 舊資料還在
    assert (conv.interest, conv.guard, conv.read_state) == (50, 0, "")


def test_reset_also_clears_the_relationship(store):
    """漏掉的話 /reset 之後她還是記得「他一直在試探我」。"""
    conv = store.get_or_create_conversation("cli", "1", "aoi")
    store.update_rapport(conv.id, interest=95, guard=60, quiet_turns=3, read_state="很累")

    store.reset_conversation(conv.id)

    fresh = store.get_conversation(conv.id)
    assert (fresh.interest, fresh.guard, fresh.quiet_turns, fresh.read_state) == (50, 0, 0, "")
