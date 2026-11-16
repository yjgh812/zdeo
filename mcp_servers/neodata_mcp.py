#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NeoData 金融数据搜索 MCP (直连版 v2, 知微 2026-09-23)
======================================================
NeoData = 腾讯 FiT 金融数据搜索服务, 覆盖股票/指数/板块/基金/宏观/外汇/大宗商品七大类。

鉴权链路 (2026-09-23 实测打通):
  WorkBuddy 登录 JWT (挖取自本机 WorkBuddy 进程, 存 D:\\zcodesj\\config\\workbuddy_jwt.json)
    -> POST https://copilot.tencent.com/agenttool/v1/tempkey  (Bearer JWT, 空body)
    -> 返回 temp_key "tk_..." (实测 ttl 86400s)
    -> POST https://copilot.tencent.com/agenttool/v1/neodata (Bearer temp_key)
本包装器自动管理: temp_key 过期前自动用 JWT 续期; 401 强制续期重试一次;
JWT 失效(登录过期)时返回明确的重新挖取指引(mine_wb_jwt.py)。

降级链: 无 token 文件时回退本地鉴权网关模式 (localhost:{AUTH_GATEWAY_PORT}/proxy/api,
WorkBuddy/AI-zone 平台侧提供; 本机桌面版当前无此网关, 保留兼容)。

ZCode config.json:
  "neodata": {"type":"stdio","command":"C:\\\\Program Files\\\\Python313\\\\python.exe",
              "args":["D:\\\\zcodesj\\\\mcp_servers\\\\neodata_mcp.py"]}
环境变量: NEODATA_SUB_CHANNEL(默认 workbuddy), AUTH_GATEWAY_PORT(默认19000)
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

import requests
from mcp.server.fastmcp import FastMCP

JWT_FILE = r"D:\zcodesj\config\workbuddy_jwt.json"
TEMPKEY_URL = "https://copilot.tencent.com/agenttool/v1/tempkey"
NEODATA_URL = "https://copilot.tencent.com/agenttool/v1/neodata"
SUB_CHANNEL = os.getenv("NEODATA_SUB_CHANNEL", "workbuddy")
GATEWAY_PORT = os.getenv("AUTH_GATEWAY_PORT", "19000")
GATEWAY_URL = f"http://localhost:{GATEWAY_PORT}/proxy/api"
GATEWAY_REMOTE = "https://jprx.m.qq.com/aizone/skillserver/v1/proxy/teamrouter_neodata/query"

BLOCK_CHAR_LIMIT = 6000
DOC_LIMIT = 5

mcp = FastMCP("neodata")

REMINER_HINT = (
    "WorkBuddy 登录 JWT 已失效(重新登录后旧 JWT 作废)。恢复步骤:\n"
    "1. 确认 WorkBuddy 桌面端已登录\n"
    "2. 重跑: python D:\\zcodesj\\tmp\\mine_wb_jwt.py\n"
    "   (自动从进程内存挖新 JWT 并验证 tempkey, 写回 config 文件)"
)

_state = {"temp_key": None, "expires_ts": 0.0}


def _read_jwt_file():
    try:
        with open(JWT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _parse_expires(s):
    """expires_at 形如 2026-09-24T01:42:19Z"""
    try:
        return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc).timestamp()
    except Exception:
        return time.time() + 3600


def _fetch_temp_key(jwt, force=False):
    """用登录 JWT 换 temp_key; 带内存缓存, 过期前 300s 自动续。"""
    now = time.time()
    if not force and _state["temp_key"] and now < _state["expires_ts"] - 300:
        return _state["temp_key"]
    cached = _read_jwt_file().get("temp_key")
    exp = _read_jwt_file().get("expires_at")
    if not force and cached and exp and now < _parse_expires(exp) - 300:
        _state.update(temp_key=cached, expires_ts=_parse_expires(exp))
        return cached
    r = requests.post(TEMPKEY_URL, json={}, timeout=15,
                      headers={"Authorization": f"Bearer {jwt}",
                               "Content-Type": "application/json"})
    body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    tk = body.get("temp_key") or (body.get("data") or {}).get("temp_key") \
        or (body.get("data") or {}).get("key")
    if r.status_code != 200 or not tk:
        return None
    exp_s = body.get("expires_at") or (body.get("data") or {}).get("expires_at")
    _state.update(temp_key=tk, expires_ts=_parse_expires(exp_s) if exp_s else now + 3600)
    # 回写缓存文件(保留 jwt)
    try:
        d = _read_jwt_file()
        d.update(temp_key=tk, expires_at=exp_s, refreshed_at=now)
        with open(JWT_FILE, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
    except Exception:
        pass
    return tk


def _call_direct(query, data_type):
    d = _read_jwt_file()
    jwt = d.get("jwt")
    if not jwt:
        return None, "JWT 文件缺失或无 jwt 字段。\n" + REMINER_HINT
    tk = _fetch_temp_key(jwt)
    if not tk:
        return None, REMINER_HINT
    payload = {"query": query, "channel": "neodata",
               "sub_channel": SUB_CHANNEL}
    if data_type != "all":
        payload["data_type"] = data_type
    r = requests.post(NEODATA_URL, json=payload, timeout=45,
                      headers={"Authorization": f"Bearer {tk}",
                               "Content-Type": "application/json"})
    if r.status_code == 401:
        tk = _fetch_temp_key(jwt, force=True)
        if not tk:
            return None, REMINER_HINT
        r = requests.post(NEODATA_URL, json=payload, timeout=45,
                          headers={"Authorization": f"Bearer {tk}",
                                   "Content-Type": "application/json"})
    r.raise_for_status()
    return r.json(), None


def _call_gateway(query, data_type, sub_channel):
    import uuid
    payload = {"channel": "neodata", "sub_channel": sub_channel,
               "query": query, "request_id": uuid.uuid4().hex,
               "data_type": data_type, "se_params": {}, "extra_params": {}}
    resp = requests.post(GATEWAY_URL, headers={
        "Content-Type": "application/json", "Remote-URL": GATEWAY_REMOTE,
    }, json=payload, timeout=45)
    resp.raise_for_status()
    return resp.json()


def _trim_block(block):
    content = block.get("content")
    if isinstance(content, str) and len(content) > BLOCK_CHAR_LIMIT:
        b = dict(block)
        b["content"] = content[:BLOCK_CHAR_LIMIT]
        b["_truncated"] = f"content 截断 {len(content)}->{BLOCK_CHAR_LIMIT} 字符"
        return b
    return block


def _format(result):
    if not isinstance(result, dict):
        return str(result)[:2000]
    code = result.get("code")
    if str(code) not in ("200", "0") and result.get("suc") is not True:
        return json.dumps({"code": code, "msg": result.get("msg"),
                           "raw_head": str(result)[:400]}, ensure_ascii=False)
    data = result.get("data", {}) or {}
    out = {"code": code}
    api = data.get("apiData") or {}
    if api:
        out["entity"] = api.get("entity")
        recalls = api.get("apiRecall") or []
        out["apiRecall_count"] = len(recalls)
        out["apiRecall"] = [_trim_block(b) for b in recalls[:20]]
    doc = data.get("docData") or {}
    docs = []
    for grp in (doc.get("docRecall") or [])[:3]:
        for d in (grp.get("docList") or [])[:DOC_LIMIT]:
            docs.append({k: d.get(k) for k in
                         ("title", "url", "publish_time", "source", "summary")
                         if d.get(k)})
    if docs:
        out["docs"] = docs[:15]
    if not api and not docs:
        out["note"] = "返回成功但 apiData/docData 均为空(可能未命中)"
    return json.dumps(out, ensure_ascii=False)


@mcp.tool()
def neodata_query(query: str, data_type: str = "all", sub_channel: str = "") -> str:
    """NeoData 自然语言金融数据查询(实时行情/财报/资金流/研报/宏观/外汇/商品)。

    Args:
        query: 自然语言查询, 如 "贵州茅台最新财报" "光芯片板块资金流向" "黄金价格"
        data_type: all=API+文章(默认) / api=仅结构化数据 / doc=仅文章
        sub_channel: 仅网关降级模式生效, 默认 workbuddy
    """
    try:
        result, err = _call_direct(query, data_type)
        if err is None:
            return _format(result)
        # 直连失败 -> 网关降级试一次
        try:
            return _format(_call_gateway(query, data_type, sub_channel or "qclaw"))
        except Exception:
            return err
    except requests.exceptions.Timeout:
        return "NeoData 请求超时(>45s), 稍后重试"
    except requests.exceptions.RequestException as e:
        return f"NeoData 请求失败: {type(e).__name__}: {e}"
    except Exception as e:
        return f"NeoData 查询异常: {type(e).__name__}: {e}"


@mcp.tool()
def neodata_status() -> str:
    """检查 NeoData 链路状态(token/JWT/端点)。"""
    d = _read_jwt_file()
    info = {"mode": "direct", "endpoint": NEODATA_URL,
            "jwt_file": JWT_FILE,
            "jwt_present": bool(d.get("jwt")),
            "temp_key_prefix": (d.get("temp_key") or "")[:6] or None,
            "temp_key_expires_at": d.get("expires_at")}
    if d.get("jwt"):
        tk = None
        try:
            tk = _fetch_temp_key(d["jwt"])
        except Exception as e:
            info["tempkey_error"] = f"{type(e).__name__}: {e}"
        if tk:
            probe = requests.post(NEODATA_URL, timeout=30,
                                  headers={"Authorization": f"Bearer {tk}",
                                           "Content-Type": "application/json"},
                                  json={"query": "上证指数", "channel": "neodata",
                                        "sub_channel": SUB_CHANNEL,
                                        "data_type": "api"})
            info["probe_http"] = probe.status_code
            info["usable"] = probe.status_code == 200
        else:
            info["usable"] = False
            info["hint"] = REMINER_HINT
    else:
        info["mode"] = "gateway_fallback"
        info["gateway"] = GATEWAY_URL
        info["hint"] = REMINER_HINT
    return json.dumps(info, ensure_ascii=False)


if __name__ == "__main__":
    mcp.run()
