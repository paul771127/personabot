"""FastAPI:三個平台的 webhook 都掛在這裡。

Webhook 要盡快回 200,不然平台會重送。所以收下來驗完簽就先回 200,
真正的產生回覆跟送出交給 BackgroundTasks —— Starlette 會先把回應送出去才跑它。
"""

from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager

import httpx
from fastapi import BackgroundTasks, FastAPI, Request, Response
from fastapi.responses import JSONResponse, PlainTextResponse

from .adapters import Adapter, LineAdapter, MessengerAdapter, TelegramAdapter
from .config import get_settings, missing_credentials
from .engine import Engine
from .types import InboundMessage

log = logging.getLogger(__name__)


def build_app(engine: Engine | None = None) -> FastAPI:
    settings = get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.settings = settings
        app.state.engine = engine or Engine(settings)
        app.state.http = httpx.AsyncClient(timeout=10.0)
        app.state.adapters = {
            "line": LineAdapter(settings, app.state.http),
            "messenger": MessengerAdapter(settings, app.state.http),
            "telegram": TelegramAdapter(settings, app.state.http),
        }
        enabled = [
            name
            for name, cfg in (
                ("line", settings.line),
                ("messenger", settings.messenger),
                ("telegram", settings.telegram),
            )
            if cfg.enabled
        ]
        problem = missing_credentials()
        if problem:
            log.error("%s", problem)
        if not enabled:
            log.warning("沒有任何平台設定完成,webhook 會一律驗簽失敗")
        log.info(
            "personabot 啟動,人設=%s,已設定的平台=%s",
            app.state.engine.persona.name,
            ",".join(enabled) or "無",
        )
        try:
            yield
        finally:
            await app.state.engine.drain()
            await app.state.http.aclose()

    app = FastAPI(title="personabot", lifespan=lifespan)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {
            "ok": not missing_credentials(),
            "persona": app.state.engine.persona.name,
            "platforms": {
                "line": settings.line.enabled,
                "messenger": settings.messenger.enabled,
                "telegram": settings.telegram.enabled,
            },
        }

    @app.post("/webhook/line")
    async def line_webhook(request: Request, background: BackgroundTasks) -> Response:
        return await _handle_webhook(app, request, "line", background)

    @app.get("/webhook/messenger")
    async def messenger_verify(request: Request) -> Response:
        adapter: MessengerAdapter = app.state.adapters["messenger"]
        params = request.query_params
        if adapter.verify_subscription(params.get("hub.mode", ""), params.get("hub.verify_token", "")):
            return PlainTextResponse(params.get("hub.challenge", ""))
        return PlainTextResponse("verification failed", status_code=403)

    @app.post("/webhook/messenger")
    async def messenger_webhook(request: Request, background: BackgroundTasks) -> Response:
        return await _handle_webhook(app, request, "messenger", background)

    @app.post("/webhook/telegram")
    async def telegram_webhook(request: Request, background: BackgroundTasks) -> Response:
        return await _handle_webhook(app, request, "telegram", background)

    return app


async def _handle_webhook(
    app: FastAPI, request: Request, platform: str, background: BackgroundTasks
) -> Response:
    adapter: Adapter = app.state.adapters[platform]
    body = await request.body()

    if not adapter.verify(body, request.headers):
        log.warning("%s webhook 驗簽失敗", platform)
        return JSONResponse({"error": "invalid signature"}, status_code=403)

    try:
        payload = json.loads(body or b"{}")
    except ValueError:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    for inbound in adapter.parse(payload):
        background.add_task(_respond, app, adapter, inbound)

    return JSONResponse({"ok": True})


async def _respond(app: FastAPI, adapter: Adapter, inbound: InboundMessage) -> None:
    engine: Engine = app.state.engine
    try:
        # LINE 的 webhook 不帶名字,但打個 API 就能拿到。
        if isinstance(adapter, LineAdapter) and not inbound.display_name:
            inbound.display_name = await adapter.fetch_display_name(inbound.user_id)

        await adapter.show_typing(inbound)
        reply = await engine.handle(inbound)
        if reply:
            await adapter.send(inbound, reply)
    except Exception:  # 背景任務不能把例外丟回 webhook
        log.exception("處理 %s 訊息時出錯", adapter.name)


app = build_app()
