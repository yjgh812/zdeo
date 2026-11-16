#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
portrait_shadow_v1.py — AI画像影子/观察池层 Phase 1（2026-09-05 主人四裁决落地）
================================================================================
定位（PROJECT_BOUNDARY §3 已登记）：
  画像层 = bp+ep/ml 之上的影子评分与观察池层，**非独立选股 owner**。
  - 名单内对照：对当日买入白名单（buy_whitelist_YYYYMMDD.csv）计算画像特征与影子分，仅供复盘对照。
  - 观察池：合规宇宙画像 TopN 输出，**名单外=不构成买入建议**；若需买入必须主人逐笔
    特批（source=manual_confirmed）走执行链双层防线闸门，本层不提供任何下单能力。
  - 排除维度（终审前禁入特征集）：筹码面（CHIP-MANUAL-3GATE v1.1 已注册未执行）、
    情绪面（PREREG_SENTIMENT_FACTOR_P3 终审 2027-02-28）；北向（8/15 退役）。
  - Phase 1 = 纯规则化评分，**零模型训练**；LightGBM/GRU/CNN 等模型化须 wfa_gate
    新指纹预注册显式立项后另行裁决。

数据合同（全部只读）：
  - 价格/量：dragon_quant_qfq.db.stock_daily_meta_qfq（回测唯一价格源，前复权）
  - 财务：factor_pit.db.stock_factors_fundamental_pit_v2——单趟多因子读取，逐因子
    复刻 multifactor/factor_loader.load_pit_factors 契约（v2 tail 前视排除 WHERE、
    per-factor dropna 回退、ep/bp 价格类按当日收盘重算），等价性由
    tests/test_portrait_shadow.py T11 对 load_pit_factors 逐因子断言守护
    （2026-09-05 性能轮：3 次独立调用 20.1s → 单趟 ~5s）
  - 元数据：dragon_quant.db.stock_info（industry/list_date/is_st/status）

宇宙过滤（对齐生产规则，非外部计划书≤20元口径）：
  沪深主板+创业板前缀闸(60/00/30) + 非ST + status=normal + 上市>60自然日 +
  当日有成交(未停牌) + 收盘≤30元 + 20日均成交额≥2000万元。

特征（方向约定仅为展示口径，**未过IC/三闸，禁作选股信号直接消费**）：
  技术面: ret20/ret60(正) ma_align 0-3(正) bias20(负,高乖离=追高风险)
          vol_ratio 当日量/前5日均量(正,关注度) vola20(负,低波动加分)
  基本面: bp/ep/roe PIT as-of T(正,价值+质量常规口径)
  影子分 = 0.5*tech + 0.5*fund（截面百分位秩均值；权重=固定展示参数非拟合产物）。

用法：
  python portrait_shadow_v1.py                 # 默认 qfq 最新交易日
  python portrait_shadow_v1.py --date 20260904
  python portrait_shadow_v1.py --selfcheck     # 结构自检（隔离断言）
退出码：0 成功；2 结构化失败（数据不新鲜/宇宙异常/文件缺失/名单合同违约）。
"""
import argparse
import json
import re
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

_ROOT = Path(r"D:\zcodesj")
sys.path.insert(0, str(_ROOT))
sys.path.insert(0, str(_ROOT / "multifactor"))  # factor_loader 依赖同级 config.py
from stock_name_lookup import verify_pair  # noqa: E402
from multifactor.factor_loader import get_stock_meta  # noqa: E402

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
WHITELIST_DIR = _ROOT / "reports"
REPORT_DIR = _ROOT / "reports"
DATA_DIR = _ROOT / "data"

# 宇宙过滤参数（展示口径，变更须同步 PROJECT_BOUNDARY §3 画像条目）
PREFIX_OK = ("60", "00", "30")
MAX_PRICE = 30.0
MIN_AMT20 = 2e7
MIN_LIST_DAYS = 60
POOL_TOP_N = 30
# 特征权重（固定展示参数，非拟合）
TECH_FEATURES = ["ret20", "ret60", "ma_align", "bias20", "vol_ratio", "vola20"]
TECH_NEG = {"bias20", "vola20"}
FUND_FEATURES = ["bp", "ep", "roe"]
W_TECH, W_FUND = 0.5, 0.5
KLINE_DAYS = 130          # 技术特征窗口(交易日)
PIT_LOOKBACK_DAYS = 400   # PIT asof 回看自然日窗(报告季度回退上界)
# 与 multifactor/factor_loader._PIT_PRICE_BASED 同契约：比值类因子按当日收盘重算
_PIT_PRICE_BASED = {"ep", "bp", "sp"}
# 白名单文件名合同（.v1/.v2 等防覆盖备份不入合同）
_WL_NAME_RE = re.compile(r"buy_whitelist_\d{8}\.csv")
# 隔离自检：影子层源码禁止出现任何下单/执行通道引用
# （字符串分段拼接防止 token 出现在本文件源码中被自身命中）
_FORBIDDEN_TOKENS = ("trade_" "executor", "portfolio_" "paper", "pre_trade_" "check",
                     "urllib." "request", "requests" ".")

_REPORT_COLS = ["rank", "code", "name", "industry", "close", "ret20", "ret60",
                "ma_align", "bias20", "vol_ratio", "vola20", "amt20",
                "bp", "ep", "roe", "tech_score", "fund_score", "total_score"]


def _fail(msg: str) -> None:
    print(f"[PORTRAIT-FAIL] {msg}")
    raise SystemExit(2)


def load_qfq_window(date: str | None) -> tuple[str, pd.DataFrame]:
    """读 T 日及之前 KLINE_DAYS 个交易日的全市场前复权日K。返回 (实际T, kline df)。"""
    with sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True) as c:
        t_max = str(c.execute("SELECT max(date) FROM stock_daily_meta_qfq").fetchone()[0])
        t = t_max if date is None else date
        dates = [str(r[0]) for r in c.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq "
            "WHERE date <= ? ORDER BY date DESC LIMIT ?", (t, KLINE_DAYS))]
        if not dates or dates[0] != t:
            _fail(f"qfq 库无 {t} 数据（最新={t_max}）；缺少原始数据，无法得出确定性判断")
        k = pd.read_sql(
            "SELECT code, date, close, volume, amount FROM stock_daily_meta_qfq "
            "WHERE date >= ? AND date <= ? ORDER BY code, date",
            c, params=(dates[-1], t))
    k["date"] = k["date"].astype(str)
    return t, k


def build_technicals(t: str, k: pd.DataFrame) -> pd.DataFrame:
    """逐股技术特征（只用 ≤T 数据，无任何前向引用）。"""
    rows = []
    for code, g in k.groupby("code", sort=True):
        g = g.sort_values("date").reset_index(drop=True)
        if g["date"].iloc[-1] != t or len(g) < 61:
            continue  # 当日停牌或历史不足：不进池
        c = g["close"].astype(float)
        v = g["volume"].astype(float)
        amt = g["amount"].astype(float)
        ma5, ma10, ma20 = c.tail(5).mean(), c.tail(10).mean(), c.tail(20).mean()
        ma60 = c.tail(60).mean()
        align = int(ma5 > ma10) + int(ma10 > ma20) + int(ma20 > ma60)
        rows.append({
            "code": code,
            "close": float(c.iloc[-1]),
            "ret20": c.iloc[-1] / c.iloc[-21] - 1.0,
            "ret60": c.iloc[-1] / c.iloc[-61] - 1.0,
            "ma_align": align,
            "bias20": c.iloc[-1] / ma20 - 1.0,
            "vol_ratio": float(v.iloc[-1]) / max(float(v.iloc[-6:-1].mean()), 1.0),
            "vola20": float(c.pct_change().tail(20).std()),
            "amt20": float(amt.tail(20).mean()),
        })
    return pd.DataFrame(rows)


def _num_col(s: pd.Series) -> pd.Series:
    """yymmdd 变体统一为 8 位整数列。"""
    return s.astype(str).str[:10].str.replace("-", "", regex=False).astype(int)


def _load_pit_asof(t: str) -> pd.DataFrame:
    """单趟多因子 PIT as-of T。逐因子语义与 factor_loader.load_pit_factors 完全一致：
    v2 WHERE 排除 tail 前视 / per-factor dropna 后 merge_asof backward（可回退到更早
    的已披露报告）/ 价格类因子 ref_close/close 重算。等价性由测试 T11 守护。"""
    import os
    if not (os.path.exists(PIT_DB) and os.path.exists(QFQ_DB)):
        _fail(f"PIT 完整读取缺库: {PIT_DB} / {QFQ_DB}")
    start_n = (datetime.strptime(t, "%Y%m%d") - timedelta(days=PIT_LOOKBACK_DAYS)).toordinal()
    start = datetime.fromordinal(start_n).strftime("%Y%m%d")
    cols = ", ".join(f"({f}) AS {f}" for f in FUND_FEATURES)
    with sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True) as cp, \
            sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True) as cq:
        pit_all = pd.read_sql(
            f"SELECT code, pit_avail_date, {cols}, ref_close "
            f"FROM stock_factors_fundamental_pit_v2 "
            f"WHERE NOT (snap_rule='tail' AND ref_date < pit_avail_date)", cp)
        daily = pd.read_sql(
            "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= ? AND date <= ?",
            cq, params=(int(start), int(t)))
    if daily.empty:
        return pd.DataFrame(columns=["code"] + FUND_FEATURES)
    daily["date_num"] = _num_col(daily["date"])
    daily = daily.sort_values("date_num")
    pit_all["avail_num"] = _num_col(pit_all["pit_avail_date"])
    t_num = int(t)

    out = None
    for f in FUND_FEATURES:
        sub = pit_all.dropna(subset=[f, "ref_close"])[
            ["code", "avail_num", f, "ref_close"]].sort_values("avail_num")
        m = pd.merge_asof(daily, sub, left_on="date_num", right_on="avail_num",
                          by="code", direction="backward")
        m = m[m["date_num"] == t_num]
        v = m[f]
        if f in _PIT_PRICE_BASED:
            v = v * m["ref_close"] / m["close"]
        part = pd.DataFrame({"code": m["code"].values, f: v.values})
        out = part if out is None else out.merge(part, on="code", how="outer")
    return out if out is not None else pd.DataFrame(columns=["code"] + FUND_FEATURES)


def load_universe(t: str) -> pd.DataFrame:
    """合规宇宙元数据（前缀闸/ST/上市时长/status）。"""
    meta = get_stock_meta()
    meta["code"] = meta["code"].astype(str)
    cut = (datetime.strptime(t, "%Y%m%d") - timedelta(days=MIN_LIST_DAYS)).strftime("%Y%m%d")
    u = meta[
        meta["code"].str.startswith(PREFIX_OK)
        & (meta["is_st"].astype(str) != "1")
        & ~meta["name"].fillna("").str.contains("ST", na=False)
        & (meta["status"].astype(str) == "normal")
        & (meta["list_date"].astype(str) <= cut)
    ].copy()
    # 主键检查放在过滤后：全库存在 status=None 的退市旧实体重复码（9/5 实测 19 条，
    # 全部非 normal 且当日无成交，对宇宙零影响），过滤后再重复才是真违约
    if u["code"].duplicated().any():
        _fail(f"过滤后宇宙仍存在重复代码 {int(u['code'].duplicated().sum())} 条"
              f"（{sorted(u[u['code'].duplicated()]['code'].unique())[:5]}…）："
              f"merge 将膨胀，fail-closed")
    if not (2000 <= len(u) <= 6000):
        _fail(f"宇宙过滤后 {len(u)} 只，超出合理区间 [2000,6000]，疑似元数据异常，fail-closed")
    return u[["code", "name", "industry", "list_date"]]


def rank_score(df: pd.DataFrame, feats: list[str], neg: set[str]) -> pd.Series:
    """截面百分位秩合成（负向特征取 1-rank）。任一特征缺失则该维不计分（NaN）。"""
    r = pd.DataFrame(index=df.index)
    for f in feats:
        s = df[f].rank(pct=True)
        r[f] = (1.0 - s) if f in neg else s
    return r.mean(axis=1).where(r.notna().all(axis=1))


def load_whitelist(t: str) -> tuple[pd.DataFrame, str]:
    """≤T 的最近一份买入白名单（文件名合同 buy_whitelist_YYYYMMDD.csv，
    .v1/.v2 防覆盖备份不入合同）；重复代码=名单生成器违约，fail-closed。"""
    picked = None
    for f in sorted(WHITELIST_DIR.glob("buy_whitelist_*.csv"), reverse=True):
        if not _WL_NAME_RE.fullmatch(f.name):
            continue  # 备份/变体后缀不入合同
        if f.stem.split("_")[-1] <= t:
            picked = f
            break
    if picked is None:
        _fail(f"reports/ 无日期≤{t} 且满足文件名合同的 buy_whitelist_*.csv：名单层数据缺失，"
              f"无法得出确定性判断")
    wl = pd.read_csv(picked, encoding="utf-8-sig", dtype={"code": str})
    if "code" not in wl.columns:
        _fail(f"白名单 {picked.name} 缺少 code 列（schema 漂移）：无法消费，fail-closed")
    wl["code"] = wl["code"].astype(str).str.strip()
    if (wl["code"] == "").any():
        _fail(f"白名单 {picked.name} 存在空代码行 {int((wl['code'] == '').sum())} 个："
              f"名单生成器疑似异常，fail-closed")
    dup = wl[wl["code"].duplicated(keep=False)]
    if len(dup):
        _fail(f"白名单 {picked.name} 存在重复代码 {len(dup)} 行（{sorted(dup['code'].unique())[:5]}…）："
              f"名单生成器疑似异常，fail-closed")
    return wl, picked.name


def selfcheck() -> None:
    src = Path(__file__).read_text(encoding="utf-8")
    hits = [tok for tok in _FORBIDDEN_TOKENS if tok in src]
    if hits:
        print(f"[SELFCheck-FAIL] 影子层源码出现执行链引用: {hits}")
        raise SystemExit(2)
    print("[SELFCheck-PASS] 零下单/执行通道引用；影子层隔离成立")


def _md_table(d: pd.DataFrame) -> str:
    if d.empty:
        return "（无）\n"
    show = d[_REPORT_COLS].copy()
    for col in ("name", "industry"):
        show[col] = show[col].fillna("—").astype(str).replace({"nan": "—", "": "—"})
    for c in ("ret20", "ret60", "bias20"):
        show[c] = (show[c] * 100).map(lambda x: f"{x:+.2f}%")
    show["amt20"] = (show["amt20"] / 1e8).map(lambda x: f"{x:.2f}亿")
    for c in ("bp", "ep", "roe", "vola20", "vol_ratio"):
        show[c] = show[c].map(lambda x: f"{x:.3f}")
    for c in ("tech_score", "fund_score", "total_score"):
        show[c] = show[c].map(lambda x: f"{x:.3f}")
    lines = ["| " + " | ".join(_REPORT_COLS) + " |",
             "|" + "---|" * len(_REPORT_COLS)]
    for _, row in show.iterrows():
        lines.append("| " + " | ".join(str(row[c]) for c in _REPORT_COLS) + " |")
    return "\n".join(lines) + "\n"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="YYYYMMDD，默认 qfq 最新交易日")
    ap.add_argument("--selfcheck", action="store_true")
    args = ap.parse_args()
    if args.selfcheck:
        selfcheck()
        return

    t, k = load_qfq_window(args.date)
    tech = build_technicals(t, k)
    if tech.empty:
        _fail(f"{t} 无可用技术特征（停牌/数据不足），不做主观推演")
    tech = tech[(tech["close"] <= MAX_PRICE) & (tech["amt20"] >= MIN_AMT20)]
    n_tech_universe = len(tech)

    uni = load_universe(t)
    df = tech.merge(uni, on="code", how="inner")
    df = df.merge(_load_pit_asof(t), on="code", how="left")

    df["tech_score"] = rank_score(df, TECH_FEATURES, TECH_NEG)
    df["fund_score"] = rank_score(df, FUND_FEATURES, set())
    df["total_score"] = (W_TECH * df["tech_score"] + W_FUND * df["fund_score"]).where(
        df["tech_score"].notna() & df["fund_score"].notna())
    df = df.dropna(subset=["total_score"]).sort_values("total_score", ascending=False)
    df["rank"] = range(1, len(df) + 1)
    if len(df) < 500:
        _fail(f"可评分样本仅 {len(df)}，PIT/技术覆盖异常，fail-closed")

    wl, wl_file = load_whitelist(t)
    wl_date = wl_file.split("_")[-1].split(".")[0]
    wl_age_days = (datetime.strptime(t, "%Y%m%d") - datetime.strptime(wl_date, "%Y%m%d")).days
    wl_codes = set(wl["code"])
    in_list = df[df["code"].isin(wl_codes)]
    pool = df[~df["code"].isin(wl_codes)].head(POOL_TOP_N).copy()

    # 名称一致性复核（宪法：出现 ❌ 禁发布）
    name_bad = []
    for _, r in pd.concat([pool, in_list]).iterrows():
        ok, actual = verify_pair(str(r["code"]), str(r["name"]))
        if not ok:
            name_bad.append(f"{r['code']}: 报告名={r['name']} / 库名={actual}")
    if name_bad:
        print("❌ 名称校验失败，禁止发布：\n" + "\n".join(name_bad))
        raise SystemExit(2)

    stamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    out_rows = df[_REPORT_COLS]

    # ---- 结构化产物 ----
    payload = {
        "asof": t, "generated_at": stamp, "whitelist_file": wl_file,
        "universe_scored": int(len(df)),
        "tech_universe": int(n_tech_universe),
        "whitelist_in_portrait": int(len(in_list)),
        "whitelist_total": int(len(wl)),
        "whitelist_age_days": int(wl_age_days),
        "excluded_dimensions": {
            "chip": "CHIP-MANUAL-3GATE v1.1 已注册未执行，终审前禁入",
            "sentiment": "PREREG_SENTIMENT_FACTOR_P3 终审 2027-02-28",
            "northbound": "8/15 主人裁决全线退役",
            "model": "Phase1 纯规则零训练；模型化须 wfa_gate 新指纹显式立项",
        },
        "whitelist_portrait": in_list.to_dict("records"),
        "observation_pool": pool.to_dict("records"),
    }
    json_path = DATA_DIR / f"portrait_shadow_{t}.json"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=float),
                         encoding="utf-8")
    csv_path = DATA_DIR / f"portrait_shadow_pool_{t}.csv"
    pool.to_csv(csv_path, index=False, encoding="utf-8-sig")

    # ---- 人读报告 ----
    md = []
    md.append(f"# PORTRAIT_SHADOW_{t} — AI画像影子/观察池层 Phase 1\n")
    md.append(f"> 生成 {stamp} ｜ 影子层**无任何下单能力**：本报告不构成买入建议；"
              f"观察池标的均为**名单外**，买入须主人逐笔特批（source=manual_confirmed）"
              f"并走执行链双层防线闸门。\n")
    md.append(f"> 排除维度：筹码面（等 CHIP-3GATE v1.1 终审）/ 情绪面（等 P3 终审 2027-02-28）"
              f"/ 北向（退役）/ 模型化（GRU/CNN 等须新指纹显式立项）。"
              f"影子分为展示口径，**未过 IC/三闸，禁作选股信号直接消费**。\n")
    md.append(f"\n## 0. 口径\n- asof={t}（qfq 前复权收盘）；基本面=stock_factors_fundamental_pit_v2 "
              f"as-of {t}（法定披露滞后，PIT 口径，单趟读取与 load_pit_factors 等价）\n"
              f"- 宇宙：前缀闸60/00/30 + 非ST + 上市>60自然日 + 当日有成交 + 收盘≤{MAX_PRICE:.0f}元 "
              f"+ 20日均额≥{MIN_AMT20/1e4:.0f}万元 → 技术宇宙 **{n_tech_universe}** 只，"
              f"三维基本面齐备可评分 **{len(df)}** 只（覆盖率 {len(df)/max(n_tech_universe,1)*100:.1f}%）\n"
              f"- 名单文件：reports/{wl_file}（{len(wl)} 行，含 tier，名单龄 {wl_age_days} 天"
              f"{'⚠️超5天·标注陈旧' if wl_age_days > 5 else ''}）\n")
    md.append("\n## 1. 名单内画像对照（仅供复盘对照，不改变生产名单序）\n")
    md.append(f"名单 {len(wl)} 只中 {len(in_list)} 只进入可评分宇宙"
              f"（其余为停牌/历史不足/超价/流动性过滤，如实留痕）。\n\n")
    md.append(_md_table(in_list))
    md.append(f"\n## 2. 观察池 Top{POOL_TOP_N}（**名单外·须特批**，按影子分降序；rank 为全宇宙名次）\n")
    md.append(_md_table(pool))
    md.append("\n## 3. 特征定义与方向约定（展示口径，非 alpha）\n"
              "| 特征 | 定义 | 方向 |\n|---|---|---|\n"
              "| ret20/ret60 | T/T-20、T/T-60 前复权收盘涨幅 | 正 |\n"
              "| ma_align | MA5>MA10>MA20>MA60 逐对成立数(0-3) | 正 |\n"
              "| bias20 | 收盘/MA20-1 | 负（高乖离=追高风险） |\n"
              "| vol_ratio | 当日量/前5日均量 | 正（关注度） |\n"
              "| vola20 | 20日日收益标准差 | 负（低波动加分） |\n"
              "| bp/ep/roe | PIT as-of T（价格类按当日收盘重算） | 正 |\n"
              "\n影子分 = 0.5×tech(6特征秩均值) + 0.5×fund(3特征秩均值)；"
              "权重为固定展示参数，非拟合产物。\n")
    md.append("\n## 4. 边界登记\n- 本层 owner=portrait_shadow_v1.py（D:\\zcodesj）；"
              "调度=直跑制定案（2026-09-05 主人裁决不挂定时任务，按需直跑）。\n"
              f"- 结构化产物：data/portrait_shadow_{t}.json + data/portrait_shadow_pool_{t}.csv\n"
              "- 【预判，存在不确定性】观察池方向约定未经 IC 检验，反向风险=动量/价值在当前 "
              "regime 下可能同向失效；消费方式仅限复盘对照与特批参考。\n")
    report_path = REPORT_DIR / f"PORTRAIT_SHADOW_{t}.md"
    report_path.write_text("\n".join(md), encoding="utf-8")

    print(f"[PORTRAIT-OK] asof={t} 技术宇宙={n_tech_universe} 可评分={len(df)} "
          f"名单对照={len(in_list)}/{len(wl)} 观察池={len(pool)}")
    print(f"  report={report_path}")
    print(f"  json  ={json_path}")
    print(f"  csv   ={csv_path}")


if __name__ == "__main__":
    main()
