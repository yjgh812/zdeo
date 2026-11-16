# -*- coding: utf-8 -*-
"""右侧启动呈请3宏观切片: 严定义事件 × PMI/CPI/M2 分层 (2026-10-11)

呈请来源: rightside_spec_coverage §三呈请3(宏观维, "PMI/CPI/M2宏观库在库")。
数据: akshare(系统python 1.18.55)直接拉取月度序列, 零人工转抄; 拉取失败该指标如实N/A。
PIT口径(预声明): 月M数据可用日 = PMI M+1日1 / CPI M+1日10 / M2 M+1日12;
  事件D取可用日严格早于D的最新读数(宁保守不前视)。
分桶(预声明结构性阈值, 非数据驱动): PMI <48/48-50/50-51/>51; CPI同比 <0/0-2/>2;
  M2同比 <9/9-10.5/>10.5(2012-2026区间8~14, 阈值注记); PMI方向=较3月前升/降。
判据: 净h60(收官主口径)。自检: ①事件映射覆盖率 ②抽样月对拍打印(人眼核非断言)。
输出: stdout + reports/rightside_startup_20261011/macro_band_summary.json
性质: 描述性研究件零交易权。宏观月度变量对h60这类60交易日窗的分辨力天然有限,
  期望管理=环境描述层, 非选股信号。
"""
import os, sys, json
import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

EV = r"D:\zcodesj\reports\rightside_startup_20261011\events_main.csv"
OUT = r"D:\zcodesj\reports\rightside_startup_20261011\macro_band_summary.json"
LAG = {"pmi": 1, "cpi": 10, "m2": 12}  # M+lag日可用(保守)


def stat(v):
    v = np.asarray(v, dtype=float)
    v = v[~np.isnan(v)]
    if len(v) < 5:
        return {"n": int(len(v))}
    t = float(v.mean() / (v.std(ddof=1) / np.sqrt(len(v))))
    return {"n": int(len(v)), "mean": round(float(v.mean()), 3),
            "win": round(float((v > 0).mean() * 100), 1), "t": round(t, 2)}


def fetch_series():
    import akshare as ak
    out = {}
    # PMI(制造业, 月度)
    try:
        df = ak.macro_china_pmi()
        cands = [c for c in df.columns if "制造" in str(c) or "pmi" in str(c).lower()]
        dcol = [c for c in df.columns if "月" in str(c) or "日期" in str(c) or "date" in str(c).lower()][0]
        s = df[[dcol, cands[0]]].dropna()
        out["pmi"] = {str(r[0])[:7]: float(r[1]) for r in s.itertuples(index=False)}
        print(f"[fetch] PMI rows={len(s)} cols={list(df.columns)[:6]}")
    except Exception as e:
        print(f"[fetch] PMI FAIL {e}")
    # CPI同比(全国, 月度)
    try:
        df = ak.macro_china_cpi()
        cols = list(df.columns)
        mcol = cols[0]
        ycol = [c for c in cols if "同比" in str(c)][0]
        s = df[[mcol, ycol]].dropna()
        out["cpi"] = {str(r[0])[:7]: float(r[1]) for r in s.itertuples(index=False)}
        print(f"[fetch] CPI rows={len(s)} ycol={ycol}")
    except Exception as e:
        print(f"[fetch] CPI FAIL {e}")
    # M2同比(月度)
    try:
        df = ak.macro_china_money_supply()
        cols = list(df.columns)
        mcol = cols[0]
        ycol = [c for c in cols if "M2" in str(c) and "同比" in str(c)][0]
        s = df[[mcol, ycol]].dropna()
        out["m2"] = {str(r[0])[:7]: float(r[1]) for r in s.itertuples(index=False)}
        print(f"[fetch] M2 rows={len(s)} ycol={ycol}")
    except Exception as e:
        print(f"[fetch] M2 FAIL {e}")
    return out


def to_series(d):
    """{月key: val} -> (month_index list, val list); 月key兼容'2024-10'/'2024年10月'等格式"""
    import re
    ks, vs = [], []
    for k, v in d.items():
        mobj = re.search(r"(\d{4})\D*(\d{1,2})", str(k))
        if not mobj:
            continue
        ks.append(int(mobj.group(1)) * 12 + int(mobj.group(2)) - 1)
        vs.append(float(v))
    o = np.argsort(ks)
    return [ks[i] for i in o], [vs[i] for i in o]


def pit_read(ks, vs, lag_days, date_int):
    """date_int=YYYYMMDD; 返回可用日严格早于date的最新读数"""
    import datetime
    y, m, dd = date_int // 10000, date_int // 100 % 100, date_int % 100
    cur = datetime.date(y, m, dd)
    best = None
    for k, v in zip(ks, vs):
        yy, mm = divmod(k, 12)
        mm = mm + 1
        ny, nm = (yy + 1, 1) if mm == 12 else (yy, mm + 1)
        try:
            avail = datetime.date(ny, nm, lag_days)
        except ValueError:
            avail = datetime.date(ny, nm, 28)
        if avail < cur:
            best = v
        else:
            break
    return best


def main():
    ser = fetch_series()
    em = pd.read_csv(EV, dtype={"code": str})
    em["year"] = em.d_date // 10000
    funnel = (em.pos60 <= 40) & (em.pre_shrink <= 0.75)
    out = {"meta": {"date": "2026-10-11", "spec": "呈请3宏观切片(PMI/CPI/M2)",
                    "pit": "PMI M+1日1 / CPI M+1日10 / M2 M+1日12 可用, 事件取可用日严格早于D的最新读数",
                    "buckets": "PMI<48/48-50/50-51/>51; CPI<0/0-2/>2; M2<9/9-10.5/>10.5 (预声明结构性)",
                    "coverage": {}, "layers": {}}, "funnel_layers": {}, "pmi_dir": {}}
    BUCK = {"pmi": [(-99, 48, "<48"), (48, 50, "48-50"), (50, 51, "50-51"), (51, 99, ">51")],
            "cpi": [(-99, 0, "<0"), (0, 2, "0-2"), (2, 99, ">2")],
            "m2": [(-99, 9, "<9"), (9, 10.5, "9-10.5"), (10.5, 99, ">10.5")]}
    for key in ["pmi", "cpi", "m2"]:
        if key not in ser:
            out["meta"]["coverage"][key] = "N/A(fetch fail)"
            continue
        ks, vs = to_series(ser[key])
        print(f"[series] {key}: {len(ks)}个月 {ks[0]//12}-{ks[0]%12+1:02d} ~ {ks[-1]//12}-{ks[-1]%12+1:02d}")
        vals = [pit_read(ks, vs, LAG[key], int(d)) for d in em.d_date]
        arr = pd.Series(vals, dtype=float)
        cov = float(arr.notna().mean())
        out["meta"]["coverage"][key] = f"{cov*100:.1f}%"
        print(f"[pit] {key} 事件映射覆盖率 {cov*100:.1f}%  抽样: {em.d_date.iloc[100]}->{vals[100]} | {em.d_date.iloc[2000]}->{vals[2000]}")
        layers = {}
        for lo, hi, lab in BUCK[key]:
            m = (arr >= lo) & (arr < hi)
            layers[lab] = stat(em.loc[m, "f60_net"])
        out["meta"]["layers"][key] = layers
        print(f"=== h60 x {key.upper()} ===")
        for lab, s in layers.items():
            print(f"  {lab}: {s}")
        # 漏斗子集
        fl = {}
        for lo, hi, lab in BUCK[key]:
            m = (arr >= lo) & (arr < hi)
            fl[lab] = stat(em.loc[m & funnel, "f60_net"])
        out["funnel_layers"][key] = fl
        # PMI方向(较3个月前)
        if key == "pmi" and len(ks) > 3:
            mi = {k: v for k, v in zip(ks, vs)}
            dirs = []
            for d, v in zip(em.d_date, vals):
                if v is None or np.isnan(v):
                    dirs.append(np.nan)
                    continue
                y, mth, dd = int(d) // 10000, int(d) // 100 % 100, int(d) % 100
                cur_i = y * 12 + mth - 1
                prev = mi.get(cur_i - 3)
                dirs.append("升" if (prev is not None and v > prev) else "降")
            dd_ = {}
            for lab in ["升", "降"]:
                m = pd.Series(dirs) == lab
                dd_[lab] = stat(em.loc[m.values, "f60_net"])
            out["pmi_dir"] = dd_
            print(f"=== h60 x PMI 3月方向 === 升: {dd_['升']} | 降: {dd_['降']}")
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print(f"[out] {OUT}")


if __name__ == "__main__":
    main()
