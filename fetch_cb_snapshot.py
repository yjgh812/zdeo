#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""fetch_cb_snapshot.py — 拉取xiaoshi可转债全量快照到本地(cb_shadow_track数据层)

⛔ TERMINATED 2026-09-25: 主人令「我们不做可转债」——已终止, 禁止运行。裁决=data/CB_DOUBLELOW_TERMINATED_20260925.json

直调模式: spawn官方venv的xiaoshi-mcp.exe(stdio), JSON-RPC握手后分页tools/call
xiaoshi_quant_data(dataset=convertible_bonds), items逐行写 data/cb_snapshot/YYYYMMDD.jsonl。
文件名日期=快照as_of日(取返回内state.data_as_of, 拒绝用本地当天伪标PIT)。

用法:
  python fetch_cb_snapshot.py            # 拉最新快照
  python fetch_cb_snapshot.py 20260918   # 拉并校验指定as_of日
"""
import json, os, subprocess, sys, time

ROOT = r"D:\zcodesj"
CONFIG = os.path.join(os.path.expanduser("~"), ".zcode", "cli", "config.json")
SNAP_DIR = os.path.join(ROOT, "data", "cb_snapshot")
PAGE = 50


def spawn_mcp():
    """spawn旧包装xiaoshi_mcp.py(带xiaoshi_quant_data工具面), 从config注入API key(不打印)."""
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    ent = cfg.get("mcp", {}).get("servers", {}).get("xiaoshi-mcp")
    if not ent:
        raise RuntimeError(f"config无xiaoshi-mcp条目: {CONFIG}")
    env = dict(os.environ)
    env.update(ent.get("env") or {})
    return subprocess.Popen([ent["command"]] + list(ent.get("args") or []),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.DEVNULL, text=True, encoding="utf-8",
                            bufsize=1, env=env)


def rpc(proc, obj, want_id=None, timeout=120):
    line = json.dumps(obj, ensure_ascii=False)
    proc.stdin.write(line + "\n")
    proc.stdin.flush()
    t0 = time.time()
    while time.time() - t0 < timeout:
        raw = proc.stdout.readline()
        if not raw:
            time.sleep(0.2)
            continue
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if "id" in msg and (want_id is None or msg["id"] == want_id):
            return msg
    raise RuntimeError(f"RPC超时 id={want_id}")


def main():
    want_date = sys.argv[1] if len(sys.argv) > 1 else None
    proc = spawn_mcp()
    try:
        rid = 0
        rid += 1
        rpc(proc, {"jsonrpc": "2.0", "id": rid, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "cb-snap-fetch", "version": "1"}}}, want_id=rid)
        proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}) + "\n")
        proc.stdin.flush()

        rows, as_of, total = [], None, None
        rid += 1
        resp = rpc(proc, {"jsonrpc": "2.0", "id": rid, "method": "tools/call", "params": {
            "name": "xiaoshi_quant_data", "arguments": {
                "dataset": "convertible_bonds", "limit": 500}}}, want_id=rid)
        result = resp.get("result") or {}
        data = None
        texts = [c.get("text") for c in result.get("content") or [] if c.get("type") == "text"]
        for t in texts:
            try:
                d1 = json.loads(t)
            except (json.JSONDecodeError, TypeError):
                continue
            inner = d1.get("result") if isinstance(d1, dict) else None
            if isinstance(inner, str):
                try:
                    inner = json.loads(inner)
                except json.JSONDecodeError:
                    inner = None
            if isinstance(inner, dict) and "items" in inner:
                data = inner
                break
            if isinstance(d1, dict) and "items" in d1:
                data = d1
                break
        if data is None:
            raise RuntimeError(f"无法解析响应: resultKeys={list(result.keys())} textHead={(texts[0][:120] if texts else '无')}")
        items = data.get("items") or []
        state = data.get("state") or {}
        as_of = state.get("data_as_of")
        total = data.get("total_count")
        rows.extend(items)
        # 单页全量(该工具无offset分页, limit=500覆盖全市场~315只)
        if not rows:
            print("[ERROR] 空快照")
            return 2
        # PIT防御: 数据集按期累积(total_count>单期), 只保留最新期(event_date==max), 防旧期混入
        def _ed(r):
            return str(r.get("event_date") or "")
        max_ed = max(_ed(r) for r in rows)
        latest = [r for r in rows if _ed(r) == max_ed]
        dropped = len(rows) - len(latest)
        rows = latest
        if want_date and str(as_of).replace("-", "") != want_date:
            print(f"[ERROR] as_of={as_of} 与请求日{want_date}不符")
            return 2
        os.makedirs(SNAP_DIR, exist_ok=True)
        out = os.path.join(SNAP_DIR, f"{max_ed.replace('-', '')}.jsonl")
        codes = set()
        with open(out, "w", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
                codes.add(str(r.get("code")))
        print(f"[OK] as_of={max_ed} 最新期items={len(rows)}(弃旧期{dropped}行) 去重code={len(codes)} total_count={total} -> {out}")
        return 0
    finally:
        try:
            proc.kill()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
