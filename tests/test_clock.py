"""現在時間:日期、星期、時段。"""

from __future__ import annotations

from datetime import datetime

from personabot import clock


def test_time_of_day_buckets():
    assert clock.time_of_day(2) == "深夜"
    assert clock.time_of_day(6) == "清晨"
    assert clock.time_of_day(9) == "早上"
    assert clock.time_of_day(12) == "中午"
    assert clock.time_of_day(15) == "下午"
    assert clock.time_of_day(18) == "傍晚"
    assert clock.time_of_day(21) == "晚上"
    assert clock.time_of_day(23) == "深夜"


def test_describe_now_has_weekday_date_and_slot():
    # 2026-10-19 是星期一
    text = clock.describe_now(datetime(2026, 10, 19, 21, 30))
    assert "星期一" in text
    assert "晚上" in text
    assert "2026年10月19日" in text
    assert "平日" in text


def test_weekend_is_marked():
    # 2026-10-17 是星期六
    text = clock.describe_now(datetime(2026, 10, 17, 10, 0))
    assert "星期六" in text
    assert "週末" in text


def test_it_tells_her_not_to_read_the_clock_aloud():
    text = clock.describe_now(datetime(2026, 10, 19, 23, 0))
    assert "不要把日期或幾點幾分唸出來" in text
