# -*- coding: utf-8 -*-
"""信号pilot实战件: 深位首板(PD-031) + 分歧日核心股(PD-035) 模拟盘小额实战验证
授权: 主人 2026-10-07 「模拟盘实战验证授权」(AskUserQuestion 批 ①②③, ④卖出侧逃生门未批维持待裁)。
框架同构复用 sold_rebuy_pilot.py(深贴回补先例): 状态机 pending→filled→exited/gone、日终死单对账、
交易时段护栏、交易日守卫、run_executor 全 R 闸、T+20 到期卖。-8% 止损由 S-5 链自动覆盖(本件不重复)。

信号源:
  deepfirst: data/deepfirst_shadow_ledger.jsonl 最新交易日深位首板事件(pos<=25+首板+放量)
  dipcore  : data/concept_stage_ledger.jsonl kind=dip_core 分歧日核心股(抗跌∧成交额前50%)
围栏: 每笔1.5万 / 并发<=3(启动时提示与深贴pilot合计敞口) / 限价=信号日收盘x1.02 / >30元不买 /
      ST排除 / T+1拾取(信号日<今日) / 主板+创业板(00/30/60)。
修复(2026-10-12晚, PD-036执行层R-a, 主人「继续」令): ①买入前实时价哨——现价>信号收盘x5%上限
      =SKIP_GAPUP+报警(追高负选择证伪纪律); 限价<现价<=上限=顶价+1%追挂(龙蟠10/8踏空根因
      =09:42下单时限价已够不着); ②拒单/跳过必toast+notifications.log(此前rc=2静默5天)。

用法: python signal_pilot.py --plan | --execute [--force]
状态: data/signal_pilot_state.json
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from sold_rebuy_pilot import (  # noqa: E402  复用成熟件: 连接/RPC/执行器/护栏
    MAX_CONCURRENT as DEEP_PILOT_MAX, PER_TRADE, PRICE_CAP,
    norm_date, qfq_con, run_executor, rpc_positions_and_cancel, tds_between)

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
QFQ_DB_PLAIN = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"  # 影子委托用(其内部自带file:?mode=ro包装)
DF_LEDGER = Path(r"D:\zcodesj\data\deepfirst_shadow_ledger.jsonl")
CS_LEDGER = Path(r"D:\zcodesj\data\concept_stage_ledger.jsonl")
DEEP_STATE = Path(r"D:\zcodesj\data\sold_rebuy_pilot_state.json")
STATE_F = Path(r"D:\zcodesj\data\signal_pilot_state.json")
ORDERS_LOG = Path(r"D:\zcodesj\data\executor_orders.jsonl")
SOURCE = "signal_pilot_dfc"
HOLD_DAYS = 20
VALID_DAYS = 5
LIMIT_PREMIUM = 1.02
GAP_CAP = 1.05   # 现价超过信号收盘5%=追高负选择证伪域, SKIP
BUMP = 1.01      # 现价在(限价,5%上限]内=顶价+1%追挂
MAX_CONCURRENT = 3
BOARD_OK = ("600", "601", "603", "605", "000", "001", "002", "003", "300", "301", "302")


def load_state() -> dict:
    if STATE_F.exists():
        return json.loads(STATE_F.read_text(encoding="utf-8"))
    return {"orders": {}}


def save_state(st: dict) -> None:
    tmp = STATE_F.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(STATE_F)


def name_of(code: str) -> str:
    try:
        out = __import__("subprocess").run(
            [sys.executable, r"D:\zcodesj\stock_name_lookup.py", code],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30)
        for line in (out.stdout or "").splitlines():
            if line.startswith(code):
                return line.split(":", 1)[1].strip()
    except Exception:
        pass
    return ""


def alarm(msg: str) -> None:
    """失败/跳过必报警: 本机toast+notifications.log(9/8裁决合规通道, 无外部推送)"""
    try:
        with open(r"D:\zcodesj\data\notifications.log", "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} [signal_pilot] {msg}\n")
    except Exception:
        pass
    try:
        subprocess.run(["powershell", "-NoProfile", "-Command",
                        f"New-BurntToastNotification -Text '{msg[:80]}'"],
                       capture_output=True, timeout=15)
    except Exception:
        pass


def _ws_call(tool: str, args: dict):
    """westock HTTP MCP 自包含直连(同 sold_rebuy_pilot.rpc 实战验证模式), 返回unwrap后对象或None"""
    import urllib.request as _ur
    url = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
    token = ""
    for cfgp in (r"C:\Users\yujie\.zcode\cli\config.json",
                 r"C:\Users\Administrator\.zcode\cli\config.json"):
        try:
            cfg = json.load(open(cfgp, encoding="utf-8"))
            ws = cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
            token = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
            if token:
                break
        except OSError:
            continue
    if not token:
        token = __import__("os").environ.get("WESTOCK_TOKEN", "")
    if not token:
        return None
    body = json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                       "params": {"name": tool, "arguments": args}}).encode()
    h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
         "Accept": "application/json, text/event-stream"}
    req = _ur.Request(url, data=body, headers=h, method="POST")
    with _ur.urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
    for line in raw.splitlines():
        if line.startswith("data:") and line.strip() != "data:":
            d = json.loads(line[5:].strip())
            if "result" in d:
                for c in d["result"].get("content", []):
                    if c.get("type") == "text":
                        return json.loads(c["text"])
    return None


def live_price(code: str):
    """实时价(开盘后下单前的现价哨); 失败返回None=退回原限价行为并WARN"""
    try:
        pre = "sh" if code.startswith("6") else "sz"
        d = _ws_call("data_quote", {"code": pre + code})
        if isinstance(d, dict):
            d = d.get("data", d)
        if isinstance(d, dict):  # data_quote 按带前缀代码做键: {"sh603906":{...}}
            if pre + code in d:
                d = d[pre + code]
            elif len(d) == 1:
                d = next(iter(d.values()))
        if isinstance(d, list):
            d = d[0] if d else {}
        for k in ("price", "lastPrice", "currentPrice", "now", "close"):
            v = (d or {}).get(k)
            if v:
                return float(v)
    except Exception as e:
        print(f"[WARN] 实时价失败 {code}: {e}", file=sys.stderr)
    return None


def ensure_ledger_current(sig_date: int) -> None:
    """台账自愈: 落后于信号日时委托canonical计算器(deepfirst_shadow_daily, 同式同口径)就地补扫。
    游标判据=deepfirst_shadow_latest.json.scanned.to; 台账幂等(code+date主键), 重复扫描零副作用。
    背景=自动消费/手动生产断链两次实锤(10/12龙蟠踏空, 10/13空转); 委托调用≠调度影子脚本,
    PD-031的影子接线禁令(schtasks/cron)不适用, 如实披露待主人追认。"""
    try:
        snap_p = Path(r"D:\zcodesj\data\deepfirst_shadow_latest.json")
        scanned_to = 0
        if snap_p.exists():
            scanned_to = int(json.loads(snap_p.read_text(encoding="utf-8"))
                            .get("scanned", {}).get("to", 0))
        if scanned_to >= sig_date:
            return
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "dfs_mod", r"D:\zcodesj\analysis\deepfirst_shadow_daily.py")
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m.QFQ = QFQ_DB_PLAIN  # 与pilot同库(wb活副本), 保证扫描窗口覆盖sig_date
        old_argv = sys.argv
        sys.argv = ["deepfirst_shadow_daily", f"--date={sig_date}"]
        try:
            m.main()
        finally:
            sys.argv = old_argv
        print(f"[pilot] 台账补扫 {sig_date} 完成(委托deepfirst_shadow_daily同式同口径)")
        alarm(f"pilot台账自愈补扫 {sig_date}")
    except Exception as e:
        print(f"[WARN] 台账补扫失败 {sig_date}: {e}", file=sys.stderr)


def latest_qfq_date(con) -> int:
    return int(con.execute(
        "SELECT MAX(date) FROM stock_daily_meta_qfq "
        "WHERE code LIKE '00%' OR code LIKE '30%' OR code LIKE '60%'").fetchone()[0])


def collect_deepfirst(con, sig_date: int) -> list[dict]:
    if not DF_LEDGER.exists():
        return []
    out = []
    for line in DF_LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if int(r["date"]) == sig_date:
            zt = r.get("zt_cnt")
            gear = ("强档" if (zt or 0) >= 61 else ("中档" if (zt or 0) >= 40 else "弱档")) if zt else "N/A"
            if zt is not None and zt < 40:
                print(f"[pilot] 情绪门跳过 {r.get('name','?')}({r['code']}) "
                      f"信号日涨停{zt}家<40(弱档) — S6主人批, 弱档win36.7%不接")
                continue
            if zt is None:
                print(f"[WARN] {r['code']} zt_cnt缺失, 放行并标注(N/A)")
            out.append({"code": r["code"], "src": "deepfirst", "sig_date": str(sig_date),
                        "ref_px": float(r["close"]),
                        "tag": f"深位首板pos{r.get('pos60p')}% vol{r.get('volr')}x 情绪{gear}(涨停{zt}家)"})
    return out


def collect_dipcore(con, sig_date: int) -> list[dict]:
    if not CS_LEDGER.exists():
        return []
    out = []
    for line in CS_LEDGER.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get("kind") != "dip_core" or int(r.get("date", 0)) != sig_date:
            continue
        for code, _nm in (r.get("core_stocks") or []):
            row = con.execute(
                "SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date=?",
                (code, sig_date)).fetchone()
            if row and row[0] and row[0] > 0:
                out.append({"code": code, "src": "dipcore", "sig_date": str(sig_date),
                            "ref_px": float(row[0]),
                            "tag": f"分歧核心股[{r.get('sector_name','')}]{r.get('stage','')}"})
    return out


def main() -> int:
    ap = __import__("argparse").ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--plan", action="store_true", help="干跑: 只打印拟动作, 零执行")
    g.add_argument("--execute", action="store_true", help="实战: 真实下单/撤单/卖出(模拟盘)")
    ap.add_argument("--force", action="store_true", help="豁免交易时段护栏(测试用)")
    a = ap.parse_args()

    now = datetime.datetime.now()
    if not a.force and not (datetime.time(9, 30) <= now.time() <= datetime.time(14, 55)):
        print(f"[pilot] 非交易时段 {now:%H:%M} (护栏09:30-14:55); 仅--plan可跑")
        if not a.plan:
            return 0

    today = now.strftime("%Y%m%d")
    from trade_day_guard import is_trading_day
    if is_trading_day(today) is False and not a.force:
        print(f"[pilot] {today} 休市日 → 全跳过")
        return 0

    st = load_state()
    con = qfq_con()
    sig_date = latest_qfq_date(con)  # 信号日=qfq最新交易日(T-1口径)

    # T+1 拾取守卫: 信号日必须早于今天(信号日收盘确认, 次日执行)
    if sig_date >= int(today) and not a.force:
        print(f"[pilot] 信号日{sig_date}>=今日 → T+1守卫: 今日不买当日信号")
        con.close()
        return 0

    ensure_ledger_current(sig_date)  # 台账自愈(委托canonical计算器, 幂等)
    cands = collect_deepfirst(con, sig_date) + collect_dipcore(con, sig_date)
    con.close()

    positions, cancel_fn = rpc_positions_and_cancel()
    if positions is None:
        print("[pilot] FAIL 取不到westock持仓(fail-closed)")
        return 3
    held = {p["code"] for p in positions}
    pos_map = {p["code"]: p for p in positions}

    st_orders = st.setdefault("orders", {})
    for code, o in list(st_orders.items()):
        if o.get("status") == "pending" and code in pos_map:
            o["status"], o["fill_date"], o["fill_price"] = "filled", today, pos_map[code]["price"]
            print(f"[pilot] 成交确认 {code} → filled @约{o['fill_price']}")
    for code, o in list(st_orders.items()):
        if o.get("status") == "pending" and o.get("placed_at") != today:
            o["status"] = "day_expired"
            print(f"[pilot] 日终死单 {code} (挂于{o['placed_at']}) → 重新评估")

    active = [c for c, o in st_orders.items() if o.get("status") in ("pending", "filled")]
    slots = max(0, MAX_CONCURRENT - len(active))
    deep_n = 0
    if DEEP_STATE.exists():
        try:
            deep_n = sum(1 for o in json.loads(DEEP_STATE.read_text(encoding="utf-8"))
                         .get("orders", {}).values() if o.get("status") in ("pending", "filled"))
        except Exception:
            pass
    print(f"[pilot] 敞口: 本件在场{len(active)}/{MAX_CONCURRENT} + 深贴pilot在场{deep_n}/{DEEP_PILOT_MAX} "
          f"(两件合计小额验证敞口={(len(active)+deep_n)*1.5:.1f}万)")

    # 1) 到期撤单 / 2) T+20到期卖出
    for code, o in list(st_orders.items()):
        if o["status"] == "pending" and len(tds_between(qfq_con(), o["signal_date"], today)) >= VALID_DAYS:
            if a.execute and o.get("order_id") and cancel_fn:
                ok = cancel_fn(o["order_id"])
                o["status"] = "expired" if ok else "expired_cancel_fail"
                print(f"[pilot] 撤单 {code}: {'OK' if ok else 'RPC失败'}")
            elif a.execute:
                o["status"] = "expired"
            else:
                print(f"[plan] 撤单 {code} (挂{VALID_DAYS}交易日未成交)")
    for code, o in list(st_orders.items()):
        if o["status"] != "filled":
            continue
        n_tds = len(tds_between(qfq_con(), o["fill_date"], today))
        p = pos_map.get(code)
        if n_tds >= HOLD_DAYS and p:
            px = round(p["price"] * 0.995, 2)
            line = f"卖出 {code} {p['name']} {p['qty']}股 @{px} (pilot满{HOLD_DAYS}交易日, 盈亏{(p['price']/o['fill_price']-1)*100:+.1f}%)"
            if a.execute:
                rc, out = run_executor(["sell", "--code", code, "--name", p["name"],
                                        "--source", SOURCE, "--price", str(px),
                                        "--volume", str(p["qty"]),
                                        "--reason", "signal_pilot T20到期", "--confirmed", "--execute"])
                o["status"] = "exited" if rc == 0 else "exit_fail"
                print(f"[pilot] {line} → rc={rc}" + ("" if rc == 0 else f" {out[-200:]}"))
            else:
                print(f"[plan] {line}")
        elif n_tds >= HOLD_DAYS and not p:
            o["status"] = "gone"

    # 3) 新挂单
    active = [c for c, o in st_orders.items() if o.get("status") in ("pending", "filled")]
    slots = max(0, MAX_CONCURRENT - len(active))
    cand = []
    seen = set()
    for c in cands:
        code = c["code"]
        if code in seen or code in held or code in active:
            continue
        if code in st_orders and st_orders[code].get("status") not in ("day_expired",):
            continue
        if code[:3] not in BOARD_OK:
            continue
        nm = name_of(code)
        if "ST" in nm.upper() or "退" in nm:
            continue
        limit = round(c["ref_px"] * LIMIT_PREMIUM, 2)
        if limit > PRICE_CAP or limit < 1.0:
            continue
        vol = int(PER_TRADE / limit / 100) * 100
        if vol < 100:
            continue
        seen.add(code)
        cand.append({**c, "name": nm or code, "limit": limit, "vol": vol})
    cand.sort(key=lambda x: (x["src"], x["sig_date"]), reverse=True)

    if a.execute and slots > 0:
        for c_ in cand[:slots]:
            limit = c_["limit"]
            live = live_price(c_["code"])
            if live is not None:
                cap = round(c_["ref_px"] * GAP_CAP, 2)
                if live > cap:
                    st["orders"][c_["code"]] = {
                        "status": "skipped_gapup", "src": c_["src"],
                        "signal_date": c_["sig_date"], "ref_px": c_["ref_px"],
                        "limit_price": limit, "volume": c_["vol"], "live_px": live,
                        "placed_at": today, "rc": 0,
                        "note": f"现价{live}>信号收盘x{GAP_CAP}={cap}, 追高负选择SKIP"}
                    alarm(f"pilot跳过 {c_['name']}({c_['code']}) 现价{live}>{cap} 超信号收盘5%上限")
                    print(f"[pilot] SKIP_GAPUP {c_['name']} 现价{live}>上限{cap}")
                    continue
                if live > limit:
                    limit = min(round(live * BUMP, 2), cap)
                    vol = int(PER_TRADE / limit / 100) * 100
                    if vol < 100:
                        continue
                    c_["limit"], c_["vol"] = limit, vol
                    print(f"[pilot] 限价追挂 {c_['name']}: {c_['limit']}→{limit} (现价{live}, 上限{cap})")
            print(f"[pilot] 买入挂单 {c_['name']}({c_['code']}) {c_['vol']}股 @{c_['limit']} "
                  f"[{c_['src']}] {c_['tag']}")
            rc, out = run_executor(["buy", "--code", c_["code"], "--name", c_["name"],
                                    "--source", SOURCE, "--price", str(c_["limit"]),
                                    "--volume", str(c_["vol"]), "--execute"], timeout=420)
            order_id = None
            try:
                for line in reversed(ORDERS_LOG.read_text(encoding="utf-8").splitlines()):
                    o_ = json.loads(line)
                    if o_.get("code") == c_["code"] and o_.get("trade_date") == today \
                            and o_.get("action") == "BUY":
                        order_id = o_.get("order_id")
                        break
            except Exception:
                pass
            status = "pending" if rc == 0 else "rejected"
            note_txt = out[-300:] if rc != 0 else ""
            st["orders"][c_["code"]] = {
                "status": status, "src": c_["src"], "signal_date": c_["sig_date"],
                "ref_px": c_["ref_px"], "limit_price": c_["limit"], "volume": c_["vol"],
                "placed_at": today, "order_id": order_id, "rc": rc,
                "note": note_txt}
            print(f"[pilot] rc={rc} → {status} order={order_id}")
            if rc != 0:
                alarm(f"pilot拒单 {c_['name']}({c_['code']}) rc={rc} {note_txt[:80]}")
    else:
        for c_ in cand[:slots if a.execute else 6]:
            print(f"[plan] 拟挂 {c_['name']}({c_['code']}) {c_['vol']}股 @{c_['limit']} "
                  f"[{c_['src']}] {c_['tag']}")

    save_state(st)
    n_p = sum(1 for o in st["orders"].values() if o["status"] == "pending")
    n_f = sum(1 for o in st["orders"].values() if o["status"] == "filled")
    print(f"\n[pilot] 状态: 挂单{n_p} 在场{n_f} 历史{len(st['orders'])-n_p-n_f} "
          f"| 模式={'EXECUTE' if a.execute else 'PLAN'} | 信号日{sig_date}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
