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


def _pack_all(chunks: list[str], max_chars: int) -> list[str]:
    """切成句子(還太長就照逗號再切),短的併在一起,每則不超過 max_chars。

    塞不進 max_bubbles 則的部分就不送了。真人傳訊息也不會一口氣講完,
    寧可少講一點,也不要一次跳出一整串。
    """
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
        can_merge = bubbles and line == last_line and not fragment
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


def _pack(chunks: list[str], max_chars: int, max_bubbles: int) -> list[str]:
    bubbles = _pack_all(chunks, max_chars)
    # 第一則是回答,再長也得送。後面的超過字數又切不開,就當成不重要的刪掉。
    over = [b for b in bubbles[1:] if len(b) > max_chars]
    if over:
        log.info("刪掉 %d 則切不開又超過 %d 字的補充", len(over), max_chars)
        bubbles = bubbles[:1] + [b for b in bubbles[1:] if len(b) <= max_chars]
    if len(bubbles) > max_bubbles:
        log.info("回覆太長,後面 %d 段不送", len(bubbles) - max_bubbles)
    return bubbles[:max_bubbles]


def overflows(raw: str, max_chars: int, max_bubbles: int) -> bool:
    """這段話照規矩切完,是不是會有東西被丟掉、或有一則還是超過字數。

    會的話就值得請模型自己重講一次 —— 程式只會從後面砍,不知道哪句重要。
    """
    chunks = [c.strip() for c in re.split(r"\n\s*\n", raw.strip()) if c.strip()]
    if not chunks:
        return False
    bubbles = _pack_all(chunks, max_chars)
    return len(bubbles) > max_bubbles or any(len(b) > max_chars for b in bubbles)


def _regroup(sentences: list[str], max_bubbles: int) -> list[str]:
    """把句子平均分成最多 max_bubbles 組,別讓某一則特別長。"""
    groups: list[str] = []
    target = max(1, len(sentences) // max_bubbles + (1 if len(sentences) % max_bubbles else 0))
    for i in range(0, len(sentences), target):
        groups.append("".join(sentences[i : i + target]))
    return groups[:max_bubbles]
