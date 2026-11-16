# -*- coding: utf-8 -*-
"""
百亚形态验证单·跟踪台账 v1.1（持久版, 2026-10-03）
台账: data/baya_verify_track.jsonl
用法:
  fill --pairs "code:px:qty,..." [--tag shadow|real] [--anchor_date YYYYMMDD]
  update [--trade_date YYYYMMDD]
  status
判定(需全部票持有>=60交易日): 证伪=中位fwd60<=0且启动<2/7→FAIL; 通过=中位>=5%或启动>=3/7→PASS_candidate
退出信号: -8%止损(S-5) / 爆量退出(vr120>=1.5∧ret20>=20)
"""
import json, sqlite3, sys, argparse
import numpy as np

LEDGER = r"D:/zcodesj/data/baya_verify_track.jsonl"
DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(SOP一百三十二)
CAND = [("000877", "天山股份"), ("301136", "招标股份"), ("000572", "海马汽车"), ("603686", "福龙马"),
        ("601567", "三星电气"), ("000761", "本钢板材"), ("301011", "华立科技")]


def load():
    try:
        return {json.loads(l)["code"] + "|" + json.loads(l).get("tag", "real"): json.loads(l)
                for l in open(LEDGER, encoding="utf-8")}
    except FileNotFoundError:
        return {}


def save(rows):
    with open(LEDGER, "w", encoding="utf-8") as f:
        for r in rows.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def fill(a):
    rows = load()
    for pair in a.pairs.split(","):
        code, px, qty = pair.split(":")
        px, qty = float(px), int(qty)
        nm = dict(CAND).get(code, code)
        key = code + "|" + (a.tag or "real")
        rows[key] = {"code": code, "name": nm, "buy_date": a.anchor_date if a.anchor_date else None,
                     "buy_px": px, "qty": qty, "tag": a.tag or "real", "status": "OPEN",
                     "last_px": px, "fwd_pct": 0.0, "exit_signal": None, "snapshots": []}
        print(f"录入 {nm} {code} @{px} x{qty}股 = {px*qty:.0f}元 [{a.tag or 'real'}]")
    save(rows)
    print(f"台账共 {len(rows)} 票, saved {LEDGER}")


def update(a):
    rows = load()
    if not rows:
        print("台账为空, 先 fill 录入成交")
        return
    con = sqlite3.connect(DB, uri=True)
    last = con.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0]
    for key, r in rows.items():
        if r["status"] != "OPEN":
            continue
        code = r["code"]
        q = con.execute("select date,close,volume from stock_daily_meta_qfq where code=? order by date",
                        (code,)).fetchall()
        d = [x[0] for x in q]
        c = np.array([x[1] for x in q], float)
        v = np.array([x[2] for x in q], float)
        if not r.get("buy_date"):
            bd = next((x for x in d if x > (a.trade_date or "20261009")), d[-1])
            r["buy_date"] = bd
        if r["buy_date"] not in d:
            continue
        i = d.index(r["buy_date"])
        now = len(d) - 1
        r["last_px"] = c[now]
        r["fwd_pct"] = round((c[now]/r["buy_px"]-1)*100, 2)
        r["days_held"] = now - i
        if c[now]/r["buy_px"] - 1 <= -0.08:
            r["exit_signal"] = "S5_STOP(-8%)"
        elif now > 121:
            vr120 = v[now]/np.mean(v[now-120:now])
            ret20 = (c[now]/c[now-20]-1)*100
            if vr120 >= 1.5 and ret20 >= 20:
                r["exit_signal"] = f"VOL_EXIT(vr120={vr120:.2f},ret20={ret20:.1f}%)"
        r["fwd_days_available"] = r["days_held"]
        r["snapshots"] = (r.get("snapshots") or [])[-4:] + [{"date": last, "px": c[now],
                                                              "fwd%": r["fwd_pct"], "exit": r["exit_signal"]}]
    con.close()
    save(rows)
    status(a)


def status(a):
    rows = load()
    if not rows:
        print("台账为空")
        return
    open_rows = [r for r in rows.values() if r["status"] == "OPEN"]
    print(f"=== 百亚验证单状态 ({len(rows)}票, OPEN {len(open_rows)}) ===")
    fwds, started = [], 0
    for r in sorted(rows.values(), key=lambda x: -(x.get("fwd_pct") or 0)):
        ex = r.get("exit_signal")
        tag = f" ⚠️{ex}" if ex else ""
        fwd_v = r.get("fwd_pct") or 0
        held_v = r.get("days_held") or 0
        print(f"  [{r.get('tag','real')}] {r['name']:<6}{r['code']} 买@{r.get('buy_px')} 现@{r.get('last_px')} "
              f"fwd={fwd_v:+.1f}% 持{held_v}天{tag}")
        if r.get("status") == "OPEN" or r.get("fwd_pct") is not None:
            fwds.append(r["fwd_pct"])
            if r["fwd_pct"] >= 20:
                started += 1
    n = len(fwds)
    if n:
        med = float(np.median(fwds))
        min_held = min((r.get("days_held") or 0) for r in rows.values())
        print(f"\n中位fwd={med:+.2f}% | 启动(≥20%)={started}/{n} | 最短持有{min_held}天")
        if n >= 7 and min_held < 60:
            print(f"→ 验证中: 需全部票持有≥60交易日才判定(当前最短{min_held}天, 约剩{60-min_held}交易日)")
        elif n >= 7:
            if med <= 0 and started < 2:
                print("→ 判定: FAIL(证伪条件命中) — 建议源移RETIRED")
            elif med >= 5 or started >= 3:
                print("→ 判定: PASS_candidate — 呈请升格")
            else:
                print("→ 判定: 验证期满未达标(中性) — 待主人裁决")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd")
    f = sub.add_parser("fill")
    f.add_argument("--pairs", required=True)
    f.add_argument("--tag", default="real")
    f.add_argument("--anchor_date", default=None)
    u = sub.add_parser("update")
    u.add_argument("--trade_date", default=None)
    sub.add_parser("status")
    a = ap.parse_args()
    {"fill": fill, "update": update, "status": status}[a.cmd](a)
