# -*- coding: utf-8 -*-
"""dip_shadow_track_daily.py — 深潜影子跟踪器(生产版, 配套PREREG_DIP_ENV_ABS_ACTIVE_20261029)。
职责: ①读当日 scan 抽样 -> 登记 signal(介入=T+1收盘待回填) ②回填已过T+1信号的介入价
     ③回填已成熟(介入+20交易日)信号的退出并重算滚动窗统计 ④kill条件判定(胜率<45%或中位<-1.0%)。
存储: data/dip_shadow_track.jsonl (逐事件UPSERT, key=signal_date|code) + data/dip_shadow_track_state.json。
kill触发: 落盘 data/dip_shadow_track_KILL_ALERT.json 并打印警报(不自动停止扫描, 停止需主人裁决)。"""
import os, sys, json, sqlite3, time
import numpy as np
import statistics as st
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
QFQ = r"D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
SCAN = os.path.join(ROOT, "data", "dip_shadow_scan_latest.json")
TRACK = os.path.join(ROOT, "data", "dip_shadow_track.jsonl")
STATE = os.path.join(ROOT, "data", "dip_shadow_track_state.json")
KILL = os.path.join(ROOT, "data", "dip_shadow_track_KILL_ALERT.json")
WIN = 20
COST = 0.0015
t0 = time.time()

con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
dates_all = sorted({int(r[0]) for r in con.execute("select distinct date from stock_daily_meta_qfq where date>=20240101 order by date")})
didx = {d: i for i, d in enumerate(dates_all)}
N = len(dates_all)
close_map = defaultdict(dict)
for code, d, c in con.execute("select code,date,close from stock_daily_meta_qfq where date>=20240101 order by code,date"):
    close_map[code][int(d)] = float(c)
con.close()

# ① 登记当日抽样
events = {}
if os.path.exists(TRACK):
    for line in open(TRACK, encoding="utf-8"):
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        events[f"{r['signal_date']}|{r['code']}"] = r
if os.path.exists(SCAN):
    scan = json.load(open(SCAN, encoding="utf-8"))
    sd = scan["scan_date"]
    idx_sd = didx.get(sd)
    for s in scan.get("shadow_sample_top10_random_seed_scan_date", []):
        key = f"{sd}|{s['code']}"
        if key not in events:
            entry_date = dates_all[idx_sd + 1] if idx_sd is not None and idx_sd + 1 < N else None
            events[key] = {"signal_date": sd, "code": s["code"], "name": s.get("name", ""),
                           "entry_date": entry_date, "entry_price": None,
                           "exit_date": None, "exit_price": None,
                           "ret_net": None, "status": "PENDING_ENTRY"}

# ②③ 回填
trade_days = dates_all
def next_k_trade_days(d, k):
    i = didx.get(d)
    if i is None or i + k >= N:
        return None
    return trade_days[i + k]

new_lines = 0
for key, ev in events.items():
    ed = ev.get("entry_date")
    code = ev["code"]
    if ev.get("entry_price") is None and ed is not None:
        px = close_map.get(code, {}).get(ed)
        if px:
            ev["entry_price"] = px
    if ev.get("entry_price") is not None and ev.get("exit_price") is None:
        xd = next_k_trade_days(ed, WIN)
        if xd is not None and xd in close_map.get(code, {}):
            ev["exit_date"] = xd
            ev["exit_price"] = close_map[code][xd]
            ev["ret_net"] = round(ev["exit_price"] / ev["entry_price"] - 1 - COST, 6)
            ev["status"] = "SETTLED"

with open(TRACK, "w", encoding="utf-8") as f:
    for ev in events.values():
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")
        new_lines += 1

# 滚动窗统计(最近20个已结算事件, 按 entry_date 排序)
settled = sorted([e for e in events.values() if e.get("ret_net") is not None],
                 key=lambda e: e["entry_date"])
recent = settled[-WIN:]
state = {"asof": time.strftime("%Y-%m-%d %H:%M"),
         "n_events_total": len(events),
         "n_pending_entry": sum(1 for e in events.values() if e["status"] == "PENDING_ENTRY"),
         "n_settled": len(settled),
         "rolling_window": {"n": len(recent),
                            "win_rate": round(100 * sum(1 for e in recent if e["ret_net"] > 0) / len(recent), 1) if recent else None,
                            "median_ret": round(100 * st.median([e["ret_net"] for e in recent]), 2) if recent else None}}
kill_flag = False
if state["rolling_window"]["n"] >= 15:
    wr = state["rolling_window"]["win_rate"]
    md = state["rolling_window"]["median_ret"]
    if (wr is not None and wr < 45) or (md is not None and md < -1.0):
        kill_flag = True
        json.dump({"triggered_at": time.strftime("%Y-%m-%d %H:%M"),
                   "window": state["rolling_window"],
                   "action_required": "暂停深潜影子并复盘(prereg kill条件触发)"},
                  open(KILL, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
json.dump(state, open(STATE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print(f"跟踪器: 登记/总数={new_lines}/{state['n_events_total']} 待介入={state['n_pending_entry']} 已结算={state['n_settled']}")
print(f"滚动窗({state['rolling_window']['n']}): win={state['rolling_window']['win_rate']}% med={state['rolling_window']['median_ret']}% kill={'🔴触发' if kill_flag else '未触发'} ({time.time()-t0:.0f}s)")
