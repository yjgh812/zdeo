# -*- coding: utf-8 -*-
"""
Fincept Terminal 一键启动器（屠龙定制）
- 强制使用存活的官方 API 端点 api.fincept.in（默认 zrok 隧道端点已 502）
- 关闭 strict 模式：服务端 guest 路由已下线(/device/register 404)，
  非 strict 模式下 Guest 按钮走本地 fallback 直接进入主界面
- 用法: D:/zcodesj/fincept/venv/Scripts/python.exe D:/zcodesj/fincept/start_fincept.py
"""
import os
import sys

os.environ["FINCEPT_API_URL"] = "https://api.fincept.in"

from fincept_terminal.Utils.config import APIConfig

APIConfig.set_strict_mode(False)

from fincept_terminal.FinceptTerminalStart import start_terminal

if __name__ == "__main__":
    start_terminal()
