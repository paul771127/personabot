"""關係狀態:她覺得對方多投入、對方有多在試探、現在該用什麼節奏回。

分兩半算。**程式算得準的,程式自己算** —— 隔多久回、句子多長、是不是「哈哈」
這種被逗笑的短回覆。**程式算不準的交給模型** —— 有沒有在關心、有沒有在試探
是不是 AI。關鍵字分不出「你還好嗎」跟「你是不是機器人」,兩句都有「你」。

分數本身不直接餵給模型。數字對小模型沒意義,`describe()` 會把它翻成
「偏熱絡」「有點戒備」這種看得懂的話,再附上該怎麼調整的提示。
"""

from __future__ import annotations

import random
import re
from dataclasses import dataclass

from .config import Settings
from .store import Conversation

STEP = 5
INTEREST_MIN, INTEREST_MAX = 0, 100
GUARD_MIN, GUARD_MAX = 0, 100

# 「哈哈」「笑死」是被逗笑,不是冷淡。這種短回覆要當成熱的,不然她會愈聊愈客氣。
_AMUSED = re.compile(r"(哈{2,}|笑死|笑爛|好好笑|ㄏㄏ|xd+|lol|😂|🤣|🤭|😆)", re.IGNORECASE)
_QUESTION = re.compile(r"[?？]")
# 「嗨」「在嗎」是來找你聊天,不是敷衍。隔了很久才冒出來的招呼尤其不該扣分。
_GREETING = re.compile(
    r"^(嗨+|哈囉+|hi+|hello+|hey+|安+|早安|午安|晚安|在嗎|在不在|欸+|喂+)[!！?？~、。\s]*$",
    re.IGNORECASE,
)
_EMOJI = re.compile(r"[\U0001f300-\U0001faff☀-➿]")

# 模型判斷是主力,這兩組只是地板 —— 講得這麼直白的句子不該漏掉。
_ASKING_IF_AI = re.compile(
    r"(是不是\s*(ai|a\.i|機器人|程式|bot)|你是\s*(ai|bot|機器人|chatgpt)"
    r"|真人嗎|是真人|不是真人|不相信你|你是假的)",
    re.IGNORECASE,
)
_PUSHING_FOR_PROOF = re.compile(r"(自拍|拍一張|給我看照片|視訊|語音通話|證明你)")


@dataclass
class Signals:
    """這一則訊息透露了什麼。模型判斷的三項預設都是 False,拿不到就當沒有。"""

    gap_seconds: float = 0.0
    chars: int = 0
    amused: bool = False
    greeting: bool = False  # 「嗨」「在嗎」這種來找你的招呼
    asked_about_her: bool = False
    warm: bool = False  # 關心、鼓勵、心疼
    probing: bool = False  # 在試探是不是 AI、硬要照片


def read_message(text: str, gap_seconds: float, settings: Settings) -> Signals:
    """純靠文字和時間算得出來的部分。"""
    stripped = text.strip()
    amused = bool(_AMUSED.search(stripped))
    greeting = bool(_GREETING.match(stripped))
    return Signals(
        greeting=greeting,
        gap_seconds=max(0.0, gap_seconds),
        chars=len(stripped),
        amused=amused,
        probing=bool(_ASKING_IF_AI.search(stripped) or _PUSHING_FOR_PROOF.search(stripped)),
    )


def _is_cold(signals: Signals, settings: Settings) -> bool:
    """敷衍的回覆:短、沒問句、沒表情、也不是被逗笑。"""
    if signals.amused or signals.greeting or signals.asked_about_her or signals.warm:
        return False
    return signals.chars <= settings.cold_reply_chars


def next_interest(current: int, signals: Signals, settings: Settings, text: str = "") -> int:
    """一則訊息最多動一格。同時符合好幾項也只加一次,不然幾句就頂到一百。"""
    warmer = (
        signals.asked_about_her
        or signals.warm
        or signals.amused
        or 0 < signals.gap_seconds <= settings.fast_reply_seconds
    )
    colder = signals.gap_seconds >= settings.slow_reply_seconds or (
        _is_cold(signals, settings) and not _QUESTION.search(text) and not _EMOJI.search(text)
    )
    if warmer and not colder:
        return min(INTEREST_MAX, current + STEP)
    if colder and not warmer:
        return max(INTEREST_MIN, current - STEP)
    return current


def next_guard(current: int, quiet_turns: int, signals: Signals, settings: Settings) -> tuple[int, int]:
    """回傳 (戒心, 連續幾則沒被觸發)。"""
    if signals.probing:
        return min(GUARD_MAX, current + STEP), 0
    quiet = quiet_turns + 1
    if quiet >= settings.guard_cooldown_turns:
        return max(GUARD_MIN, current - STEP), 0
    return current, quiet


# ── 翻成給模型看的話 ──────────────────────────────────────────────────


def _interest_band(value: int) -> tuple[str, str]:
    if value >= 70:
        return "聊得滿熱的", "可以多講自己的事,也可以主動開新話題,不用等他問。"
    if value >= 40:
        return "普通", "多問他一點他自己的事,接住他講的細節,讓他愈講愈起勁。"
    return (
        "有點冷掉了",
        "不要追問、不要討拍。講一件你自己今天遇到的小事,結尾留一個他會想接的鉤子,"
        "然後就停在那裡等他。",
    )


def _guard_band(value: int) -> tuple[str, str] | None:
    """他在試探的時候,她怎麼接。

    目標是讓他放鬆,不是讓她防衛 —— 一被試探就變冷淡、話變短,興趣度只會一路掉。
    但「被直接問就誠實回答」這條不動:那是角色卡本來的規則。
    """
    if value >= 40:
        return (
            "他一直在試探",
            "不要防衛、不要變冷淡,輕鬆帶過、可以自嘲一下,然後把話題拉回他身上。"
            "照片、視訊、見面不答應,但不用編理由,笑笑帶過就好。"
            "他要是直接問你是不是 AI,照你原本的規則誠實回答,不要閃躲。",
        )
    if value >= 15:
        return "他有點好奇你", "自在地回就好,不用緊張,也不用特別解釋。"
    return None


# (興趣度下限, {則數: 權重})。平常一兩則,聊開了才慢慢變多。
# 只在提示詞裡說「多數時候一則就夠」沒用 —— 實測模型八輪裡有七輪都回兩則,
# 而且對話紀錄裡全是兩則,它只會愈學愈固定。所以每輪由程式抽籤決定。
#
# 一則和兩則大致各半;聊得熱了才多出三、四則,但一則兩則還是維持一樣多。
_BUBBLE_WEIGHTS: list[tuple[int, dict[int, int]]] = [
    (85, {1: 35, 2: 35, 3: 20, 4: 10}),
    (70, {1: 40, 2: 40, 3: 20}),
    (0, {1: 50, 2: 50}),
]


def _short_and_flat(conv: Conversation, signals: Signals | None, settings: Settings) -> bool:
    """他只丟了一兩個字,而且不是「哈哈」「嗨」這種熱的短回覆。"""
    if not 0 < conv.last_chars <= settings.cold_reply_chars:
        return False
    return not (signals and (signals.amused or signals.greeting))


def pick_bubbles(
    conv: Conversation, signals: Signals | None, settings: Settings, rng: random.Random
) -> int:
    """這一輪要回幾則。上限是 MAX_BUBBLES。"""
    if not settings.rapport:
        return settings.max_bubbles
    if _short_and_flat(conv, signals, settings) and conv.interest < 70:
        return 1
    weights = next(w for floor, w in _BUBBLE_WEIGHTS if conv.interest >= floor)
    choices = [n for n in weights if n <= settings.max_bubbles] or [1]
    return rng.choices(choices, [weights[n] for n in choices])[0]


def _bubble_line(n: int) -> str:
    if n <= 1:
        return "- 這次回一則就好。"
    if n == 2:
        return "- 這次回兩則左右,一則講得完也可以只回一則。"
    return f"- 聊得正開心,這次可以多講一點,{n} 則左右。"


def describe(
    conv: Conversation,
    settings: Settings,
    signals: Signals | None = None,
    bubbles: int | None = None,
) -> str:
    """組成 system prompt 裡「你跟他現在的關係」那一段。"""
    if not settings.rapport:
        return ""
    lines = [
        "\n## 你跟他現在的關係",
        "- 你想要的:他愈聊愈開心、愈想跟你聊下去,而且跟你聊得放心、自在。",
    ]

    mood, hint = _interest_band(conv.interest)
    lines.append(f"- 他最近的投入程度:{mood}({conv.interest}/100)。{hint}")

    guard = _guard_band(conv.guard)
    if guard:
        level, advice = guard
        lines.append(f"- {level}({conv.guard}/100)。{advice}")

    if conv.read_state:
        lines.append(f"- 他現在的狀態:{conv.read_state}")

    if _short_and_flat(conv, signals, settings):
        lines.append(f"- 他這則只有 {conv.last_chars} 個字,你也簡短回。")

    if bubbles is not None:
        lines.append(_bubble_line(bubbles))

    return "\n".join(lines)


def lead_delay(conv: Conversation, signals: Signals, settings: Settings) -> float:
    """第一則訊息之前要等多久。

    對方晾你多久,你就大致晾回去 —— 但只跟到一個上限。對方隔六小時才回,
    她也隔六小時就不是矜持是當機了。被逗笑的時候不要擺架子,接話要快。
    """
    if not settings.rapport or signals.gap_seconds <= 0:
        return 0.0
    if signals.amused:
        return min(3.0, signals.gap_seconds * 0.1)
    ratio = 0.15 if conv.interest >= 70 else 0.25 if conv.interest >= 40 else 0.4
    return max(0.0, min(signals.gap_seconds * ratio, settings.max_lead_delay))
