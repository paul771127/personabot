"""本機測試用:不接任何平台,直接在終端機跟角色聊天。

    python -m personabot.cli
    python -m personabot.cli --persona personas/aoi.yaml
    python -m personabot.cli --telegram        # 用 long polling 真的接上 Telegram
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from .adapters import TelegramAdapter
from .config import get_settings, missing_credentials
from .engine import Engine
from .persona import Persona
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


def main() -> None:
    parser = argparse.ArgumentParser(description="personabot")
    parser.add_argument("--persona", help="角色卡路徑,預設讀 PERSONA_PATH")
    parser.add_argument("--telegram", action="store_true", help="用 long polling 接上 Telegram")
    parser.add_argument("--verbose", "-v", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    settings = get_settings()
    problem = missing_credentials()
    if problem:
        sys.exit(problem)

    persona = Persona.load(args.persona) if args.persona else Persona.load(settings.persona_path)
    engine = Engine(settings=settings, persona=persona)

    if args.telegram:
        asyncio.run(run_telegram(engine))
    else:
        asyncio.run(repl(engine))


if __name__ == "__main__":
    main()
