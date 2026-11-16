#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
droe_pit_top20_picker.py — 生产版 droe PIT Top20 选股器
========================================================
依据 2026-08-15 DSR 全检验结论（Sharpe 1.082 / DSR(N=5) 0.967 首个通过）。
口径与回测 crossval_dsr_wrapper.py 完全一致：
  - 因子: factor_pit.db (pit_avail_date<=T 最新报告期, 无前视)
  - 域过滤: 原始收盘价<=30元 / 非ST / 非金融 / 前缀60/00/30 (沪深主板+创业板;
    宪法§0 剔除科创/北交所, 2026-09-02 前缀闸门物化——此前 docstring 的 68 与实际无前缀过滤均违宪, 920575 实证漏网)
  - 排序: droe 降序, 行业≤4只, Top20
  - 名称: stock_name_lookup.require_name (铁律)
用法:
  python droe_pit_top20_picker.py            # 最新截面
  python droe_pit_top20_picker.py --date 20260814
"""
import sys, sqlite3, argparse, time
sys.path.insert(0, "D:/zcodesj")
import numpy as np
import pandas as pd
import factor_pit_revalidate_20260815 as reval
from stock_name_lookup import require_name

PIT_DB = r"D:\zcodesj\factor_pit.db"
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
OUT_MD = r"D:\zcodesj\reports\DROE_PIT_TOP20_PICKS_{T}.md"


def latest_fund_date():
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    d = con.execute("SELECT MAX(date) FROM stock_factors_fundamental").fetchone()[0]
    con.close()
    return d


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", type=str, default=None, help="截面日期 yyyymmdd, 默认最新")
    ap.add_argument("--topn", type=int, default=20)
    args = ap.parse_args()

    T_raw = args.date or latest_fund_date()
    T = int(T_raw)   # pit_avail_date 为 int64，比较前必须归一化（--date 传入的是 str）
    print(f"[picker] 截面日 T={T}  TopN={args.topn}")
    print(f"[picker] 口径: PIT(pit_avail_date<=T) + 原始close<=30 + 非ST/金融 + 行业<=4")

    # ── PIT 截面 (无前视) ──
    pit = reval.load_pit_full()
    snap = reval.pit_snap_at(pit, T)   # pit_avail_date<=T 每股最新报告期, 陈旧度<=400天
    if snap.empty:
        print("[picker] ❌ PIT截面为空"); return 1
    print(f"[picker] PIT截面: {len(snap)}只 (报告期陈旧度<=400天)")

    # ── 剔除集 ──
    st, fin, ind_map = reval.load_exclusions()
    excl = st | fin
    snap = snap[~snap.index.isin(excl)]
    print(f"[picker] 剔除ST/退市/金融后: {len(snap)}只")

    # ── T日原始收盘价 (域过滤+可用性) ──
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    rc = pd.read_sql_query(
        "SELECT code, close FROM stock_factors_fundamental WHERE date=?",
        con, params=[str(T)])
    con.close()
    if rc.empty:
        print(f"[picker] ❌ FAIL-CLOSED: 主库无 T={T} 截面数据（日期不存在或未入库）。"
              f"最新可用截面: {latest_fund_date()}。禁止产出空清单。")
        return 1
    rc["code"] = rc["code"].astype(str).str.zfill(6)
    rc = rc.set_index("code")["close"]

    # ── 组合截面 ──
    g = snap[["droe"]].dropna().copy()
    g = g[g["droe"] != np.inf]
    g = g[g["droe"] != -np.inf]
    # 域前缀闸门(宪法§0): 沪深主板+创业板=60/00/30; 剔除科创68/北交所920等 ——
    # stock_info 排除集对缺表新码(如920575)不设防, 前缀闸门是唯一可靠边界(9/2 实证破口)
    g = g[g.index.str.startswith(("60", "00", "30"))]
    g["close"] = rc.reindex(g.index)
    g = g.dropna(subset=["close"])
    g = g[g["close"] <= 30.0]
    if g.empty:
        print("[picker] ❌ FAIL-CLOSED: 价格过滤后候选池为空，禁止产出空清单。")
        return 1
    g = g.sort_values("droe", ascending=False)
    print(f"[picker] 价格<=30元域: {len(g)}只, droe降序排列")

    # ── 行业<=4, TopN ──
    picks, used = [], {}
    for code in g.index:
        if len(picks) >= args.topn:
            break
        ind = ind_map.get(code, "UNKNOWN")
        if used.get(ind, 0) >= 4:
            continue
        used[ind] = used.get(ind, 0) + 1
        picks.append(code)

    # ── 输出 ──
    rows = []
    for i, c in enumerate(picks, 1):
        rows.append({
            "名次": i, "代码": c, "名称": require_name(c),
            "现价": round(float(g.loc[c, "close"]), 2),
            "droe": round(float(g.loc[c, "droe"]), 4),
            "行业": ind_map.get(c, "UNKNOWN"),
        })
    df = pd.DataFrame(rows)

    # 报告期分布
    rpt = snap.loc[picks, "report_date"].astype(str)
    print(f"\n[picker] 报告期分布: {rpt.value_counts().to_dict()}")

    # B组对比
    b_group = {"000430","002640","000506","603626","002582","300159","002795",
               "300313","002379","300492","600961","300839","002520","002259",
               "300444","002291","000692","603399","300773","300511","601069","002848"}
    overlap = set(picks) & b_group
    print(f"[picker] 与B组(WF-icir Top22)重合: {len(overlap)}/{len(picks)}")

    # 写报告
    out = OUT_MD.format(T=T)
    with open(out, "w", encoding="utf-8") as f:
        f.write(f"# droe PIT Top{T and args.topn} 选股清单 {T}\n\n")
        f.write(f"- 生成: {time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"- 口径: PIT(pit_avail_date<=T) + close<=30 + 非ST/金融 + 行业<=4 + Top{args.topn}\n")
        f.write(f"- 依据: DSR(N=5)=0.967 首个通过完整多重检验 (2026-08-15)\n")
        f.write(f"- 与B组(WF-icir)重合: {len(overlap)}/{len(picks)}\n\n")
        f.write(df.to_markdown(index=False))
        f.write("\n\n## 重合B组标的\n\n")
        if overlap:
            for c in sorted(overlap):
                f.write(f"- {c} {require_name(c)}\n")
        else:
            f.write("(无)\n")
    print(f"\n[picker] 已保存: {out}")
    print(df.to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())