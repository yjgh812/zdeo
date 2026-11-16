# -*- coding: utf-8 -*-
r"""erp_low_gate_daily.py — ERP×低区双票市场底层·只读读出【侦察观测层, 零交易权】
目的: PD-015呈请(ERP分位≥80∧低区占比≥60=市场底开仓门, f60+9.08%/win70.1%, 敏感性平台已证)
在裁决前的只读监测载体——每日输出两票状态/距阈值距离/历史大底对照, 供裁决后即时接线与人工研判。
口径: ERP分位=erp_history.db erp_pct_5y(int日期⚠str规范化); 低区占比=全A(剔688/689/4/8/ST)
pos250<40占比(门A同口径, portrait_gate_shadow.py); 合取判读=reports/verify_erp_double_gate_20261003.md。
铁律: 只读观测零下单; 输出为环境事实呈现非买卖指令; 无数据如实N/A不编造。
用法:
    python erp_low_gate_daily.py            # 当日读出(本地库最新交易日)
    python erp_low_gate_daily.py --history  # 近段双票亮灯日清单
"""
from __future__ import annotations
import argparse, sqlite3, sys
import numpy as np

ROOT = r"D:\zcodesj"
ERP_DB = r"file:D:/zcodesj/erp_history.db?mode=ro"
QFQ_DB = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"  # 2026-10-13 切wb唯一真源(旧zc副本停更10/09已废, 409ef2c域); 前缀0/3/6过滤天然剔北交所, 翻转零口径变化
LOW_POS_THR = 40      # 门A口径: 低区=pos250<40
ERP_THR, LOW_THR = 80, 60
try: sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception: pass

def load_erp():
    con = sqlite3.connect(ERP_DB, uri=True)
    rows = con.execute("SELECT date, index_close, erp, erp_pct_5y FROM erp_daily ORDER BY date").fetchall()
    con.close()
    return [(str(d), px, erp, p) for d, px, erp, p in rows if p is not None]

def low_share_series():
    from stock_name_lookup import get_name
    con = sqlite3.connect(QFQ_DB, uri=True)
    cur = con.execute("SELECT code, date, close FROM stock_daily_meta_qfq "
                      "WHERE (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date")
    by_code = {}
    while True:
        chunk = cur.fetchmany(500000)
        if not chunk: break
        for code, d, c in chunk:
            by_code.setdefault(code, ([], []))
            by_code[code][0].append(str(d)); by_code[code][1].append(c)
    con.close()
    st = {c for c in by_code if "ST" in (get_name(c) or "").upper() or "退" in (get_name(c) or "")}
    daily = {}  # date -> [total, n_low]
    for code, (dates, closes) in by_code.items():
        if code in st or len(closes) < 250: continue
        c = np.asarray(closes, float)
        sw = np.lib.stride_tricks.sliding_window_view(c, 250)
        pos = np.concatenate([np.full(249, np.nan),
                              (sw[:, -1]-sw.min(axis=1))/(sw.max(axis=1)-sw.min(axis=1))*100])
        for i, d in enumerate(dates):
            if np.isnan(pos[i]): continue
            rec = daily.setdefault(d, [0, 0])
            rec[0] += 1
            if pos[i] < LOW_POS_THR: rec[1] += 1
    return {d: n/t*100 if t else 0.0 for d, (t, n) in daily.items()}

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--history", action="store_true")
    a = ap.parse_args()
    erp = load_erp()
    share = low_share_series()
    erp_by_d = {d: (px, e, p) for d, px, e, p in erp}
    common = [d for d in sorted(share) if d in erp_by_d]
    if not common:
        print("[GATE] N/A: erp库与qfq库日期交集为空(检查键类型str规范化)"); return 1
    rows = []
    for d in common:
        px, e, p = erp_by_d[d]
        ls = share[d]
        rows.append((d, px, p, ls, bool(p >= ERP_THR and ls >= LOW_THR)))
    last = rows[-1]
    d, px, p, ls, both = last
    print(f"[GATE] {d} 沪深300={px:.0f} | ERP分位={p:.1f}({'亮' if p>=ERP_THR else '灭'},距{ERP_THR}线{p-ERP_THR:+.1f}) "
          f"| 低区占比={ls:.1f}%({'亮' if ls>=LOW_THR else '灭'},距{LOW_THR}线{ls-LOW_THR:+.1f}pp) "
          f"| 双票={'🔴亮(市场底开仓门条件成立)' if both else '灭'}")
    near = [r for r in rows[-250:] if (r[3] >= LOW_THR-10) != (r[3] >= LOW_THR) or (r[2] >= ERP_THR-10) != (r[2] >= ERP_THR)]
    print(f"[GATE] 近250日接近阈值(±10)天数: {len(near)} (监控接近度)")
    if a.history:
        from datetime import datetime, timedelta
        lit = [r for r in rows if r[4]]
        print(f"[GATE] 双票亮灯历史: {len(lit)}日")
        segs = []  # [start_dt, end_dt]
        for r in lit:
            dt = datetime.strptime(r[0], "%Y%m%d")
            if segs and dt <= segs[-1][1] + timedelta(days=7):
                segs[-1][1] = max(segs[-1][1], dt)
            else:
                segs.append([dt, dt])
        print("  亮灯区间:", "、".join(
            f"{a:%Y/%m%d}~{b:%m%d}" if a.year == b.year else f"{a:%Y/%m%d}~{b:%Y/%m%d}"
            for a, b in segs))
    print("[GATE] 铁律: 只读观测零下单; 亮灯=开仓门条件呈现非买入指令; 裁决权=主人(PD-015)")
    return 0

if __name__ == "__main__":
    sys.exit(main())
