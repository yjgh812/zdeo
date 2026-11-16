#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deepdrawdown_shadow.py — PD-035 深回撤分歧低吸观察信号 影子/回填件 (2026-10-07)

模式=PD-031 deepfirst同构第三次复用。判据LOCKED(PREREG_DEEPDRAWDOWN_SHADOW_20261007):
  宇宙: 剔688/8/4/920开头; 当日跌停(<=-9.5%/-19.5%)剔除(崩塌不接)
  三合取: drawdown10>=0.08 (1-close/max(high[T-9..T]))
        × gene60>=3 (近60交易日涨停次数, 股性激活=馒头八件套口径; v1 gene>=1密度424/日失效已废)
        × 行业(同花顺L1)当日涨停家数>=3 (主线级=40轮主线段口径)
  fwd: f5/f20收盘锚定; 分层: 情绪档(强>=61/中40-60/弱<40)+分年
用途: backfill全史参考层(in_sample)——v2结果全线负, PD-035已废案(FALSIFIED_BY_BACKFILL_20261007), 台账保留作证据, 禁止前向复活
台账: data/deepdrawdown_shadow_ledger.jsonl(backfill整体重建幂等); 零交易权
"""
import json
import sqlite3
import sys
from collections import defaultdict, deque

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
WB = r"D:\workbuddyspace\databases\fundamental_data.db"
LEDGER = r"D:\zcodesj\data\deepdrawdown_shadow_ledger.jsonl"
OUT_SUMMARY = r"D:\zcodesj\tmp\deepdrawdown_backfill_summary_20261007.json"

DD_TH, GENE_TH, IND_ZT_TH = 0.08, 3, 3
STRONG, MID = 61, 40
GENE_WIN = 60
HIGH_WIN = 10


def load_ind_map():
    conn = sqlite3.connect(f"file:{WB}?mode=ro", uri=True)
    m = {}
    for code, ind in conn.execute("SELECT code, industry FROM industry_map_ths"):
        if code and ind:
            m[code.strip()] = ind.split("-")[0]
    conn.close()
    return m


def med_win(xs):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return {"n": 0}
    n = len(xs)
    med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    return {"n": n, "med": round(med, 3),
            "win": round(100 * sum(1 for x in xs if x > 0) / n, 1)}


def main():
    ind_map = load_ind_map()
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    dates = [r[0] for r in conn.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date")]
    didx = {d: i for i, d in enumerate(dates)}
    ind_zt_cnt = [defaultdict(int) for _ in dates]
    mkt_zt = [0] * len(dates)

    sig_codes = set()
    sig_events = []
    # 每股状态: 60日窗(zt计数)+10日high窗
    state = {}  # code -> {"dq":deque[(date,zt)], "hq":deque[high]}
    cur = conn.execute(
        "SELECT code, date, high, close, change_pct FROM stock_daily_meta_qfq "
        "WHERE (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date")
    for code, d, hi, cl, chg in cur:
        st = state.get(code)
        if st is None:
            st = state[code] = {"dq": deque(maxlen=GENE_WIN), "hq": deque(maxlen=HIGH_WIN)}
        gem = code.startswith(("300", "301"))
        zt_th = 19.5 if gem else 9.5
        is_zt = chg is not None and chg >= zt_th
        i = didx[d]
        if is_zt:
            mkt_zt[i] += 1
            ind = ind_map.get(code)
            if ind:
                ind_zt_cnt[i][ind] += 1
        st["dq"].append((d, is_zt))
        st["hq"].append(hi)
        gene60 = sum(1 for _, z in st["dq"] if z)
        if chg is None or chg <= -zt_th or len(st["hq"]) < HIGH_WIN or gene60 < GENE_TH:
            continue
        dd = 1.0 - cl / max(st["hq"])
        if dd < DD_TH:
            continue
        ind = ind_map.get(code)
        if not ind or ind_zt_cnt[i][ind] < IND_ZT_TH:
            continue
        sig_codes.add(code)
        sig_events.append((d, code, round(dd, 3), i, cl))
    conn.close()
    print(f"signal events={len(sig_events)} codes={len(sig_codes)}")

    close_seq = defaultdict(dict)
    conn = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    for code, d, cl in conn.execute("SELECT code, date, close FROM stock_daily_meta_qfq ORDER BY code, date"):
        if code in sig_codes:
            close_seq[code][d] = cl
    conn.close()

    out_rows = []
    for d, code, dd, i, cl in sig_events:
        cs = close_seq[code]
        ds = sorted(cs)
        j = ds.index(d)
        f5 = cs[ds[j + 5]] / cl - 1 if j + 5 < len(ds) else None
        f20 = cs[ds[j + 20]] / cl - 1 if j + 20 < len(ds) else None
        zt = mkt_zt[i]
        tier = "strong" if zt >= STRONG else ("mid" if zt >= MID else "weak")
        out_rows.append({"date": d, "code": code, "dd10": dd, "mkt_zt": zt, "tier": tier,
                          "f5": round(f5 * 100, 3) if f5 is not None else None,
                          "f20": round(f20 * 100, 3) if f20 is not None else None,
                          "in_sample": True})

    by_tier = defaultdict(lambda: defaultdict(list))
    by_year = defaultdict(lambda: defaultdict(list))
    for r in out_rows:
        for k in ("f5", "f20"):
            by_tier[r["tier"]][k].append(r[k])
            by_year[r["date"][:4]][k].append(r[k])
    summary = {
        "total": len(out_rows),
        "all": {k: med_win([r[k] for r in out_rows]) for k in ("f5", "f20")},
        "by_tier": {t: {k: med_win(v) for k, v in d.items()} for t, d in sorted(by_tier.items())},
        "by_year": {y: {k: med_win(v) for k, v in d.items()} for y, d in sorted(by_year.items())},
        "judge_pinned": "前向判据: f5 med>=+0.5% 且 win>=52% 且 n>=30 (>=60交易日), 任一不过即废",
    }
    with open(LEDGER, "w", encoding="utf-8") as f:
        for r in out_rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    with open(OUT_SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=1)
    print(json.dumps(summary, ensure_ascii=False, indent=1))
    print(f"rows={len(out_rows)} -> {LEDGER}")


if __name__ == "__main__":
    main()
