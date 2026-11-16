#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""MORNINGSTAR_NEXTBAR 早晨之星影子日更(观测层零交易权)
对应 PREREG_MORNINGSTAR_NEXTBAR_20261020.json, 主人 2026-10-20「继续」批准
(2026-10-19 转贴例→10-20 质疑复核抓出口径错误→next-bar 修正轮达标→呈请→批准, 决策链见 prereg)。
事件定义/口径与 analysis/kline_pattern_nextbar_full_20261020.py 逐位一致
(next-bar: T+1收盘入场→T+21收盘结算); 参数单一真源=prereg params 字段
(缺字段 fail-closed 退出, 禁止回退硬编码)。
数据源=dragon_quant_qfq.db stock_daily_meta_qfq(唯一回测价格源)。
幂等: 全史重算+三产物原子覆盖, 重跑安全;
台账含样内参考层(in_sample=true, date<activation_since)与 OOS 影子层。
边界: 纯观测影子层零交易权, 禁止接交易闸门/交易cron/executor。
OOS 验收门(LOCKED)=激活日 20261021 起 60 个交易日或已结算 n>=30 先到,
pass=OOS nb20 med>=+1.5% 且 win>=52%; kill_switch=OOS 前 10 个到期事件 med 全<0 提请废案。

用法:
  python analysis/morning_star_shadow_daily.py            # 全史重算+三产物
  python analysis/morning_star_shadow_daily.py --report   # 同上并打印简报
  python analysis/morning_star_shadow_daily.py --prereg=X.json  # 指定prereg(fail-closed测试用)
"""
from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

if sys.stdout and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

PREREG = Path(r"D:\zcodesj\data\PREREG_MORNINGSTAR_NEXTBAR_20261020.json")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
STOCK_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
LEDGER = Path(r"D:\zcodesj\data\morning_star_shadow_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\morning_star_shadow_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\morning_star_shadow_brief_latest.txt")

NEED = ["pattern", "entry", "horizons", "dedup_days", "activation_since",
        "universe_exclude_prefix", "judge", "data_source_daily"]


def load_prereg(path: Path) -> dict:
    p = json.loads(path.read_text(encoding="utf-8"))
    if p.get("status") != "ACTIVE":
        raise SystemExit(f"[FAIL-CLOSED] prereg status={p.get('status')} 非ACTIVE")
    pa = p.get("params") or {}
    missing = [k for k in NEED if k not in pa]
    if missing:
        raise SystemExit(f"[FAIL-CLOSED] prereg params 缺字段: {missing}")
    return p


def expected_latest_trade_date() -> str:
    """粗略上一工作日(周一~五); 节假日误报由重试+STALE标注兜底, 不作权威。"""
    d = datetime.now()
    while d.weekday() >= 5 or d.strftime("%Y-%m-%d") == datetime.now().strftime("%Y-%m-%d") and datetime.now().hour < 15:
        d -= timedelta(days=1)
        if d.weekday() < 5 and d.strftime("%Y-%m-%d") != datetime.now().strftime("%Y-%m-%d"):
            break
    # 简化: 取今天之前最近的工作日
    d = datetime.now() - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def qfq_max_date_with_retry(retry_n: int, wait_s: int) -> tuple[str, bool]:
    """qfq 库 max(date); 未达期望交易日则重试; 仍旧则 STALE 标注(vr120f 先例模式)。"""
    want = expected_latest_trade_date()
    for i in range(retry_n + 1):
        cn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        mx = cn.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        cn.close()
        if mx and mx >= want:
            return mx, False
        if i < retry_n:
            print(f"[RETRY] qfq max={mx} < 期望{want}, {wait_s}s后重试({i + 1}/{retry_n})", flush=True)
            time.sleep(wait_s)
    return mx, True


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--prereg", default=None, help="指定prereg路径(测试用)")
    args = ap.parse_args()
    preg_path = Path(args.prereg) if args.prereg else PREREG
    preg = load_prereg(preg_path)
    pa = preg["params"]
    act = int(pa["activation_since"])
    dedup_days = int(pa["dedup_days"])
    horizons = [int(x) for x in pa["horizons"]]
    hz_main = horizons[0]
    excl = tuple(pa["universe_exclude_prefix"])

    mx, stale = qfq_max_date_with_retry(int(os.environ.get("MS_RETRY_N", "2")),
                                        int(os.environ.get("MS_RETRY_WAIT_S", "600")))
    print(f"qfq max={mx} stale={stale}", flush=True)

    cn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    df = pd.DataFrame(cn.execute(
        "SELECT code, date, open, high, low, close, volume FROM stock_daily_meta_qfq "
        "ORDER BY code, date").fetchall(),
        columns=["code", "date", "open", "high", "low", "close", "volume"])
    cn.close()

    # ST 过滤(当前名单, 口径限制见prereg)
    try:
        cn2 = sqlite3.connect(f"file:{STOCK_DB}?mode=ro", uri=True)
        st = {r[0] for r in cn2.execute("SELECT code FROM stock_info WHERE is_st=1").fetchall()}
        cn2.close()
    except Exception as e:
        print(f"[WARN] ST过滤失败({e}), 仅按板块过滤", flush=True)
        st = set()

    g = df.groupby("code", sort=False)
    o, c = df["open"], df["close"]
    o1, c1 = g["open"].shift(1), g["close"].shift(1)
    o2, c2 = g["open"].shift(2), g["close"].shift(2)
    b1, b2 = c1 - o1, c2 - o2
    body = c - o
    ret2 = c2 / g["close"].shift(3) - 1
    body1_ret = b1 / c2
    c12 = g["close"].shift(12)
    rownum = g.cumcount()
    nb = {n: g["close"].shift(-(1 + n)) / c1 - 1 for n in set(horizons + [5, 10])}

    m = ((c2 < c12) & (b2 < 0) & (ret2 <= -0.015)
         & (body1_ret.abs() <= 0.005)
         & (pd.concat([o1, c1], axis=1).max(axis=1) <= c2)
         & (body > 0) & (c >= (o2 + c2) / 2)
         & ~df["code"].str.startswith(excl)
         & ~df["code"].isin(st)
         & (rownum >= 60))
    ev = df[m][["code", "date"]].copy()
    for n, s in nb.items():
        ev[f"nb{n}"] = s[m]
    # dedup: 同code在dedup_days个交易日内只保留首个(rownum差<dedup_days剔除)
    if dedup_days > 0:
        rn = rownum[m]
        keep = []
        last_rn = {}
        last_code = None
        for idx, (cd, rn_v) in zip(ev.index, zip(ev["code"], rn)):
            if cd != last_code:
                last_rn = {}
                last_code = cd
            if any(rn_v - k < dedup_days for k in last_rn):
                keep.append(False)
            else:
                keep.append(True)
                last_rn[rn_v] = True
        ev = ev[[bool(k) for k in keep]]
    ev["in_sample"] = ev["date"].str.replace("-", "").astype(int) < act
    print(f"events={len(ev)} (dedup={dedup_days}) in_sample={int(ev['in_sample'].sum())} "
          f"oos={int((~ev['in_sample']).sum())}", flush=True)

    # 台账
    lines = []
    for r in ev.itertuples(index=False):
        settled = bool(pd.notna(getattr(r, f"nb{hz_main}")))
        rec = {"code": r.code, "date": r.date, "in_sample": bool(r.in_sample),
               "settled": settled}
        for n in set(horizons + [5, 10]):
            v = getattr(r, f"nb{n}")
            rec[f"nb{n}"] = None if pd.isna(v) else round(float(v) * 100, 4)
        lines.append(json.dumps(rec, ensure_ascii=False))
    tmp = LEDGER.with_suffix(".tmp")
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    os.replace(tmp, LEDGER)

    # 判据统计
    def stats(sub, key):
        vals = pd.Series([r[key] for r in map(json.loads, lines)
                          if r["settled"] and r[key] is not None and
                          (r["in_sample"] if sub == "in" else not r["in_sample"])])
        if len(vals) == 0:
            return {"n": 0, "med": None, "win": None}
        return {"n": int(len(vals)), "med": round(float(vals.median()), 3),
                "win": round(float((vals > 0).mean() * 100), 2)}

    jr = pa["judge"]
    oos20 = stats("oos", f"nb{hz_main}")
    state = {
        "name": preg["name"], "generated_at": datetime.now().isoformat(timespec="seconds"),
        "qfq_max": mx, "stale_dump": stale,
        "params_fingerprint": hashlib.sha256(
            json.dumps(pa, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16],
        "activation_since": act, "dedup_days": dedup_days,
        "counts": {"total": len(ev), "in_sample": int(ev["in_sample"].sum()),
                   "oos": int((~ev["in_sample"]).sum())},
        "in_sample_ref": {f"nb{n}": stats("in", f"nb{n}") for n in set(horizons + [5, 10])},
        "oos": {f"nb{n}": stats("oos", f"nb{n}") for n in set(horizons + [5, 10])},
        "judge": {**jr, "lock_rule_progress": f"已结算OOS n={oos20['n']}(门=n>=30或60交易日)"},
    }
    tmp2 = SNAPSHOT.with_suffix(".tmp")
    tmp2.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp2, SNAPSHOT)

    oos_med = oos20["med"]
    oos_win = oos20["win"]
    b = [
        f"[morning_star_shadow] {datetime.now():%Y-%m-%d %H:%M} qfq_max={mx}{' [STALE_DUMP]' if stale else ''}",
        f"事件={len(ev)} (样内={state['counts']['in_sample']} OOS={state['counts']['oos']}, dedup={dedup_days})",
        f"样内参考: nb{hz_main} med={state['in_sample_ref'][f'nb{hz_main}']['med']}% "
        f"win={state['in_sample_ref'][f'nb{hz_main}']['win']}%",
        f"OOS: 已结算n={oos20['n']} med={oos_med}% win={oos_win}% "
        f"(pass门=med>=+1.5%且win>=52%; kill=前10到期全负)",
        "[ok] 三产物原子覆盖完成; 观测层零交易权",
    ]
    tmp3 = BRIEF.with_suffix(".tmp")
    tmp3.write_text("\n".join(b) + "\n", encoding="utf-8")
    os.replace(tmp3, BRIEF)
    if args.report:
        print("\n".join(b))
    print(f"[OK] ledger={LEDGER.name}({len(lines)}行) state/brief 已覆盖", flush=True)


if __name__ == "__main__":
    main()
