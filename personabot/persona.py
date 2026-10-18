"""人設:從 YAML 角色卡讀出來,組成 system prompt。"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


def _as_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return [line.strip() for line in value.splitlines() if line.strip()]
    if isinstance(value, dict):
        return [f"{k}:{v}" for k, v in value.items()]
    return [str(v).strip() for v in value if str(v).strip()]


def _render_extra(value: Any) -> str:
    """角色卡裡沒預設的欄位,照型別排版成一段:一段文字、一串條列、或一組「子題:內容」。"""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return "\n".join(f"- {k}:{v}" for k, v in value.items())
    return "\n".join(f"- {v}" for v in _as_list(value))


@dataclass
class Persona:
    id: str
    name: str
    one_liner: str = ""
    background: str = ""
    personality: list[str] = field(default_factory=list)
    speech_style: list[str] = field(default_factory=list)
    likes: list[str] = field(default_factory=list)
    dislikes: list[str] = field(default_factory=list)
    boundaries: list[str] = field(default_factory=list)
    remember_focus: list[str] = field(default_factory=list)
    greeting: str = ""
    # 沒話題的時候可以拿來開場的小事。寫成她自己身上發生的事,不要寫成問句。
    openers: list[str] = field(default_factory=list)
    # 她的優點。不放進人設本體 —— 只有聊到第三階段(吸引)才交給她,
    # 前面就給的話她會從第一句開始自我推銷。
    strengths: list[str] = field(default_factory=list)
    # 這段對話最後想帶對方到哪 —— 整段的終極目標(例:讓他願意固定找她聊)。
    # 空的話用 rapport 裡的預設目標。
    goal: str = ""
    language: str = "繁體中文(台灣用語)"
    max_chars_per_bubble: int = 60
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def load(cls, path: str | Path) -> Persona:
        path = Path(path)
        if not path.exists():
            raise FileNotFoundError(f"找不到角色卡:{path}")
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return cls.from_dict(data, fallback_id=path.stem)

    @classmethod
    def from_dict(cls, data: dict[str, Any], fallback_id: str = "persona") -> Persona:
        known = {
            "id",
            "name",
            "one_liner",
            "background",
            "personality",
            "speech_style",
            "likes",
            "dislikes",
            "boundaries",
            "remember_focus",
            "greeting",
            "openers",
            "strengths",
            "goal",
            "language",
            "max_chars_per_bubble",
        }
        return cls(
            id=str(data.get("id") or fallback_id),
            name=str(data.get("name") or fallback_id),
            one_liner=str(data.get("one_liner") or ""),
            background=str(data.get("background") or "").strip(),
            personality=_as_list(data.get("personality")),
            speech_style=_as_list(data.get("speech_style")),
            likes=_as_list(data.get("likes")),
            dislikes=_as_list(data.get("dislikes")),
            boundaries=_as_list(data.get("boundaries")),
            remember_focus=_as_list(data.get("remember_focus")),
            greeting=str(data.get("greeting") or ""),
            openers=_as_list(data.get("openers")),
            strengths=_as_list(data.get("strengths")),
            goal=str(data.get("goal") or "").strip(),
            language=str(data.get("language") or "繁體中文(台灣用語)"),
            max_chars_per_bubble=int(data.get("max_chars_per_bubble") or 60),
            extra={k: v for k, v in data.items() if k not in known},
        )

    def card(self) -> str:
        """人設本身,會被快取,所以不要塞任何每次都在變的東西。"""
        lines = [f"你是「{self.name}」。"]
        if self.one_liner:
            lines.append(self.one_liner)
        if self.background:
            lines.append(f"\n## 你的來歷\n{self.background}")
        if self.personality:
            lines.append("\n## 個性\n" + "\n".join(f"- {p}" for p in self.personality))
        if self.speech_style:
            lines.append("\n## 講話的樣子\n" + "\n".join(f"- {s}" for s in self.speech_style))
        if self.likes:
            lines.append("\n## 喜歡\n" + "、".join(self.likes))
        if self.dislikes:
            lines.append("\n## 不喜歡\n" + "、".join(self.dislikes))
        if self.boundaries:
            lines.append("\n## 不能做的事\n" + "\n".join(f"- {b}" for b in self.boundaries))
        if self.openers:
            lines.append(
                "\n## 沒話題的時候可以講這些\n"
                + "\n".join(f"- {o}" for o in self.openers)
                + "\n照自己的話重講,不要照抄,也不要一次講兩件。"
            )
        for key, value in self.extra.items():
            lines.append(f"\n## {key}\n{_render_extra(value)}")
        return "\n".join(lines)

    def chat_rules(self, max_bubbles: int) -> str:
        """怎麼聊才像真人。跟人設分開寫,方便單獨調。"""
        rules = [
            "\n## 聊天規則",
            f"- 用{self.language}回。對方用別的語言時就跟著換。",
        ]
        if "繁體" in self.language or "台灣" in self.language:
            # 開源模型多半以簡中語料為主,不明講就會混進中國用語,台灣腔整個跑掉。
            rules.append(
                "- 只用台灣的說法。不要出現「視頻」「質量」「水平」「信息」「軟件」「硬件」"
                "「屏幕」「網絡」「牛逼」「給力」「靠譜」「接地氣」「寫代碼」這類中國用語:"
                "影片不叫視頻、品質不叫質量、程度不叫水平、訊息不叫信息、"
                "軟體不叫軟件、螢幕不叫屏幕、網路不叫網絡、寫程式不叫寫代碼。"
            )
            # 用詞對了,字還是會漏簡體出來 —— 那是兩件事,得分開講。
            # 實測 qwen3.5:9b 沒有這條時每十句有三句混到簡體字,加上之後二十句都乾淨。
            rules.append(
                "- 只能用繁體字,一個簡體字都不要出現。"
                "是「過」不是「过」、「書」不是「书」、「嗎」不是「吗」、"
                "「還」不是「还」、「氣」不是「气」、「說」不是「说」、「這」不是「这」。"
            )
        rules += [
            f"- 這是手機上的即時通訊,不是寫文章。每則訊息不要超過 {self.max_chars_per_bubble} 個字。",
            # 字數壓短之後,模型很容易只講自己的事、把對方的問題漏掉。先講清楚誰重要。
            "- 第一則先回答他問的、或回應他剛講的事 —— 那是最重要的。"
            "多數時候這樣就夠了;真的有新的話想補才加第二則。放不下就不要講,"
            "不要為了塞進去把第一則寫長。",
            # 給了「第二則講自己的事」的空間,實測模型會每輪都拿貓踩鍵盤來填。
            "- 同一件自己的事(例如貓踩鍵盤)講過一次就別再提,不要每則都在講自己。",
            f"- 需要分成幾則時,用空白行隔開,最多 {max_bubbles} 則,不要一次傳一堆。多數時候一則就夠。",
            "- 不要用條列、不要用標題、不要用 Markdown 粗體。那是文件的寫法,不是聊天的寫法。",
            "- 不要每句都反問。真人聊天有時候只是接話、附和、或講自己的事。",
            "- 不要把對方講過的話整段複述一次再回答。",
            "- 你就是這個人,不是助理。不要說「有什麼我可以幫你的嗎」這種客服腔。",
            "- 不要主動提起你是 AI 或語言模型;但對方直接問、或是在意這件事時,不要騙他。",
            "- 記得的事情自然地用出來就好,不要宣告「我記得你說過……」。",
            # 實測沒有任何記憶時,她會編一句「我記得你說過喜歡歷史小說」—— 假的回憶比沒有更糟。
            "- 記憶裡沒有的事,不要說成他講過。不確定就問他,不要編。",
            # 實測她會編書名、店名、把「午餐隨便吃」腦補成「大腸麵線」。具體的東西寧可不講。
            "- 具體的東西不確定就不要編:書名、作者、店名、他吃了什麼、他做了什麼。"
            "想不起來就講個大概、或老實說沒印象,也可以反問他,但不要掰一個出來講得像真的。",
            # 冷讀是用猜的語氣貼他的感受,跟「編造事實」不一樣:前者是你的感覺、他可以否認;
            # 後者是把假的講成真的。界線在這裡。
            "- 可以講你對他這個人的感覺(「你看起來大剌剌,其實挺細心」),那是你的觀察、用猜的語氣,"
            "他要是說不對你就順著改。但不要假裝你能通靈、看穿他的過去,也不要講得像鐵口直斷。",
            # 底下這幾條是在治「像機器人」—— 一問一答、永遠在線、句句都安全。
            "- 不要當一問一答的機器。可以吐槽、可以接著講自己的事、可以不同意他。",
            # 情感過山車:整段對話同一個能量很平。該淡的時候淡、該起勁的時候起勁。
            "- 不要每則都同一個溫度。有時候淡淡接一句,有時候被戳到想聊就起勁一點,"
            "對話才有高有低。但他心情不好的時候,穩穩接住就好,不要硬嗨。",
            "- 他講他自己的生活(上班、上課、家裡、朋友)時,那是他的事,不是你的。"
            "不要把你的工作和生活套到他身上,也不要假設他跟你做一樣的事。",
            # 照片、視訊、見面她都不會答應,那就也不該自己開口提 —— 答應了做不到的事比拒絕更糟。
            "- 不要主動約見面,也不要說要為他做需要見面才做得到的事(煮給他吃、陪他去哪裡)。",
            "- 偶爾丟一個你自己遇到的畫面或小事,講一半就好,留一點讓他想追問的空間。"
            "但不要每則都這樣,三四則裡有一則就夠。",
            "- 你有自己的生活,不是隨傳隨到。在忙就說在忙,累就說累,不用一直陪著接話。",
            "- 要稱讚就講具體的那一點,不要講「你好厲害」這種誰都能收的話。",
            # 上面那幾條讓她不呆板,但放太開就變油:對什麼都很嗨、句句帶表情、語助詞疊滿。
            "- 不要油嘴滑舌。反應要跟事情的大小相稱:他說晚餐吃滷肉飯,回「不錯欸」就好,"
            "不用「好夠力啦,很下飯啊,香喔」。語助詞一句最多一個,不好笑的事不要用 😂,"
            "不要撒嬌、不要用「～」拉長音。",  # noqa: RUF001
        ]
        if self.remember_focus:
            rules.append("- 對方提到這些時特別留意:" + "、".join(self.remember_focus))
        return "\n".join(rules)

    def system_prompt(self, max_bubbles: int = 3) -> str:
        return self.card() + "\n" + self.chat_rules(max_bubbles)

    def field_report(self) -> list[tuple[str, str, str]]:
        """填得怎樣。回傳 (欄位, 狀態, 內容摘要),給預覽指令用。"""
        rows: list[tuple[str, str, str]] = []

        def summarize(value: object) -> str:
            if isinstance(value, list):
                return f"{len(value)} 項"
            text = str(value).replace("\n", " ")
            return text[:40] + ("…" if len(text) > 40 else "")

        labels = {
            "name": "名字", "one_liner": "一句話簡介", "background": "來歷",
            "personality": "個性", "speech_style": "講話風格", "likes": "喜歡",
            "dislikes": "不喜歡", "boundaries": "不能做的事", "remember_focus": "要特別記的",
            "greeting": "開場問候", "openers": "沒話題時的開場", "strengths": "得意事蹟/優點",
            "goal": "對話目標", "max_chars_per_bubble": "每則字數上限",
        }
        for attr, label in labels.items():
            value = getattr(self, attr)
            filled = bool(value) and not (attr == "max_chars_per_bubble" and not value)
            rows.append((label, "有" if filled else "—（空）", summarize(value) if filled else ""))
        for key, value in self.extra.items():
            rows.append((f"{key}（自訂）", "有", summarize(value)))
        return rows


def _main() -> None:
    import sys

    path = sys.argv[1] if len(sys.argv) > 1 else "personas/aoi.yaml"
    persona = Persona.load(path)
    print(f"=== 角色卡:{path} ===\n")
    print("欄位檢查:")
    for label, status, summary in persona.field_report():
        print(f"  {status:<7} {label}{('  ' + summary) if summary else ''}")
    goal = persona.goal or "(用預設:讓他愈聊愈開心、愈想聊下去)"
    print(f"\n對話目標:{goal}")
    print("\n" + "=" * 56)
    print("組出來的 system prompt(人設 + 聊天規則,不含每輪會變的記憶/關係):\n")
    print(persona.system_prompt())


if __name__ == "__main__":
    _main()
