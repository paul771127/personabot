"""把模型回的一段話切成幾則訊息。

engine 送出前用它切;brain 也用它判斷一段回覆塞不塞得進字數上限,
塞不進就請模型自己刪掉不重要的部分重講。
"""

from __future__ import annotations

import logging
import re

log = logging.getLogger(__name__)


_SENTENCE_END = re.compile(r"(?<=[。!?!?~])\s*")


def split_bubbles(
    raw: str, max_bubbles: int, long_bubble_chars: int = 120, max_chars: int | None = None
) -> list[str]:
    """把模型回的一段文字拆成幾則訊息。

    先照空白行拆(這是我們要求模型用的分隔方式);如果模型沒照做又寫得很長,
    就退而求其次照句尾標點拆。

    給了 max_chars 就是硬規定:每則不超過這麼多字、最多 max_bubbles 則。
    提示詞裡講了,但模型不一定聽,這裡是最後一道。
    """
    raw = raw.strip()
    if not raw:
        return []

    chunks = [c.strip() for c in re.split(r"\n\s*\n", raw) if c.strip()]
    if max_chars:
        return _pack(chunks, max_chars, max_bubbles)
    if len(chunks) == 1 and len(chunks[0]) > long_bubble_chars:
        sentences = [s.strip() for s in _SENTENCE_END.split(chunks[0]) if s.strip()]
        if len(sentences) > 1:
            chunks = _regroup(sentences, max_bubbles)

    if len(chunks) > max_bubbles:
        head = chunks[: max_bubbles - 1]
        tail = "\n".join(chunks[max_bubbles - 1 :])
        chunks = [*head, tail]
    return chunks


# 嚴格模式下的句尾。比上面那個多認刪節號和全形波浪號 —— 口語裡這些常常就是一句話的結束。
_STRICT_SENTENCE_END = re.compile(r"(?<=[。!?！？~～…])\s*|(?<=\.\.\.)\s*")  # noqa: RUF001
_CLAUSE_END = re.compile(r"(?<=[,，、;；])")
_TRAILING_PAUSE = "，,、;；"
# 只有一個語助詞的「欸...」「嗯。」,它是下一句的開頭,不是上一句的結尾。
_INTERJECTION = re.compile(r"^[欸啊嗯喔哦唉噢誒]+[.…。~～!！?？,，]*$")  # noqa: RUF001


def _units(chunks: list[str], max_chars: int) -> list[tuple[str, int, bool]]:
    """切成句子(還太長就照逗號再切)。回傳 (文字, 第幾行, 是不是半句)。"""
    # (文字, 第幾行, 是不是從一句長句裡切出來的半句)
    units: list[tuple[str, int, bool]] = []
    line_no = 0
    for chunk in chunks:
        # 模型常用單一換行當分句,那也是一則的界線 —— 換行的地方就是它想分開送的地方。
        for line in chunk.splitlines():
            line_no += 1
            for sentence in _STRICT_SENTENCE_END.split(line):
                sentence = sentence.strip()
                if not sentence:
                    continue
                if len(sentence) <= max_chars:
                    units.append((sentence, line_no, False))
                    continue
                # 一句話本身就超過,照逗號再切,切完只在這句裡面重新湊。
                # 沒有逗號可切的就只好讓它超過。
                clauses = [c.strip() for c in _CLAUSE_END.split(sentence) if c.strip()]
                packed: list[str] = []
                for clause in clauses:
                    if packed and len(packed[-1]) + len(clause) <= max_chars:
                        packed[-1] += clause
                    else:
                        packed.append(clause)
                units += [(p, line_no, True) for p in packed]
    return units


def _assemble(units: list[tuple[str, int, bool]], max_chars: int, merge: bool) -> list[str]:
    bubbles: list[str] = []
    last_line = 0
    pending = ""  # 單獨一個「欸...」是下一句的開頭,先扣著
    for text, line, fragment in units:
        if _INTERJECTION.match(text):
            pending += text
            continue
        if pending:
            text, pending = pending + text, ""
        # 只有同一行裡的短句才併(「嗯。好。」)。跨行併起來會變成「我是小葵二十四歲…」
        # 這種沒有標點的一長串。半句也不往前黏,不然「欸,」會黏到上一句後面。
        can_merge = merge and bubbles and line == last_line and not fragment
        if can_merge and len(bubbles[-1]) + len(text) <= max_chars:
            bubbles[-1] += text
        else:
            bubbles.append(text)
        last_line = line
    # 扣著的「欸...」後面沒東西了 —— 掛在最後很怪,不送。
    if pending and not bubbles:
        bubbles.append(pending)

    # 照逗號切的時候,前一段會留著逗號結尾。真人不會用逗號結束一則訊息。
    return [b.rstrip(_TRAILING_PAUSE) or b for b in bubbles]


def _pack_all(chunks: list[str], max_chars: int, max_bubbles: int) -> list[str]:
    """每則不超過 max_chars。句子能各自一則就各自一則,超過 max_bubbles 才把同一行的短句併起來。

    原本是一律先併 —— 結果兩句加起來沒超過字數就變成一則,這輪明明可以回兩則,
    實測十二輪只有一輪真的回了兩則。
    """
    units = _units(chunks, max_chars)
    loose = _assemble(units, max_chars, merge=False)
    if len(loose) <= max_bubbles:
        return loose
    return _assemble(units, max_chars, merge=True)


def _pack(chunks: list[str], max_chars: int, max_bubbles: int) -> list[str]:
    bubbles = _pack_all(chunks, max_chars, max_bubbles)
    # 第一則是回答,再長也得送。後面的超過字數又切不開,就當成不重要的刪掉。
    over = [b for b in bubbles[1:] if len(b) > max_chars]
    if over:
        log.info("刪掉 %d 則切不開又超過 %d 字的補充", len(over), max_chars)
        bubbles = bubbles[:1] + [b for b in bubbles[1:] if len(b) <= max_chars]
    if len(bubbles) > max_bubbles:
        log.info("回覆太長,後面 %d 段不送", len(bubbles) - max_bubbles)
    return bubbles[:max_bubbles]


# 表情符號,連同組合用的變體選擇符和零寬連接符。
_EMOJI = re.compile("[\U0001f300-\U0001faff\u2600-\u27bf\ufe0f\u200d]")
_TILDE = re.compile(r"[～~]+")  # noqa: RUF001
_SPACES = re.compile(r"\s{2,}")


def tone_down(
    bubbles: list[str],
    her_recent: list[str],
    emoji_gap: int = 5,
    strip_tilde: bool = True,
) -> list[str]:
    """把讓人覺得油的小動作收掉:表情符號太密、拉長音的波浪號。

    her_recent 是她最近幾則回覆(舊到新)。最近 emoji_gap 則裡用過表情,
    這則就一個都不留;沒用過的話,整則回覆最多留第一個。
    """
    keep_one = emoji_gap <= 0 or not any(_EMOJI.search(t) for t in her_recent[-emoji_gap:])
    out: list[str] = []
    for bubble in bubbles:
        if strip_tilde:
            bubble = _TILDE.sub("", bubble)
        if emoji_gap > 0:
            first = _EMOJI.search(bubble) if keep_one else None
            if first:
                bubble = bubble[: first.end()] + _EMOJI.sub("", bubble[first.end() :])
                keep_one = False
            else:
                bubble = _EMOJI.sub("", bubble)
        bubble = _SPACES.sub(" ", bubble).strip()
        if bubble:
            out.append(bubble)
    return out


def overflows(raw: str, max_chars: int, max_bubbles: int) -> bool:
    """這段話照規矩切完,是不是會有東西被丟掉、或有一則還是超過字數。

    會的話就值得請模型自己重講一次 —— 程式只會從後面砍,不知道哪句重要。
    """
    chunks = [c.strip() for c in re.split(r"\n\s*\n", raw.strip()) if c.strip()]
    if not chunks:
        return False
    bubbles = _pack_all(chunks, max_chars, max_bubbles)
    return len(bubbles) > max_bubbles or any(len(b) > max_chars for b in bubbles)


def _regroup(sentences: list[str], max_bubbles: int) -> list[str]:
    """把句子平均分成最多 max_bubbles 組,別讓某一則特別長。"""
    groups: list[str] = []
    target = max(1, len(sentences) // max_bubbles + (1 if len(sentences) % max_bubbles else 0))
    for i in range(0, len(sentences), target):
        groups.append("".join(sentences[i : i + target]))
    return groups[:max_bubbles]
