# -*- coding: utf-8 -*-
"""
smc_signal_daily.py — S2×ROE>10% SSL 流动性扫荡每日信号扫描（阶段1 正式信号源）

用途：用 smartmoneyconcepts 严格定义（流动性池 + 扫荡 + displacement 确认），
      叠加 PIT ROE>10% 基本面过滤，扫描全市场最新交易日的做多信号。

策略定位（阶段0全部过闸，见 reports/SMC_LAB_20260823.md 四之七节）：
  - S2×ROE>10% 叠加策略：Sharpe 1.56 / 年化 53.9% / PBO 0.1143 / DSR 0.990 / 与bp_ep相关 0.137
  - 逻辑：高 ROE 好公司被 SSL 扫荡（超跌洗盘）+ displacement 确认后修复快
  - admit 实验源：smc_s2_roe（G12 降半仓 ×0.5，灰度 3 个月）

用法:
  python smc_signal_daily.py               # 扫描最新交易日
  python smc_signal_daily.py --date 20260821
  python smc_signal_daily.py --min-ret20 -0.3   # 只输出扫荡前20日动量<-30%（深跌超跌优先）
依赖: smartmoneyconcepts(已装0.0.27), qfq库, stock_name_lookup
"""
import sqlite3, argparse, sys, time, warnings
from pathlib import Path
import numpy as np, pandas as pd
from smartmoneyconcepts import smc

sys.path.insert(0, r"D:\zcodesj")
from stock_name_lookup import require_name
warnings.filterwarnings("ignore")

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
PIT_DB = r"D:\zcodesj\factor_pit.db"
OUT = Path(r"D:\zcodesj\reports")
SWING = 16        # 与阶段0一致（swing_length=16, 前后8根）
RANGE_PCT = 0.01  # 流动性池范围 1%
ROE_THR = 0.10     # ROE>10% 基本面门槛（阶段0过闸值）


def load_data():
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    df = pd.read_sql_query(
        "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq WHERE date>='20180101' ORDER BY code,date",
        con)
    con.close()
    df["date"] = df["date"].astype(str)
    df["ret1"] = df["change_pct"] / 100.0
    return df


def load_pit_roe():
    """加载 PIT ROE 因子，返回按 code 分组的 (pit_int 数组, roe 数组) 字典。"""
    con = sqlite3.connect(f"file:{PIT_DB}?mode=ro", uri=True)
    pit = pd.read_sql_query(
        "SELECT code,pit_avail_date,roe FROM stock_factors_fundamental_pit_v2 WHERE pit_avail_date>0", con)
    con.close()
    pit["pit_int"] = pit["pit_avail_date"].astype(int)
    pit = pit.sort_values(["code", "pit_int"]).reset_index(drop=True)
    return {c: (g["pit_int"].values, g["roe"].values) for c, g in pit.groupby("code")}


def get_pit_roe(pit_map, code, date_str):
    """PIT 无前视取 ROE：返回 date_str 时点可看到的最新 ROE，无则 NaN。"""
    g = pit_map.get(str(code))
    if g is None:
        return np.nan
    arr, vals = g
    idx = np.searchsorted(arr, int(date_str), side="right") - 1
    if idx < 0:
        return np.nan
    return vals[idx]


def find_ssl_sweeps(df, target_date=None):
    """对每只股票算 swing+liquidity，收集 SSL 扫荡事件（Liquidity=-1 且 Swept 有效）。

    8/23 升级（学"别人怎么做"落地）：加 displacement 确认列。
    displacement 确认 = 扫荡后 1-3 日内出现放量阳线反包
      （close>流动性水平 且 volume>前5日均量×1.2）→ 标 confirmed_date；
    实证（smc_s2_displacement.py）：带确认入场组合 Sharpe -0.09→+0.25、月均转正，更优形态。
    """
    rows = []
    for code, o in df.groupby("code"):
        o = o.sort_values("date").reset_index(drop=True)
        if len(o) < 60:
            continue
        ohlc = o[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
        try:
            shl = smc.swing_highs_lows(ohlc, swing_length=SWING)
            liq = smc.liquidity(ohlc, shl, range_percent=RANGE_PCT)
        except Exception:
            continue
        vol5 = ohlc["volume"].rolling(5).mean()
        for i in np.where(liq["Liquidity"].values == -1)[0]:
            swept = int(liq["Swept"].iloc[i]) if not np.isnan(liq["Swept"].iloc[i]) else 0
            if swept > 0 and swept < len(o):
                lvl = liq["Level"].iloc[i]
                sweep_low = ohlc["low"].iloc[swept]
                depth_pct = (sweep_low / lvl - 1) * 100 if lvl else np.nan
                close_back = bool(ohlc["close"].iloc[swept] >= lvl) if lvl else False
                r20 = np.nan
                if swept >= 21:
                    r20 = o["close"].iloc[swept] / o["close"].iloc[swept - 20] - 1
                # displacement 确认：扫荡后 1-3 日内放量阳线反包
                confirmed_date = None
                for j in range(swept + 1, min(swept + 4, len(o))):
                    if (ohlc["close"].iloc[j] > lvl and
                            ohlc["volume"].iloc[j] > vol5.iloc[j] * 1.2):
                        confirmed_date = o["date"].iloc[j]
                        break
                rows.append({
                    "code": code,
                    "sweep_date": o["date"].iloc[swept],
                    "sweep_level": lvl,
                    "sweep_low": sweep_low,
                    "depth_pct": depth_pct,
                    "close_back": close_back,
                    "confirmed_date": confirmed_date,   # None=未确认; 日期=已触发 displacement
                    "close": o["close"].iloc[swept],
                    "ret1": o["ret1"].iloc[swept],
                    "ret20": r20,
                })
    return pd.DataFrame(rows)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None, help="目标交易日YYYYMMDD，默认取数据最新")
    ap.add_argument("--min-ret20", type=float, default=None, help="只输出扫荡前20日动量<=此值(如-0.3=深跌超跌)")
    ap.add_argument("--confirmed-only", action="store_true", help="只输出已触发 displacement 确认的（更优形态）")
    ap.add_argument("--roe-thr", type=float, default=ROE_THR, help=f"ROE 门槛（默认{ROE_THR}，阶段0过闸值）")
    ap.add_argument("--strategy", action="store_true", help="输出策略级清单（S2×ROE>thr，source=smc_s2_roe，供白名单接入）")
    args = ap.parse_args()

    t0 = time.time()
    print("读取 qfq 数据...")
    df = load_data()
    target = args.date or df["date"].max()
    print(f"数据到 {df['date'].max()}, 目标 {target}, 扫描全市场...")

    ev = find_ssl_sweeps(df)
    # 只保留 target 当日的扫荡（Swept 落在 target）
    sig = ev[ev["sweep_date"] == target].copy()
    if args.min_ret20 is not None:
        sig = sig[sig["ret20"] <= args.min_ret20]
    if args.confirmed_only:
        sig = sig[sig["confirmed_date"].notna()]
    print(f"全市场 SSL 扫荡事件 {len(ev)}，{target} 当日信号 {len(sig)}，耗时 {time.time()-t0:.0f}s")

    # 策略模式：加 PIT ROE>thr 过滤，输出策略级清单
    if args.strategy:
        print(f"加载 PIT ROE 因子（门槛 >{args.roe_thr*100:.0f}%）...")
        pit_map = load_pit_roe()
        sig["roe"] = sig.apply(lambda r: get_pit_roe(pit_map, r["code"], r["sweep_date"]), axis=1)
        before = len(sig)
        sig = sig[sig["roe"] > args.roe_thr].copy()
        print(f"ROE>{args.roe_thr*100:.0f}% 过滤: {before}→{len(sig)} 只")
        if sig.empty:
            print(f"[{target}] 无 S2×ROE 策略信号（扫荡有但 ROE 不达标）")
            return 0

    if sig.empty:
        print(f"[{target}] 无 S2 SSL 扫荡信号")
        return 0

    # 名称铁律
    sig["name"] = sig["code"].map(lambda c: require_name(c))

    # 策略模式：输出白名单可读的策略清单
    if args.strategy:
        sig["source"] = "smc_s2_roe"
        sig = sig[["code", "name", "source", "sweep_date", "confirmed_date", "roe",
                   "depth_pct", "close_back", "close", "ret20"]]
        sig = sig.sort_values(["confirmed_date", "ret20"], ascending=[False, True], na_position="last")
        out_csv = OUT / f"smc_s2_roe_signal_{target}.csv"
        sig.to_csv(out_csv, index=False, encoding="utf-8-sig")
        print(f"\n== S2×ROE>10% 策略信号 ==\n已输出 {out_csv}  ({len(sig)} 只, source=smc_s2_roe)")
        print(sig.to_string(index=False))
        return 0

    # 研究模式输出（全部扫荡信号）
    sig = sig[["code", "name", "sweep_date", "confirmed_date", "sweep_level", "sweep_low",
               "depth_pct", "close_back", "close", "ret1", "ret20"]]
    # 已确认优先，同状态内按 ret20 升序（深跌优先）
    sig["_conf"] = sig["confirmed_date"].notna().astype(int)
    sig = sig.sort_values(["_conf", "ret20"], ascending=[False, True]).drop(columns="_conf")
    n_conf = sig["confirmed_date"].notna().sum()
    out_csv = OUT / f"smc_ssl_signal_{target}.csv"
    sig.to_csv(out_csv, index=False, encoding="utf-8-sig")
    print(f"\n已输出 {out_csv}  ({len(sig)} 只, 其中 displacement 已确认 {n_conf} 只)")
    print(sig.to_string(index=False))

    # 已确认优先摘要
    conf = sig[sig["confirmed_date"].notna()].head(15)
    if not conf.empty:
        print("\n== displacement 已确认优先（实证更优形态）==")
        print(conf[["code", "name", "sweep_date", "confirmed_date", "depth_pct", "close", "ret20"]].to_string(index=False))
    else:
        print("\n== 深跌超跌优先（ret20 最低 15）==")
        print(sig.head(15)[["code", "name", "sweep_date", "depth_pct", "close_back", "close", "ret20"]].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
