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
