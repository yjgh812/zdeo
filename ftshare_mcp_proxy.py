#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ftshare MCP 本地代理（2026-08-13 知微编写）
==============================================
问题根因：远程服务器 https://market.ft.tech/gateway/mcp 的 HTTP streamable session
实测 300 秒整过期（t=0 成功 / t=300s 返回 404 "Not Found: Session not found"）。
ZCode 客户端池化连接空闲时不保活、404 后不自动重握手，导致空闲超过 5 分钟后
的每次工具调用都失败。

本代理方案：
- 以 stdio 方式被 ZCode 启动（ZCode 侧 ftshare 改为 stdio 类型指向本脚本）
- 转发 initialize / tools/list / tools/call / 通知 到远程
- 每次转发前检查 session 年龄：> RENEW_AFTER(240s) 自动重新 initialize（含
  notifications/initialized），提前于 300s TTL 续期
- 收到 404 "Session not found"（TTL 已过）→ 重新握手并原样重试一次
- 远程响应为 SSE 流（text/event-stream），解析 data: 事件还原 JSON-RPC 消息

用法（ZCode config.json）：
  "ftshare": {
    "type": "stdio",
    "command": "C:\\Users\\yujie\\AppData\\Local\\Microsoft\\WindowsApps\\python.exe",
    "args": ["D:\\zcodesj\\ftshare_mcp_proxy.py"]
  }

环境变量：
  FTSHARE_PROXY_FORCE_STALE=1  测试用：每次调用强制走"session 过期重握手"路径
"""
import json
import os
import sys
import time
import http.client
import ssl

REMOTE_HOST = "market.ft.tech"
REMOTE_PATH = "/gateway/mcp"
SESSION_TTL = 300        # 服务器 session 有效期（实测精确 300s）
RENEW_AFTER = 240        # 提前 60s 续期，留出余量
HTTP_TIMEOUT = 90        # 单次远程请求超时（数据量大时放宽）

_session_id = None
_session_ts = 0.0


def _new_conn():
    # 2026-09-22 C盘重装后 Windows 证书库缺 Sectigo E46 链根（服务器链经
    # ZeroSSL CA 2 -> Sectigo E46 -> USERTrust ECC），改用 certifi 包验证
    try:
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        ctx = ssl.create_default_context()
    return http.client.HTTPSConnection(REMOTE_HOST, 443, context=ctx,
                                       timeout=HTTP_TIMEOUT)


def _remote_request(payload, sid=None):
    """POST 到远程端点。返回 (http_status, session_id_header, body_text)。"""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Content-Length": str(len(body)),
    }
    if sid:
        headers["Mcp-Session-Id"] = sid
    conn = _new_conn()          # 每次新建连接，规避 keep-alive 复用问题
    try:
        conn.request("POST", REMOTE_PATH, body=body, headers=headers)
        resp = conn.getresponse()
        text = resp.read().decode("utf-8", "replace")
        return resp.status, resp.getheader("Mcp-Session-Id"), text
    finally:
        conn.close()


def _parse_sse(text, want_id=None):
    """解析 SSE/JSON 响应，返回 jsonrpc 消息（None 表示无消息）。"""
    t = text.strip()
    if not t:
        return None
    if t.startswith("{"):       # 纯 JSON（非流式）
        try:
            return json.loads(t)
        except Exception:
            pass
    msgs = []
    for block in text.split("\n\n"):
        data_lines = []
        for line in block.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if not data_lines:
            continue
        data = "\n".join(data_lines)
        if not data:
            continue
        try:
            msgs.append(json.loads(data))
        except Exception:
            continue
    if not msgs:
        return None
    if want_id is not None:     # 优先取与请求 id 匹配的最后一条
        for m in reversed(msgs):
            if m.get("id") == want_id:
                return m
    return msgs[-1]


def _reinit(init_id="proxy-init"):
    """重新建立远程 session：initialize -> 记录 id -> initialized 通知。
    init_id: 外部 initialize 请求时传请求 id（保证响应 id 匹配）；
             内部自动续期时用默认值（响应不返回给客户端）。"""
    global _session_id, _session_ts
    status, sid, text = _remote_request({
        "jsonrpc": "2.0",
        "id": init_id,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "ftshare-mcp-proxy", "version": "1.0"},
        },
    })
    msg = _parse_sse(text, want_id="proxy-init")
    if sid:
        _session_id = sid
        _session_ts = time.time()
        try:
            _remote_request({"jsonrpc": "2.0",
                             "method": "notifications/initialized"}, sid)
        except Exception:
            pass
    return msg, status, text


def _fresh_session():
    """确保远程 session 新鲜，返回当前 session id。"""
    global _session_id
    stale = (_session_id is None
             or time.time() - _session_ts > RENEW_AFTER
             or os.environ.get("FTSHARE_PROXY_FORCE_STALE") == "1")
    if stale:
        _reinit()
    return _session_id


def _forward(req):
    """处理单个 JSON-RPC 请求，返回要写回 stdio 的响应消息（None 不写回）。"""
    method = req.get("method")
    rid = req.get("id")

    if method == "initialize":
        msg, _, _ = _reinit(init_id=rid)
        if msg is None:
            return {"jsonrpc": "2.0", "id": rid,
                    "error": {"code": -32000, "message": "remote initialize failed"}}
        return msg

    if method in ("notifications/initialized", "notifications/cancelled"):
        sid = _fresh_session()
        if sid:
            try:
                _remote_request(req, sid)
            except Exception:
                pass
        return None                      # 通知类不写回

    if method == "ping":
        return {"jsonrpc": "2.0", "id": rid, "result": {}}

    # tools/list / tools/call / 其他
    sid = _fresh_session()
    status, sid2, text = _remote_request(req, sid)
    if sid2:                             # 服务器刷新了 session id
        _session_id = sid2
        _session_ts = time.time()
    if status == 404 and "Session not found" in text:
        # TTL 已过：重新握手后重试一次
        _reinit()
        status, sid2, text = _remote_request(req, _session_id)
        if sid2:
            _session_id = sid2
            _session_ts = time.time()
    msg = _parse_sse(text, want_id=rid)
    if msg is not None:
        return msg
    if status == 202 or status == 204:   # 已受理、无内容
        return None
    return {"jsonrpc": "2.0", "id": rid,
            "error": {"code": -32000,
                      "message": "remote http %s: %s" % (status, text[:500])}}


def main():
    sys.stdin.reconfigure(encoding="utf-8", errors="replace")
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
        except Exception:
            continue
        if not isinstance(req, dict):
            continue
        try:
            out = _forward(req)
        except Exception as e:           # 代理自身异常也要回显，避免 ZCode 挂死
            out = {"jsonrpc": "2.0", "id": req.get("id"),
                   "error": {"code": -32603, "message": "proxy error: %s" % e}}
        if out is not None:
            sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
