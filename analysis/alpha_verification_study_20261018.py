# -*- coding: utf-8 -*-
"""P0-3 alpha 检验预研（反思文档开放项，只读零交易权）：
收益法正交化——把「没有一个剥 β 后仍显著的 alpha 来源」从断言变成实测数字。
  R_ml = α + β·R_bp_ep + ε     （剥价值腿后 ml 截距）
  R_X  = α + β·R_hs300 + γ·R_size + ε   （X=combo/bp/ml；size=中证500-沪深300 价差收益，控市值偏离）
  分年 α + 前后半窗方向稳定性 + 日聚簇 Newey-West t。
限制如实声明：收益法 α ≠ 因子级正交化；ml 腿含模型选择效应（expanding 全窗重训）；
A池统计检验已在档（10/11 FDR/BH+块bootstrap 工具 A池13臂族首用），本脚本不重做仅引用。
输出 = reports/alpha_verification_study_20261018.md（脚本直出）
"""
import sqlite3

import numpy as np
import pandas as pd
import statsmodels.api as sm

EOD = r"D:/zcodesj/reports/backtest_full_20261016_eod"
IDXDB = r"D:/workbuddyspace/factor_engine/data/dragon_quant.db"
OUT = r"D:/zcodesj/reports/alpha_verification_study_20261018.md"

L = []


def w(s=""):
    L.append(s)


def load_eq(path):
    df = pd.read_csv(path, encoding="utf-8-sig")
    val = "value" if "value" in df.columns else "equity"
    df["date"] = df["date"].astype(str)
    return df.set_index("date")[val].astype(float)


def load_idx(code):
    conn = sqlite3.connect(IDXDB)
    df = pd.read_sql_query(
        "SELECT date, close FROM index_daily WHERE index_code=? ORDER BY date", conn, params=(code,)
    )
    conn.close()
    df["date"] = df["date"].astype(str)
    s = df.set_index("date")["close"].astype(float)
    s.index = s.index.map(lambda x: x if len(x) == 10 else f"{x[:4]}-{x[4:6]}-{x[6:8]}")
    return s


eq_bp = load_eq(f"{EOD}/bp_ep_core_equity.csv")
eq_ml = load_eq(f"{EOD}/ml_signal_equity.csv")
eq_combo = load_eq(f"{EOD}/portfolio_combo_equity.csv")
hs300 = load_idx("000300")
czz500 = load_idx("000905")

r_bp = eq_bp.pct_change().dropna()
r_ml = eq_ml.pct_change().dropna()
r_combo = eq_combo.pct_change().dropna()
r_hs = hs300.pct_change().dropna()
# size 因子 = 中证500收益 - 沪深300收益（长短仓价差）
r_size = (czz500.pct_change() - hs300.pct_change()).dropna()

common = r_bp.index.intersection(r_ml.index).intersection(r_hs.index).intersection(r_size.index).intersection(r_combo.index)
rb, rm, rc, rh, rs = (r_bp.loc[common], r_ml.loc[common], r_combo.loc[common],
                      r_hs.loc[common], r_size.loc[common])
ANN = 244

# ml 启用窗：净值首日变动前为平线（未启用段，2016-2019），回归仅用启用窗防零方差稀释
ml_active = eq_ml[eq_ml != eq_ml.iloc[0]]
ml_start = ml_active.index[0]
common_ml = [d for d in common if d >= ml_start]
rb_m, rm_m = rb.loc[common_ml], rm.loc[common_ml]


def nw_ols(y, X_cols_dict, label):
    """y: Series; X_cols_dict: {name: Series}; 返回 dict(alpha年化, t_hac, betas, r2, n)"""
    df = pd.DataFrame({"y": y})
    for k, v in X_cols_dict.items():
        df[k] = v
    df = df.dropna()
    Y = df["y"]
    X = sm.add_constant(df[[k for k in X_cols_dict]])
    res = sm.OLS(Y, X).fit(cov_type="HAC", cov_kwds={"maxlags": 5})
    a = res.params.get("const", np.nan)
    t = res.tvalues.get("const", np.nan)
    p = res.pvalues.get("const", np.nan)
    betas = {k: round(float(res.params.get(k, np.nan)), 3) for k in X_cols_dict}
    return {
        "label": label, "n": len(df),
        "alpha_ann": a * ANN,
        "t": t, "p": p,
        "betas": betas,
        "r2": float(res.rsquared),
    }


w("# P0-3 alpha 检验预研（收益法正交化，只读）")
w()
w("> 生成：脚本 analysis/alpha_verification_study_20261018.py 直出（禁人工转抄）｜"
  "数据源=10/16 全史引擎两腿一组合日收益 + index_daily 沪深300/中证500｜"
  "日聚簇防护=Newey-West HAC(5)｜只读零交易权，预研非结论")
w()
w(f"公共样本：{common[0]} ~ {common[-1]}，{len(common)} 个交易日")
w()

w("## 一、剥 β 截距检验（α 年化，HAC t 值）")
w()
w("| 回归 | n | α(年化) | HAC t | p | β | R² |")
w("|---|---|---|---|---|---|---|")

specs = [
    ("R_ml ~ R_bp_ep（剥价值腿后 ml 残差 α，ml 启用窗）", rm_m, {"R_bp_ep": rb_m}),
    ("R_bp_ep ~ R_ml（对称方向，ml 启用窗）", rb_m, {"R_ml": rm_m}),
    ("R_combo ~ hs300", rc, {"hs300": rh}),
    ("R_combo ~ hs300 + size(500-300)", rc, {"hs300": rh, "size": rs}),
    ("R_bp_ep ~ hs300 + size", rb, {"hs300": rh, "size": rs}),
    ("R_ml ~ hs300 + size（ml 启用窗）", rm.loc[common_ml], {"hs300": rh.loc[common_ml], "size": rs.loc[common_ml]}),
]
results = []
for label, y, xs in specs:
    r = nw_ols(y, xs, label)
    results.append(r)
    beta_str = ", ".join(f"{k}={v}" for k, v in r["betas"].items())
    w(f"| {label} | {r['n']} | {r['alpha_ann']*100:+.2f}% | {r['t']:+.2f} | {r['p']:.3f} | {beta_str} | {r['r2']:.2f} |")
w()
w("读数口径：α 年化=日截距×244；HAC t≥2 且 p<0.05 记为显著；R² 高说明解释腿覆盖大部分方差，α 读的是残差截距。")
w()

w("## 二、分年 α 稳定性（regime 依赖如实呈现）")
w()
w("### 2.1 R_ml ~ R_bp_ep 分年截距（年化，HAC t；ml 启用窗=2020 起，此前未启用记 N/A）")
w()
w("| 年 | n | α(年化) | t | 显著(≥2) |")
w("|---|---|---|---|---|")
years = sorted({d[:4] for d in common})
sig_years_ml = 0
tot_years_ml = 0
ml_start_year = ml_start[:4]
for y in years:
    if y < ml_start_year:
        w(f"| {y} | — | N/A（ml 未启用，净值平线） | — | — |")
        continue
    mask = [d for d in common if d[:4] == y]
    if len(mask) < 60:
        continue
    r = nw_ols(rm.loc[mask], {"R_bp_ep": rb.loc[mask]}, y)
    if not np.isfinite(r["t"]):
        w(f"| {y} | {r['n']} | N/A（零方差） | — | — |")
        continue
    sig = abs(r["t"]) >= 2 and r["alpha_ann"] > 0
    sig_years_ml += 1 if sig else 0
    tot_years_ml += 1
    w(f"| {y} | {r['n']} | {r['alpha_ann']*100:+.2f}% | {r['t']:+.2f} | {'是' if sig else '否'} |")
w()
w(f"ml 对价值腿的正显著年：{sig_years_ml}/{tot_years_ml}（仅计启用窗内年份）")
w()

w("### 2.2 前后半窗 α 方向一致性（P2 纪律；ml 系回归按启用窗切半）")
w()
half_m = len(common_ml) // 2
half = len(common) // 2
for label, y, xs in [
    ("R_ml ~ R_bp_ep（启用窗）", rm_m, {"R_bp_ep": rb_m}),
    ("R_combo ~ hs300+size", rc, {"hs300": rh, "size": rs}),
]:
    if xs is rb_m:
        r1 = nw_ols(y[:half_m], {k: v[:half_m] for k, v in xs.items()}, label + "前半")
        r2 = nw_ols(y[half_m:], {k: v[half_m:] for k, v in xs.items()}, label + "后半")
    else:
        r1 = nw_ols(y.iloc[:half], {k: v.iloc[:half] for k, v in xs.items()}, label + "前半")
        r2 = nw_ols(y.iloc[half:], {k: v.iloc[half:] for k, v in xs.items()}, label + "后半")
    same = (r1["alpha_ann"] > 0) == (r2["alpha_ann"] > 0)
    w(f"- {label}：前半 α={r1['alpha_ann']*100:+.2f}%(t={r1['t']:+.2f})｜后半 α={r2['alpha_ann']*100:+.2f}%(t={r2['t']:+.2f})｜方向{'一致✓' if same else '反转⚠️'}")
w()

w("## 三、限制与口径声明（反臆想）")
w()
w("1. **收益法 α ≠ 因子级正交化**：本预研在收益序列上剥 β，未做持仓/因子暴露对齐；因子级增量 IC 检验未建，属学习清单待点名项；")
w("2. **ml 腿含模型选择效应**：ml 模型 expanding 全窗月度重训，其历史日收益含「模型是被这同一历史选出来的」这一选择成分——收益法 α 对该成分不做剥离，读数是相对量级不是因果估计；")
w("3. **同一实现同一窗口**：两腿一组合出自同一引擎同一区间，非独立实现；")
w("4. **A池不重做**：画像 A池的统计显著性已由 10/11 FDR/BH+块bootstrap 工具在 13 臂族首用检验（在档），本预研仅覆盖两腿一组合；")
w("5. 本预研零交易权，不构成任何配置/接线依据；正负读数如实呈现，不做倾向性摘选。")
w()
w("**置信度**：中——回归机械事实高置信（HAC 稳健、分年+前后半窗双检），但对「真 alpha」的因子级因果推断不足（限制 1/2）。")

with open(OUT, "w", encoding="utf-8") as f:
    f.write("\n".join(L) + "\n")
print("\n".join(L))
print(f"\n[OK] {OUT}")
