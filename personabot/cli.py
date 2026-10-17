"""本機測試用:不接任何平台,直接在終端機跟角色聊天。

    python -m personabot.cli
    python -m personabot.cli --persona personas/aoi.yaml
    python -m personabot.cli --telegram        # 用 long polling 真的接上 Telegram
    python -m personabot.cli --selftest        # 一次驗完五個環節,看哪裡沒通
    python -m personabot.cli --smoke           # 跑腳本對話,挑出小模型常見的毛病
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .adapters import TelegramAdapter
from .config import Settings, get_settings, missing_credentials
from .engine import Engine
from .persona import Persona
from .store import Store
from .types import InboundMessage, Reply


async def repl(engine: Engine) -> None:
    greeting = engine.greeting()
    name = engine.persona.name
    print(f"— 跟「{name}」聊天中。Ctrl-C 或輸入 /quit 離開。/help 看指令。—\n")
    for bubble in greeting.bubbles:
        print(f"{name}:{bubble}")

    loop = asyncio.get_running_loop()
    while True:
        try:
            line = await loop.run_in_executor(None, lambda: input("\n你:"))
        except (EOFError, KeyboardInterrupt):
            break
        line = line.strip()
        if not line:
            continue
        if line in {"/quit", "/exit"}:
            break

        reply = await engine.handle(
            InboundMessage(platform="cli", user_id="local", text=line, display_name="你")
        )
        for bubble in reply.bubbles:
            print(f"{name}:{bubble}")

    print("\n— 等背景的記憶整理跑完 —")
    await engine.drain()
    print("掰。")


async def run_telegram(engine: Engine) -> None:
    settings = get_settings()
    if not settings.telegram.enabled:
        sys.exit("沒有設 TELEGRAM_BOT_TOKEN")
    adapter = TelegramAdapter(settings)
    await adapter.delete_webhook()  # polling 跟 webhook 不能並存

    async def handler(inbound: InboundMessage) -> Reply:
        return await engine.handle(inbound)

    try:
        await adapter.poll(handler)
    except KeyboardInterrupt:
        pass
    finally:
        await engine.drain()
        await adapter.aclose()


# ── 自我檢查 ──────────────────────────────────────────────────────────

OK = "\033[32m✓\033[0m"
BAD = "\033[31m✗\033[0m"
WARN = "\033[33m!\033[0m"


def _report(label: str, passed: bool, detail: str = "") -> bool:
    print(f"  {OK if passed else BAD} {label}" + (f" — {detail}" if detail else ""))
    return passed


def _warn(label: str, detail: str = "") -> None:
    """有疑慮但不致命,不要用紅叉嚇人。"""
    print(f"  {WARN} {label}" + (f" — {detail}" if detail else ""))


async def selftest(settings: Settings, persona_path: str | None) -> int:
    """逐項確認:角色卡、資料庫、金鑰、模型往返、記憶。回傳 exit code。"""
    print("personabot 自我檢查\n")

    # 1. 角色卡
    path = persona_path or settings.persona_path
    try:
        persona = Persona.load(path)
    except (FileNotFoundError, ValueError) as exc:
        _report("角色卡", False, str(exc))
        print("\n先把角色卡準備好再試一次。")
        return 1
    _report("角色卡", True, f"{persona.name}({path})")

    # 2. 資料庫:真的往設定的路徑寫寫看
    try:
        store = Store(settings.db_path)
        probe = store.get_or_create_conversation("selftest", "probe", persona.id)
        store.add_message(probe.id, "user", "probe")
        store.reset_conversation(probe.id)
    except Exception as exc:  # 這裡就是要把任何失敗原因講給使用者聽
        _report("資料庫", False, f"{settings.db_path} 寫不進去:{exc}")
        return 1
    _report("資料庫", True, str(settings.db_path))

    # 3. 後端:雲端看金鑰,本地看伺服器與模型有沒有拉下來
    print(f"\n  後端:{settings.backend}")
    if settings.backend == "local":
        if not await _check_local(settings):
            return 1
    else:
        problem = missing_credentials(settings)
        if not _report("模型金鑰", not problem, problem.replace("\n", " ") if problem else "已設定"):
            print("\n金鑰沒設,後面兩項沒辦法驗。")
            return 1

    # 4. 真的打一次 API
    engine = Engine(settings=settings, persona=persona, store=store)
    probe_msg = InboundMessage(
        platform="selftest",
        user_id="probe",
        text="你好,這是連線測試。請隨便回我一句話就好。",
        display_name="測試",
    )
    try:
        reply = await engine.brain.chat(persona=persona, history=_probe_history(probe_msg.text))
    except Exception as exc:  # 連線失敗的原因要原汁原味給使用者
        _report("模型連線", False, f"{type(exc).__name__}: {exc}")
        if settings.backend == "local":
            print("\n常見原因:模型還沒 pull、記憶體不夠被 OOM、或回應超過 LOCAL_TIMEOUT 秒。")
        else:
            print("\n常見原因:金鑰打錯、額度用完、或這台機器連不出去。")
        return 1
    _report("模型連線", True, f"{settings.chat_model} 回了:{reply[:40]}")

    # 5. 走完整流程,確認記憶真的寫進去
    conv_reply = await engine.handle(probe_msg)
    await engine.drain()
    conv = store.get_or_create_conversation("selftest", "probe", persona.id)
    stored = store.recent_messages(conv.id, 10)
    memory_ok = len(stored) >= 2
    _report("記憶寫入", memory_ok, f"存了 {len(stored)} 則訊息、{len(store.get_facts(conv.id))} 則長期記憶")

    store.reset_conversation(conv.id)  # 把測試痕跡清掉,別污染真實對話

    print(f"\n  {persona.name} 說:{conv_reply.text}")
    if not memory_ok:
        print("\n對話跑得起來,但記憶沒有寫進去。先別急著接平台,把上面那行貼出來看。")
        return 1
    print("\n全部通過。可以直接 python -m personabot.cli 開始聊了。")
    return 0


async def _check_local(settings: Settings) -> bool:
    """本地後端:伺服器活著嗎?要用的模型拉下來了嗎?"""
    import httpx

    url = settings.local.base_url
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(
                f"{url}/models", headers={"Authorization": f"Bearer {settings.local.api_key}"}
            )
    except httpx.HTTPError as exc:
        _report("本地伺服器", False, f"{url} 連不上:{exc}")
        print("\n先把伺服器開起來:ollama serve(LM Studio 則是打開 Local Server 分頁)。")
        return False

    if response.status_code >= 400:
        _report("本地伺服器", False, f"{url} 回 {response.status_code}")
        return False

    available = {m.get("id", "") for m in (response.json().get("data") or [])}
    _report("本地伺服器", True, f"{url}({len(available)} 個模型)")

    # Ollama 會回 "qwen3.5:9b-q8_0" 這種完整名稱,也可能省略 :latest
    def present(name: str) -> bool:
        return name in available or f"{name}:latest" in available

    wanted = dict.fromkeys([settings.chat_model, settings.utility_model])  # 去重且保序
    missing = [m for m in wanted if not present(m)]
    if not missing:
        _report("模型已列出", True, " + ".join(wanted))
        return True

    # 名稱沒列出不代表真的不能用:Strata 這類伺服器只掛一個模型,任何名稱都收。
    # 所以這裡只警告,真正的答案交給下一步真的打一次 API。
    _warn("模型已列出", "伺服器沒列出 " + "、".join(missing))
    print("    Ollama 的話先拉下來:" + "、".join(f"ollama pull {n}" for n in missing))
    print("    Strata 這類只掛單一模型的伺服器不看名稱,繼續往下驗。")
    return True


def _probe_history(text: str):
    from .store import Message

    return [Message(id=1, role="user", text=text, created_at=0.0)]


def main() -> None:
    parser = argparse.ArgumentParser(description="personabot")
    parser.add_argument("--persona", help="角色卡路徑,預設讀 PERSONA_PATH")
    parser.add_argument("--telegram", action="store_true", help="用 long polling 接上 Telegram")
    parser.add_argument("--selftest", action="store_true", help="驗完五個環節就離開")
    parser.add_argument("--smoke", action="store_true", help="跑一段腳本對話並檢查常見毛病")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()

    if args.selftest:
        sys.exit(asyncio.run(selftest(settings, args.persona)))

    problem = missing_credentials()
    if problem:
        sys.exit(problem)

    persona = Persona.load(args.persona) if args.persona else Persona.load(settings.persona_path)
    engine = Engine(settings=settings, persona=persona)

    if args.smoke:
        from .smoke import run_smoke

        sys.exit(asyncio.run(run_smoke(engine)))

    if args.telegram:
        asyncio.run(run_telegram(engine))
    else:
        asyncio.run(repl(engine))


if __name__ == "__main__":
    main()
