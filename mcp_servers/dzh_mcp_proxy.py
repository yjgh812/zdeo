#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
大智慧官方 MCP 数据服务本地代理 (知微 2026-09-23)
==================================================
远程: https://mcpali.dzh.com.cn/mcp?group=<gid>   STREAMABLE HTTP, 强制 Bearer token
鉴权: OAuth2 client_credentials
  POST https://aiappgw.dzh.com.cn/oauth2/token
  Header: Authorization: Basic Base64(clientId:clientSecret)
  Body:   grant_type=client_credentials  (x-www-form-urlencoded)
  Resp:   {"access_token":"...","scope":"mcp_read_all","token_type":"Bearer","expires_in":36000}
凭据申请: 邮件 dzhts@gw.com.cn (提供公司名+用途), 或电话 021-20219990 / 021-20219997
凭据落地: 环境变量 DZH_CLIENT_ID / DZH_CLIENT_SECRET
          (或写 D:\\zcodesj\\config\\dzh_credentials.json {"client_id":..., "client_secret":...})

ToC 个人免费通道 (2026-09-23 新增, 官方 dzhmcp.html "ToC个人AI桌面智能体免费调用"):
  大智慧 MCP 已上线 WorkBuddy 连接器 (catalog: connectors/dzh-mcp/mcp.json,
  url=https://mcpali.dzh.com.cn/mcp 根端点无group, 24 个友好命名工具 getQuoteKline 等)。
  主人在 WorkBuddy 连接器中授权后, 本代理支持从 connector token 文件取凭证:
    D:\\zcodesj\\config\\dzh_connector_token.json
      {"access_token":..., "expires_at":unix秒|ISO, "refresh_token":..., "client_id":可选}
  token 策略: connector文件(优先,401时用refresh_token自续,鉴权法先none后basic)
              -> env client_credentials (ToB)
  端点模式: DZH_MODE=root (ToC根端点) / DZH_MODE=group (ToB分组,默认)
            DZH_MODE=group 时可用 DZH_GROUPS 缩小范围

本代理职责 (stdio, 模式沿用 ftshare_mcp_proxy.py):
1. tools/list: 聚合各 group 端点的工具原样透传, 建 name->group 路由表; 重名时保留
   首个, 冲突者改名 <name>__<group> 并在描述前加注。
2. tools/call: 按路由表转发到对应 group 端点。
3. token: 读缓存 D:\\zcodesj\\config\\dzh_token.json, 过期前 120s 自动重取; 远程 401 时
   强制刷新重试一次。
4. 无凭据: 仍正常起 MCP, 仅暴露 dzh_status / dzh_setup 两个工具, 其余调用返回申请指引。

ZCode config.json 用法:
  "dzh-mcp": {"type":"stdio","command":"C:\\\\Program Files\\\\Python313\\\\python.exe",
              "args":["D:\\\\zcodesj\\\\mcp_servers\\\\dzh_mcp_proxy.py"]}
环境变量: DZH_GROUPS  逗号分隔, 缺省=全部数据组
"""
import base64
import json
import os
import ssl
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import anyio
import certifi
import httpx
import mcp.types as types
from mcp.server.lowlevel import Server

REMOTE_BASE = "mcpali.dzh.com.cn"
REMOTE_PATH = "/mcp"
TOKEN_URL = "https://aiappgw.dzh.com.cn/oauth2/token"
CONFIG_DIR = Path(r"D:\zcodesj\config")
TOKEN_CACHE = CONFIG_DIR / "dzh_token.json"
CRED_FILE = CONFIG_DIR / "dzh_credentials.json"
CONNECTOR_FILE = CONFIG_DIR / "dzh_connector_token.json"
SESSION_RENEW_AFTER = 240.0   # 与 ftshare 同口径: 提前续期
HTTP_TIMEOUT = 60.0
MODE = os.environ.get("DZH_MODE", "group").lower()   # group | root

# 官方 dzhmcp.html 披露的数据服务组 (2026-09-23 抓取)
ALL_GROUPS = [
    "a_share_quote_snapshot", "a_share_capital_monitoring", "a_share_margin_trading",
    "sector_founds", "market_founds", "technical_analysis",
    "financial_overview", "financial_overview_reverse",
    "hk_financial_panorama", "us_financial_panorama",
    "composite_factors", "company_profile", "shareholder_structure",
    "shareholder_returns", "fund", "research_views", "sentiment_monitoring",
    "featured_content", "event_driven", "market_expectations",
]

_sessions = {}   # group -> {"sid": str|None, "ts": float}
_routes = {}     # tool_name -> group
_routes_loaded = False
_ssl_ctx = ssl.create_default_context(cafile=certifi.where())


def _log(msg):
    print(f"[dzh-mcp-proxy] {msg}", file=sys.stderr)


def _load_credentials():
    cid = os.environ.get("DZH_CLIENT_ID", "")
    sec = os.environ.get("DZH_CLIENT_SECRET", "")
    if cid and sec:
        return cid, sec
    try:
        d = json.loads(CRED_FILE.read_text(encoding="utf-8"))
        return d.get("client_id", ""), d.get("client_secret", "")
    except Exception:
        return "", ""


_conn_cache = {}

def _load_connector_token():
    """ToC 通道: 读 WorkBuddy 连接器授权落地的 token 文件。
    读失败(文件锁/AV抖动)时重试3次, 仍失败回退内存缓存, 不静默清空凭据。"""
    global _conn_cache
    import time as _t
    for i in range(3):
        try:
            d = json.loads(CONNECTOR_FILE.read_text(encoding="utf-8"))
            if d:
                _conn_cache = d
            return d or dict(_conn_cache)
        except FileNotFoundError:
            break
        except Exception:
            _t.sleep(0.2 * (i + 1))
    return dict(_conn_cache)


def _parse_ts(v):
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v)
    try:
        if s.endswith("Z"):
            import datetime as _dt
            return _dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
                tzinfo=_dt.timezone.utc).timestamp()
        return float(s)
    except Exception:
        return 0.0


def _refresh_connector_token(rt, client_id):
    """refresh_token 续期; token_endpoint_auth_methods 支持 none/basic, 先 none 后 basic。"""
    import base64 as _b64
    import urllib.parse as _up
    import urllib.request as _ur
    form = _up.urlencode({"grant_type": "refresh_token", "refresh_token": rt,
                          **({"client_id": client_id} if client_id else {})}).encode()
    attempts = [({}, "none")]
    if client_id:
        basic = _b64.b64encode(f"{client_id}:".encode()).decode()
        attempts.append(({"Authorization": f"Basic {basic}"}, "basic"))
    for extra, _tag in attempts:
        req = _ur.Request(TOKEN_URL, data=form, method="POST",
                          headers={"Content-Type": "application/x-www-form-urlencoded",
                                   **extra})
        try:
            with _ur.urlopen(req, timeout=30, context=_ssl_ctx) as resp:
                d = json.loads(resp.read().decode("utf-8"))
            if d.get("access_token"):
                return d
        except Exception:
            continue
    return None


def _get_token(force=False):
    """统一取 token: connector 文件优先(可自续), 降级 ToB client_credentials。"""
    now = time.time()
    c = _load_connector_token()
    at = _conn_at(c)
    if at:
        exp = _parse_ts(c.get("expires_at") or c.get("expires_in"))
        if not force and (not exp or now < exp - 120):
            return at
        rt = _conn_rt(c)
        if rt:
            d = _refresh_connector_token(rt, c.get("client_id"))
            if d and d.get("access_token"):
                import datetime as _dt
                camel = "accessToken" in c
                c["accessToken" if camel else "access_token"] = d["access_token"]
                c["refreshToken" if camel else "refresh_token"] = d.get("refresh_token", rt)
                c["expires_at"] = (_dt.datetime.now(_dt.timezone.utc)
                                   + _dt.timedelta(seconds=int(d.get("expires_in", 3600)))
                                   ).strftime("%Y-%m-%dT%H:%M:%SZ")
                c.setdefault("expires_at_ms", int(now * 1000))
                try:
                    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
                    CONNECTOR_FILE.write_text(json.dumps(c, ensure_ascii=False),
                                              encoding="utf-8")
                except Exception:
                    pass
                return d["access_token"]
        if not force and exp == 0.0:
            return at   # 无过期信息, 信任现值
    return _fetch_token(force=force)


def _url(group):
    if MODE == "root" or group == "root":
        return f"https://{REMOTE_BASE}{REMOTE_PATH}"
    return f"https://{REMOTE_BASE}{REMOTE_PATH}?group={group}"


def _fetch_token(force=False):
    """OAuth2 client_credentials 取 token, 带磁盘缓存。"""
    now = time.time()
    if not force:
        try:
            d = json.loads(TOKEN_CACHE.read_text(encoding="utf-8"))
            if d.get("access_token") and now < d.get("expires_at", 0) - 120:
                return d["access_token"]
        except Exception:
            pass
    cid, sec = _load_credentials()
    if not (cid and sec):
        raise RuntimeError("NO_CREDENTIALS")
    basic = base64.b64encode(f"{cid}:{sec}".encode()).decode()
    body = urllib.parse.urlencode({"grant_type": "client_credentials"}).encode()
    req = urllib.request.Request(
        TOKEN_URL, data=body, method="POST",
        headers={"Authorization": f"Basic {basic}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=30, context=_ssl_ctx) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"token response missing access_token: {str(data)[:200]}")
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_CACHE.write_text(json.dumps({
        "access_token": token,
        "expires_at": now + int(data.get("expires_in", 36000)),
        "scope": data.get("scope", ""), "fetched_at": now,
    }, ensure_ascii=False), encoding="utf-8")
    _log(f"token refreshed, expires_in={data.get('expires_in')}")
    return token


def _groups():
    if MODE == "root":
        return ["root"]
    env = os.environ.get("DZH_GROUPS", "")
    gs = [g.strip() for g in env.split(",") if g.strip()]
    return gs or ALL_GROUPS


def _sse_or_json(headers, body_text):
    """远程响应兼容 application/json 与 text/event-stream 两种形态。"""
    ctype = headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in body_text.splitlines():
            if line.startswith("data:"):
                payload = line[5:].strip()
                if payload and payload != "[DONE]":
                    try:
                        return json.loads(payload)
                    except Exception:
                        continue
        return None
    try:
        return json.loads(body_text)
    except Exception:
        return None


def _remote(group, payload, token=None, sid=None):
    """POST 到远程端点。返回 (status, new_sid, resp_json|None)。"""
    url = _url(group)
    headers = {"Content-Type": "application/json",
               "Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if sid:
        headers["Mcp-Session-Id"] = sid
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT, context=_ssl_ctx) as resp:
            return (resp.status, resp.headers.get("Mcp-Session-Id"),
                    _sse_or_json(resp.headers, resp.read().decode("utf-8", "ignore")))
    except urllib.error.HTTPError as e:
        return (e.code, e.headers.get("Mcp-Session-Id") if e.headers else None,
                e.read().decode("utf-8", "ignore")[:500])


def _ensure_session(group, token):
    """组会话: 过龄/缺失则重新 initialize + initialized 通知。"""
    st = _sessions.get(group)
    now = time.time()
    if st and now - st["ts"] < SESSION_RENEW_AFTER:
        return st["sid"]
    init = {"jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {},
        "clientInfo": {"name": "dzh-mcp-proxy", "version": "1.0.0"}}}
    code, sid, _ = _remote(group, init, token=token)
    if code != 200 or not sid:
        _log(f"group {group} initialize failed http={code}")
        _sessions[group] = {"sid": None, "ts": now}
        return None
    note = {"jsonrpc": "2.0", "method": "notifications/initialized"}
    _remote(group, note, token=token, sid=sid)
    _sessions[group] = {"sid": sid, "ts": now}
    return sid


def _remote_rpc(group, method, params=None, rpc_id=1, retry_auth=True):
    """带 token/会话管理的远程 JSON-RPC 调用。"""
    try:
        token = _get_token()
    except RuntimeError as e:
        return {"error": str(e)}
    sid = _ensure_session(group, token)
    payload = {"jsonrpc": "2.0", "id": rpc_id, "method": method}
    if params is not None:
        payload["params"] = params
    code, new_sid, resp = _remote(group, payload, token=token, sid=sid)
    if code == 401 and retry_auth:
        token = _get_token(force=True)
        sid = _ensure_session(group, token)
        code, new_sid, resp = _remote(group, payload, token=token, sid=sid)
    if code == 404 and retry_auth:
        # 会话过期: 强制重握手后重试一次
        _sessions.pop(group, None)
        sid = _ensure_session(group, token)
        code, new_sid, resp = _remote(group, payload, token=token, sid=sid)
    if new_sid:
        _sessions[group] = {"sid": new_sid, "ts": time.time()}
    if code != 200:
        return {"error": f"http {code}: {str(resp)[:300]}"}
    return resp or {}


def _conn_at(c):
    return c.get("access_token") or c.get("accessToken")

def _conn_rt(c):
    return c.get("refresh_token") or c.get("refreshToken")

def _has_credential():
    if _conn_at(_load_connector_token()):
        return True
    return bool(_load_credentials()[0])

def _load_routes():
    """聚合各 group 的 tools/list, 建路由表。返回 (tools, conflicts)。"""
    global _routes_loaded
    tools, conflicts = [], []
    routes = {}
    have_cred = _has_credential()
    if have_cred:
        for g in _groups():
            resp = _remote_rpc(g, "tools/list", {}, rpc_id=2, retry_auth=True)
            if "error" in resp:
                _log(f"group {g} tools/list error: {str(resp['error'])[:150]}")
                continue
            result = resp.get("result", {})
            for t in result.get("tools", []):
                name = t["name"]
                if name in routes:
                    conflicts.append(name)
                    t = dict(t)
                    t["name"] = f"{name}__{g}"
                    t["description"] = f"[{g}] " + (t.get("description") or "")
                routes[t["name"]] = g
                tools.append(t)
    _routes.clear()
    _routes.update(routes)
    _routes_loaded = True
    _log(f"routes loaded: {len(routes)} tools from {len(_groups())} groups, "
         f"conflicts renamed: {len(conflicts)}")
    return tools, conflicts


SETUP_GUIDE = (
    "大智慧官方 MCP 数据服务需申请凭据后使用:\n"
    "1. 邮件联系 dzhts@gw.com.cn (提供公司名+用途), 或电话 021-20219990 / 021-20219997\n"
    "   申请 clientId 与 clientSecret (OAuth2 client_credentials)\n"
    "2. 拿到后执行: setx DZH_CLIENT_ID <id> && setx DZH_CLIENT_SECRET <secret>\n"
    "   (或写入 D:\\zcodesj\\config\\dzh_credentials.json)\n"
    "3. 重启本 MCP 即自动取 token (缓存 D:\\zcodesj\\config\\dzh_token.json)\n"
    "ToC 个人免费通道: 在 WorkBuddy 连接器中授权「大智慧 MCP」(免费额度开放),\n"
    "   授权后把 access_token 写入 D:\\zcodesj\\config\\dzh_connector_token.json\n"
    "   (字段: access_token/refresh_token/expires_at), 并设环境变量 DZH_MODE=root。\n"
    "ToB 端点: https://mcpali.dzh.com.cn/mcp?group=<gid>, 数据组共 20 个"
    "(a_share_quote_snapshot/financial_overview/fund/research_views 等)"
)

server = Server("dzh-mcp")


@server.list_tools()
async def list_tools() -> list[types.Tool]:
    meta = [
        types.Tool(name="dzh_status", description="大智慧MCP代理状态(凭据/路由/会话)",
                   inputSchema={"type": "object", "properties": {}}),
        types.Tool(name="dzh_setup", description="大智慧MCP凭据申请与配置指引",
                   inputSchema={"type": "object", "properties": {}}),
    ]
    if not _has_credential():
        return meta
    try:
        tools, _ = _load_routes()
        return meta + [types.Tool(**{k: v for k, v in t.items()
                                     if k in ("name", "description", "inputSchema")})
                       for t in tools]
    except Exception as e:
        _log(f"list_tools error: {e}")
        return meta


@server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "dzh_setup":
        return [types.TextContent(type="text", text=SETUP_GUIDE)]
    if name == "dzh_status":
        conn = _load_connector_token()
        token = None
        try:
            if _has_credential():
                token = _get_token()
        except Exception as e:
            token = None
        status = {
            "mode": MODE,
            "credentials": ("connector" if (conn.get("access_token") or conn.get("accessToken"))
                            else "client_credentials" if _load_credentials()[0]
                            else "missing"),
            "connector_token_expires_at": conn.get("expires_at"),
            "token": "valid" if token else "unavailable",
            "groups": _groups(),
            "routes_loaded": len(_routes),
            "sessions": {g: (bool(s["sid"])) for g, s in _sessions.items()},
        }
        return [types.TextContent(type="text",
                                  text=json.dumps(status, ensure_ascii=False, indent=1))]
    if not _has_credential():
        return [types.TextContent(type="text",
                text=f"未配置凭据, 无法调用 {name}。\n{SETUP_GUIDE}")]
    group = _routes.get(name)
    if not group:
        if not _routes_loaded:
            _load_routes()
            group = _routes.get(name)
    if not group:
        return [types.TextContent(type="text",
                text=f"未知工具 {name}, 请先 tools/list 刷新路由。")]
    rpc_id = int(time.time()) % 100000
    resp = _remote_rpc(group, "tools/call",
                       {"name": name, "arguments": arguments or {}}, rpc_id=rpc_id)
    if "error" in resp:
        return [types.TextContent(type="text", text=f"远程错误: {resp['error']}")]
    result = resp.get("result", {})
    contents = result.get("content", [])
    out = []
    for c in contents:
        if c.get("type") == "text":
            out.append(c.get("text", ""))
        else:
            out.append(json.dumps(c, ensure_ascii=False)[:2000])
    text = "\n".join(out) if out else json.dumps(result, ensure_ascii=False)[:4000]
    if result.get("isError"):
        text = "TOOL_ERROR: " + text
    return [types.TextContent(type="text", text=text)]


async def run():
    from mcp.server.stdio import stdio_server
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    anyio.run(run)
