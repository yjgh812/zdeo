# -*- coding: utf-8 -*-
"""右侧启动呈请3切片: 严定义事件 × PD-034情绪档分层重验 (2026-10-11)

呈请来源: rightside_spec_coverage §三呈请3(情绪维), 同时闭合宽定义研究悬置项「严定义×情绪档分层重验」。
家数源(与PD-034v2/R37同源, 原样复刻其链式口径): qfq库 close/prev_close-1 >= 9.5% 逐日计数,
  宇宙=0/3/6开头剔688/689(ST含在分母, 名册不在qfq); 档位=band(家数(D-1)), T-1位移同PD-034。
  band: >=80超强 / 61-79强 / 40-60中 / <40弱。
自检: ①2019+窗档位天数=弱323/中552/强422/超强584(与PD-034脚本逐日对拍±2, close链舍入差)。
  🔴顺序勘误: PD-034v2提交与记忆所记「583/422/552/323」为[超强,强,中,弱]降序——弱323/超强584,
  本件据此定序; 转写未标顺序系误读源(本件commit一并勘误)。
判读: 收官headline=严定义h60全档+3.19%; 宽定义P2|超强格+2.54%为情绪调制样本——本件回答
  「严定义漏斗是否也被情绪档调制」。分年+2019+子窗(PD-034可比)+ACTIVE漏斗子集(pos60≤40∧缩量≤0.75)。
输出: stdout + reports/rightside_startup_20261011/emotion_band_summary.json
性质: 描述性研究件零交易权。
"""
import os, sys, json, sqlite3
from collections import defaultdict
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EV = r"D:\zcodesj\reports\rightside_startup_20261011\events_main.csv"
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT = r"D:\zcodesj\reports\rightside_startup_20261011\emotion_band_summary.json"
START = "20120101"  # 事件域起点(档位天需要D-1, 首个计数日20120104起)


def band_of(cnt):
    if cnt >= 80:
        return "超强"
    if cnt >= 61:
        return "强"
    if cnt >= 40:
        return "中"
    return "弱"


def build_counts():
    con = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    cur = con.execute(
        "SELECT code, date, close FROM stock_daily_meta_qfq "
        "WHERE date>=? AND (substr(code,1,1) IN ('0','3','6')) ORDER BY code, date",
        (START,))
    prev_c, cnt = {}, defaultdict(int)
    for code, d, c in cur:
        if code[:3] in ("688", "689") or code[:1] in ("4", "8"):
            continue
        p = prev_c.get(code)
        prev_c[code] = c
        if p and p > 0 and c / p - 1 >= 0.095:
            cnt[int(d)] += 1
    con.close()
    return cnt


def stat(v):
    v = np.asarray(v, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < 3:
        return {"n": int(len(v))}
    t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))
    plr = float(v[v > 0].mean() / -v[v < 0].mean()) if (v < 0).any() and (v > 0).any() else None
    return {"n": int(len(v)), "mean": round(float(v.mean()), 3),
            "win": round(float((v > 0).mean() * 100), 1), "t": round(t, 2),
            "plr": round(plr, 2) if plr else None}


def main():
    cnt = build_counts()
    days = sorted(cnt)
    day_set = set(days)
    print(f"[家数] days={len(days)} {days[0]}~{days[-1]}")
    w19 = [d for d in days if d >= 20190101]
    chk = pd.Series([cnt[d] for d in w19])
    b19 = pd.cut(chk, [-1, 39, 60, 79, 10**9], labels=["弱", "中", "强", "超强"]).value_counts().reindex(["弱", "中", "强", "超强"])
    ok1 = all(abs(int(b19[k]) - t) <= 2 for k, t in [("弱", 323), ("中", 552), ("强", 422), ("超强", 584)])
    print(f"[check1] 2019+档位天数 弱{b19['弱']}/中{b19['中']}/强{b19['强']}/超强{b19['超强']} (PD-034同源目标323/552/422/584) {'PASS' if ok1 else 'FAIL'}")

    def band_of_date(D):
        # band(D)=家数(D-1): 取严格小于D的最近计数日
        import bisect
        i = bisect.bisect_left(days, D)
        if i == 0:
            return None
        return band_of(cnt[days[i - 1]])

    em = pd.read_csv(EV, dtype={"code": str})
    em["band"] = [band_of_date(int(d)) for d in em.d_date]
    mapped = em.band.notna().sum()
    print(f"[band] 事件映射 {mapped}/{len(em)}")
    funnel = (em.pos60 <= 40) & (em.pre_shrink <= 0.75)

    out = {"meta": {"date": "2026-10-11", "spec": "呈请3情绪维切片+宽定义悬置闭合",
                    "source": "PD-034v2同源(qfq close链>=9.5%计数, T-1位移)",
                    "order_erratum": "PD-034v2所记583/422/552/323为[超强,强,中,弱]降序=弱323/超强584",
                    "check1_pass": bool(ok1), "events_mapped": int(mapped)},
           "h60_by_band_all": {}, "h60_by_band_funnel": {}, "h20_by_band_all": {}, "by_year_all": {}}
    print("\n=== 净h60 × 情绪档(全体4,313) ===")
    for b in ["弱", "中", "强", "超强"]:
        s = stat(em.loc[em.band == b, "f60_net"])
        out["h60_by_band_all"][b] = s
        print(f"{b}: {s}")
    print("=== 净h60 × 情绪档(ACTIVE漏斗 pos60≤40∧缩量≤0.75) ===")
    for b in ["弱", "中", "强", "超强"]:
        s = stat(em.loc[funnel & (em.band == b), "f60_net"])
        out["h60_by_band_funnel"][b] = s
        print(f"{b}: {s}")
    print("=== 净h20 × 情绪档(全体) ===")
    for b in ["弱", "中", "强", "超强"]:
        out["h20_by_band_all"][b] = stat(em.loc[em.band == b, "f20_net"])
    print("=== 分年(全体, mean/win) ===")
    em["year"] = em.d_date // 10000
    for y, g in em.dropna(subset=["band"]).groupby("year"):
        row = {b: stat(g.loc[g.band == b, "f60_net"]) for b in ["弱", "中", "强", "超强"]}
        out["by_year_all"][int(y)] = row
        print(y, {k: f"{v.get('n',0)}/{v.get('mean','-')}" for k, v in row.items()})
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT}")


if __name__ == "__main__":
    main()
