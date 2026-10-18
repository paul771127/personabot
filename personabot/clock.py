"""現在幾點、今天幾號、星期幾 —— 讓她的話對得上時間。

沒有這個,她會在半夜說早安、中午叫人快睡。時間每輪都在變,所以放在動態那段,
不進會被快取的人設。只給她知道,不要她把日期時間唸出來。
"""

from __future__ import annotations

from datetime import datetime

_WEEKDAY = ["一", "二", "三", "四", "五", "六", "日"]

# (這個鐘點之前算這個時段)。由早到晚排,取第一個命中的。
_SLOTS = [
    (5, "深夜"),
    (8, "清晨"),
    (11, "早上"),
    (13, "中午"),
    (17, "下午"),
    (19, "傍晚"),
    (23, "晚上"),
    (24, "深夜"),
]


def time_of_day(hour: int) -> str:
    for edge, name in _SLOTS:
        if hour < edge:
            return name
    return "深夜"


def describe_now(now: datetime) -> str:
    weekday = _WEEKDAY[now.weekday()]
    slot = time_of_day(now.hour)
    weekend = now.weekday() >= 5
    kind = "週末" if weekend else "平日"
    return (
        "\n## 現在\n"
        f"- 現在是星期{weekday}{slot}({now.year}年{now.month}月{now.day}日、{kind})。"
        "配合現在的時間講話:晚上別說早安,中午別叫人去睡,週末別問他今天上不上班。"
        "但不要把日期或幾點幾分唸出來,就自然地用。"
    )
