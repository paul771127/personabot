"""personabot:跨平台 AI 角色陪聊機器人。"""

import sys

# requirements.txt 沒辦法表達 Python 版本需求,所以太舊的 Python 會先在 pip
# 那邊炸成一串看不懂的「找不到符合的版本」。這裡至少讓執行時講人話。
#
# ruff 以 py310 為目標,會覺得這個判斷永遠成立所以多餘(UP036) —— 但它要擋的
# 正是「有人用更舊的 Python 跑起來」這件 ruff 看不到的事,所以保留。
# 整個檔案也刻意只用 3.7 解析得過的語法,否則使用者只會看到 SyntaxError。
if sys.version_info < (3, 10):  # noqa: UP036
    raise SystemExit(
        f"personabot 需要 Python 3.10 以上,你現在是 "
        f"{sys.version_info[0]}.{sys.version_info[1]}。\n"
        "anthropic 套件從 1.0 起就要求 3.10,所以舊版 Python 連裝都裝不起來。\n"
        "到 https://www.python.org/downloads/ 裝一個新的,然後重建虛擬環境:\n"
        "    Windows      :  py -3.12 -m venv .venv\n"
        "    Linux/macOS  :  python3.12 -m venv .venv"
    )

__version__ = "0.1.0"
