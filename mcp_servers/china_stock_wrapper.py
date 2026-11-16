#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
china-stock MCP 通道净化 wrapper (知微 2026-09-23)
====================================================
背景: china-stock-mcp 包自身向 stdout 打印非 JSON 启动行(中文/FastMCP banner,
GBK 环境下甚至非 UTF-8), 污染 stdio JSON-RPC 通道(mcp 客户端报
Failed to parse JSONRPC / UnicodeDecodeError, 收尾时 BrokenResourceError)。

方案: 本 wrapper 以子进程拉起真正的 uvx 服务, 对其 stdout 做 JSON 行过滤——
只有能通过 json.loads 的行才转发给客户端, 其余降级到 stderr 留痕;
stdin 原样转发。通道从此纯净, 与服务内部实现解耦。

ZCode config.json:
  "china-stock": {"type":"stdio",
    "command":"C:\\\\Program Files\\\\Python313\\\\python.exe",
    "args":["D:\\\\zcodesj\\\\mcp_servers\\\\china_stock_wrapper.py"],
    "env": {"FASTMCP_SHOW_SERVER_BANNER":"false","PYTHONIOENCODING":"utf-8"}}
"""
import json
import os
import subprocess
import sys
import threading

UVX = r"C:\Program Files\Python313\Scripts\uvx.exe"
CHILD_ENV_EXTRA = {
    "FASTMCP_SHOW_SERVER_BANNER": "false",
    "PYTHONIOENCODING": "utf-8",
}


def pump_out(src, dst):
    """子进程 stdout -> 客户端, 只放行合法 JSON 行。"""
    for raw in iter(src.readline, b""):
        s = raw.strip()
        if not s:
            continue
        try:
            json.loads(s.decode("utf-8"))
        except Exception:
            try:
                print(f"[china-stock-wrapper] dropped non-JSON: {s[:100]!r}",
                      file=sys.stderr)
            except Exception:
                pass
            continue
        dst.write(raw if raw.endswith(b"\n") else raw + b"\n")
        dst.flush()


def pump_in(src, dst):
    try:
        for raw in iter(src.readline, b""):
            dst.write(raw)
            dst.flush()
    except Exception:
        pass


def main():
    env = {**os.environ, **CHILD_ENV_EXTRA}
    p = subprocess.Popen(
        [UVX, "--from", "china-stock-mcp", "china-stock-mcp"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        env=env)
    t = threading.Thread(target=pump_in, args=(sys.stdin.buffer, p.stdin),
                         daemon=True)
    t.start()
    try:
        pump_out(p.stdout, sys.stdout.buffer)
    finally:
        try:
            p.terminate()
        except Exception:
            pass
        sys.stdout.flush()


if __name__ == "__main__":
    main()
