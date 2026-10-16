"""--selftest 的失敗路徑。全綠路徑要真的 API,留給使用者自己跑。"""

from __future__ import annotations

from dataclasses import replace

import pytest

from personabot.cli import selftest
from personabot.config import reset_settings


@pytest.fixture
def no_credentials(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    reset_settings()


async def test_missing_persona_fails_first(settings, tmp_path, capsys):
    code = await selftest(settings, str(tmp_path / "nope.yaml"))
    out = capsys.readouterr().out
    assert code == 1
    assert "角色卡" in out
    assert "資料庫" not in out  # 角色卡沒過就不往下驗


async def test_missing_key_stops_before_the_api(settings, tmp_path, no_credentials, capsys):
    persona = tmp_path / "p.yaml"
    persona.write_text("name: 小葵\ngreeting: 嗨\n", encoding="utf-8")
    settings = replace(settings, db_path=tmp_path / "t.db")

    code = await selftest(settings, str(persona))
    out = capsys.readouterr().out

    assert code == 1
    assert "角色卡" in out and "資料庫" in out
    assert "ANTHROPIC_API_KEY" in out
    assert "模型連線" not in out


async def test_unwritable_db_is_reported(settings, tmp_path, capsys):
    persona = tmp_path / "p.yaml"
    persona.write_text("name: 小葵\n", encoding="utf-8")
    blocker = tmp_path / "blocked"
    blocker.write_text("我是檔案不是資料夾", encoding="utf-8")
    settings = replace(settings, db_path=blocker / "sub" / "t.db")

    code = await selftest(settings, str(persona))
    out = capsys.readouterr().out

    assert code == 1
    assert "寫不進去" in out


# ---- 本地後端 ----


@pytest.fixture
def stub_server():
    """在背景執行緒跑一個最小的 OpenAI 相容伺服器,回傳 base_url。"""
    import json
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    state = {"models": []}

    class Handler(BaseHTTPRequestHandler):
        def _send(self, obj):
            body = json.dumps(obj).encode()
            self.send_response(200)
            self.send_header("content-type", "application/json")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            self._send({"data": [{"id": m} for m in state["models"]]})

        def do_POST(self):
            self.rfile.read(int(self.headers.get("content-length", 0)))
            self._send({"choices": [{"message": {"role": "assistant", "content": "嗯,在。"}}]})

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    state["base_url"] = f"http://127.0.0.1:{server.server_port}/v1"
    try:
        yield state
    finally:
        server.shutdown()


def _local(settings, tmp_path, base_url: str):
    from personabot.config import LocalConfig

    return replace(
        settings,
        backend="local",
        db_path=tmp_path / "t.db",
        chat_model="qwen3.5:9b-q8_0",
        utility_model="qwen3.5:2b",
        local=LocalConfig(base_url=base_url, api_key="ollama", timeout=5.0),
    )


@pytest.fixture
def persona_file(tmp_path):
    path = tmp_path / "p.yaml"
    path.write_text("name: 小葵\ngreeting: 嗨\n", encoding="utf-8")
    return str(path)


async def test_local_server_down_is_reported(settings, tmp_path, persona_file, capsys):
    # 127.0.0.1:1 一定沒人在聽,會立刻被拒絕
    code = await selftest(_local(settings, tmp_path, "http://127.0.0.1:1/v1"), persona_file)
    out = capsys.readouterr().out

    assert code == 1
    assert "後端:local" in out
    assert "本地伺服器" in out and "連不上" in out
    assert "ollama serve" in out
    assert "模型連線" not in out


async def test_unpulled_models_are_named_with_pull_commands(
    settings, tmp_path, persona_file, stub_server, capsys
):
    stub_server["models"] = ["llama3:8b"]

    code = await selftest(_local(settings, tmp_path, stub_server["base_url"]), persona_file)
    out = capsys.readouterr().out

    assert code == 1
    assert "本地伺服器" in out
    assert "缺少 qwen3.5:9b-q8_0、qwen3.5:2b" in out
    assert "ollama pull qwen3.5:9b-q8_0" in out


async def test_local_all_green(settings, tmp_path, persona_file, stub_server, capsys):
    stub_server["models"] = ["qwen3.5:9b-q8_0", "qwen3.5:2b"]

    code = await selftest(_local(settings, tmp_path, stub_server["base_url"]), persona_file)
    out = capsys.readouterr().out

    assert code == 0
    assert "模型已下載" in out
    assert "模型連線" in out
    assert "記憶寫入" in out
    assert "全部通過" in out


async def test_latest_suffix_counts_as_present(
    settings, tmp_path, persona_file, stub_server, capsys
):
    # Ollama 列出來常常帶 :latest,不該因此判定為沒下載
    stub_server["models"] = ["qwen3.5:9b-q8_0:latest", "qwen3.5:2b:latest"]

    code = await selftest(_local(settings, tmp_path, stub_server["base_url"]), persona_file)

    assert code == 0
    assert "缺少" not in capsys.readouterr().out
