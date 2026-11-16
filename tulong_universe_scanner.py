# -*- coding: utf-8 -*-
"""
tulong_universe_scanner.py — 屠龙多源股票池扫描器 v1.0
创建: 2026-08-18  错误闭环: 修复"涨停导向股票池漏掉吸筹未爆发型"盲区(日发精机案)

盲区定性: 前置过滤器偏差(股票池构建阶段只用涨停/新高事件做入选条件,
"资金持续吸筹但未爆发"型股票在第一段即被系统性排除, 后续验证再严也无效)。

修复架构(三池并行 + 统一闸门 + EM口径终审):
  池A 事件池(爆发基因): 东财涨停池/强势池/炸板池 — push2ex host 本地可用
  池B 资金池(吸筹基因): 同花顺个股资金流排行 即时+5日 — 10jqka host 本地可用
  终审(人工/MCP步骤, 不在本脚本): westock data_fund_flow 批量复核东财口径
        5日/10日主力净流入, 与THS口径同正才放行(双口径确认闸门)

用法:
  D:/workbuddyspace/Claw/.venv_ml/Scripts/python.exe D:/zcodesj/tulong_universe_scanner.py
  可选: --top-b 150 --max-pct 8 --price-cap 30 --min-turnover 2 --pos-cap 40

输出: 控制台摘要 + D:/zcodesj/reports/universe_scan_YYYYMMDD.csv
铁律: 本脚本只做池构建+基础闸门, 不产生买入指令; 候选须经四维人工/MCP验证。
"""
import argparse
import datetime as dt
import re
import sys
import urllib.request

import akshare as ak
import pandas as pd

REPORT_DIR = r"D:\zcodesj\reports"


# ---------- 数据源: 池A 事件池(东财push2ex) ----------
def fetch_pool_a(date_str: str) -> pd.DataFrame:
    rows = []
    sources = [
        ("涨停", lambda: ak.stock_zt_pool_em(date=date_str)),
        ("强势", lambda: ak.stock_zt_pool_strong_em()),
        ("炸板", lambda: ak.stock_zt_pool_zbgc_em(date=date_str)),
    ]
    for tag, fn in sources:
        try:
            df = fn()
            if df is None or df.empty:
                continue
            code_col = "代码" if "代码" in df.columns else None
            if code_col is None:
                continue
            sub = df[[code_col]].copy()
            sub.columns = ["code"]
            sub["code"] = sub["code"].astype(str).str.zfill(6)
            sub["pool_a"] = tag
            rows.append(sub.drop_duplicates("code"))
        except Exception as e:  # 单源失败不阻塞
            print(f"[WARN] 池A-{tag} 获取失败: {type(e).__name__} {str(e)[:60]}")
    if not rows:
        return pd.DataFrame(columns=["code", "pool_a"])
    out = pd.concat(rows).drop_duplicates("code")
    return out


# ---------- 数据源: 池B 资金池(同花顺) ----------
def _parse_amt(s):
    if not isinstance(s, str):
        return None
    s = s.replace(",", "")
    m = re.match(r"(-?[\d.]+)(亿|万)", s)
    if not m:
        return None
    return float(m.group(1)) * (1e8 if m.group(2) == "亿" else 1e4)


def fetch_pool_b(top_n: int) -> pd.DataFrame:
    rows = []
    for tag, symbol in [("即时", "即时"), ("5日", "5日排行")]:
        try:
            df = ak.stock_fund_flow_individual(symbol=symbol)
            amt_col = "净额" if "净额" in df.columns else "资金流入净额"
            amt_key = "净流入元"
            df[amt_key] = df[amt_col].map(_parse_amt)
            df["code"] = df["股票代码"].astype(str).str.strip().str.zfill(6)
            pos = df[df[amt_key] > 0].sort_values(amt_key, ascending=False).head(top_n)
            sub = pos[["code", amt_key]].copy()
            sub.columns = ["code", f"ths_{tag}_净流入"]
            sub["pool_b"] = f"THS{tag}"
            rows.append(sub.drop_duplicates("code"))
        except Exception as e:
            print(f"[WARN] 池B-{tag} 获取失败: {type(e).__name__} {str(e)[:60]}")
    if not rows:
        return pd.DataFrame(columns=["code", "pool_b"])
    out = pd.concat(rows)
    if len(rows) == 2:
        out = rows[0].merge(rows[1][["code", "ths_5日_净流入"]], on="code", how="outer")
        out["pool_b"] = out["pool_b"].fillna("THS5日")
    return out.drop_duplicates("code")


# ---------- 腾讯实时批量行情(补当日涨跌幅/换手/量比/均价) ----------
def fetch_tencent_quotes(codes):
    out = {}
    for i in range(0, len(codes), 60):
        batch = codes[i : i + 60]
        q = ",".join(
            ("sh" if c.startswith("6") else "bj" if c[0] in "48" else "sz") + c
            for c in batch
        )
        url = f"http://qt.gtimg.cn/q={q}"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
            text = urllib.request.urlopen(req, timeout=10).read().decode("gbk", "ignore")
        except Exception as e:
            print(f"[WARN] 腾讯行情批次失败: {e}")
            continue
        for m in re.finditer(r'v_(?:sh|sz|bj)(\d{6})="([^"]+)"', text):
            code, f = m.group(1), m.group(2).split("~")
            if len(f) < 50:
                continue
            try:
                price, prev_close = float(f[3]), float(f[4])
                vol_hand, amount_wan = float(f[6]), float(f[37])
                avg = amount_wan * 1e4 / (vol_hand * 100) if vol_hand else None
                pct = round((price / prev_close - 1) * 100, 2) if prev_close else None
                out[code] = {
                    "name": f[1],
                    "price": price,
                    "pct_today": pct,
                    "turnover": float(f[38]) if f[38] else None,
                    "volume_ratio": float(f[49]) if f[49] else None,
                    "avg_price": round(avg, 3) if avg else None,
                }
            except (ValueError, IndexError):
                continue
    return out


# ---------- 统一闸门 ----------
def apply_gates(df, args, q, preopen=False):
    df["name"] = df["code"].map(lambda c: q.get(c, {}).get("name"))
    df["price"] = df["code"].map(lambda c: q.get(c, {}).get("price"))
    df["pct_today"] = df["code"].map(lambda c: q.get(c, {}).get("pct_today"))
    df["turnover"] = df["code"].map(lambda c: q.get(c, {}).get("turnover"))
    df["volume_ratio"] = df["code"].map(lambda c: q.get(c, {}).get("volume_ratio"))
    df["avg_price"] = df["code"].map(lambda c: q.get(c, {}).get("avg_price"))
    df = df[df["name"].notna()].copy()

    def limit_up_pct(row):
        # 主板10% 创业板20% (近似: 按代码前缀)
        return 20.0 if row["code"][:3] in ("300", "301") else 10.0

    df["limit_pct"] = df.apply(limit_up_pct, axis=1)
    n0 = len(df)
    df = df[~df["name"].str.contains("ST", case=False, na=False)]
    df = df[~df["code"].str.startswith(("688", "8", "4", "9"))]  # 排除科创/北交/B股
    df = df[df["price"] <= args.price_cap]
    df = df[(df["pct_today"] > 0) & (df["pct_today"] <= args.max_pct)]  # 追高拦截
    df = df[df["pct_today"] < df["limit_pct"]]  # 未涨停(可买)
    if not preopen:
        # 盘中: 要求活跃度(换手/量比)与站上均价; 盘前竞价时段这两项无有效值, 跳过
        df = df[(df["turnover"].fillna(0) >= args.min_turnover) | (df["volume_ratio"].fillna(0) >= 2)]
        df = df[df["price"] >= df["avg_price"] * 0.995]  # 站上均价(容差0.5%)
    else:
        # 盘前竞价: 无连续成交, 不看换手/量比/均价, 保留竞价涨幅信息供盘前判断
        pass
    print(
        f"[闸门] 输入{n0} -> 产出{len(df)} "
        f"(剔除ST/科创北交/价格>{args.price_cap}/涨幅>{args.max_pct}%或≤0/已涨停"
        + ("" if preopen else "/死水/破均价")
        + ")"
    )
    return df


# ---------- 涨停日标记(算法争议区, 2026-08-18四样本泛化验证) ----------
# 结论: 窗口含涨停日时EM与THS资金流可方向相反(4/4样本: 日发精机/中石科技/
# 农发种业方向反, 罗牛山幅度差11倍), 涨停封板成交归类是两算法最大分歧场景。
VIPDOC = r"D:\anzhuang\tdx\vipdoc"


def flag_limit_days(codes):
    """读通达信vipdoc日线(32字节/记录, 价格*100), 标注近5交易日涨停/跌停天数"""
    import struct

    out = {}
    for code in codes:
        mkt = "sh" if code.startswith("6") else "bj" if code[0] in "48" else "sz"
        path = rf"{VIPDOC}\{mkt}\lday\{mkt}{code}.day"
        n = 0
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 6 * 32))  # 最近6条(算5个涨跌幅)
                buf = f.read(6 * 32)
            recs = [struct.unpack("<IIIIIfII", buf[i : i + 32]) for i in range(0, len(buf), 32)]
            closes = [(r[0], r[4] / 100) for r in recs]
            is_cyb = code[:3] in ("300", "301")
            thr = 19.8 if is_cyb else 9.8
            for i in range(1, len(closes)):
                if closes[i - 1][1] <= 0:
                    continue
                pct = (closes[i][1] / closes[i - 1][1] - 1) * 100
                if abs(pct) >= thr:
                    n += 1
        except OSError:
            n = 0
        out[code] = n
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--top-b", type=int, default=150)
    ap.add_argument("--max-pct", type=float, default=8.0)
    ap.add_argument("--price-cap", type=float, default=30.0)
    ap.add_argument("--min-turnover", type=float, default=2.0)
    ap.add_argument("--preopen", action="store_true", help="盘前竞价模式: 跳过换手/量比/均价闸门(09:15-09:30无连续成交)")
    args = ap.parse_args()

    # 自动识别盘前竞价时段(09:15~09:30): 无连续成交, 换手/量比/均价无有效值
    now = dt.datetime.now()
    preopen = args.preopen or (now.hour == 9 and 15 <= now.minute <= 30)

    date_str = dt.date.today().strftime("%Y%m%d")
    print(f"=== 屠龙多源股票池扫描 {date_str} ===")

    pa = fetch_pool_a(date_str)
    print(f"[池A] 事件池(涨停/强势/炸板): {len(pa)} 只")
    pb = fetch_pool_b(args.top_b)
    print(f"[池B] 资金池(THS即时+5日净流入Top{args.top_b}): {len(pb)} 只")

    merged = (
        pa.merge(pb, on="code", how="outer")
        if not pb.empty
        else pa.assign(pool_b=None)
    )
    if "pool_a" not in merged.columns:
        merged["pool_a"] = None
    if "pool_b" not in merged.columns:
        merged["pool_b"] = None
    merged["双池命中"] = merged["pool_a"].notna() & merged["pool_b"].notna()

    q = fetch_tencent_quotes(merged["code"].tolist())
    result = apply_gates(merged, args, q, preopen=preopen)
    if preopen:
        print("[盘前竞价模式] 已跳过换手/量比/均价闸门(此时段无连续成交, 换手/量比/均价无有效值)")

    # 涨停日算法争议区标记: 资金流窗口含涨停/跌停日的候选, 双口径验证必须拆逐日
    ld = flag_limit_days(result["code"].tolist())
    result["近5日停板天数"] = result["code"].map(ld).fillna(0).astype(int)
    warn = result[result["近5日停板天数"] > 0]
    if len(warn):
        print(f"\n[⚠️算法争议区] 以下{len(warn)}只候选近5日含涨停/跌停日, 资金流双口径分歧概率高,")
        print("  严禁直接引用5日净流入结论, 必须拆逐日双口径核验:")
        for _, r in warn.iterrows():
            print(
                f"    {r['code']} {r['name']} 停板{r['近5日停板天数']}天 "
                f"(THS5日净流入={r.get('ths_5日_净流入')})"
            )

    # 2026-08-24 P1修复: THS 5日榜单源失败时无 ths_5日_净流入 列，直接 sort 必 KeyError
    # 打崩整个扫描器（违背文件头"单源失败不阻塞"承诺）；缺列补 None 再排序
    if "ths_5日_净流入" not in result.columns:
        result["ths_5日_净流入"] = None
    if "ths_即时_净流入" not in result.columns:
        result["ths_即时_净流入"] = None
    result = result.sort_values(
        ["双池命中", "ths_5日_净流入"], ascending=[False, False], na_position="last"
    )

    cols = [
        "code", "name", "price", "pct_today", "volume_ratio", "turnover",
        "avg_price", "近5日停板天数", "pool_a", "pool_b",
        "ths_即时_净流入", "ths_5日_净流入", "双池命中",
    ]
    cols = [c for c in cols if c in result.columns]
    out = result[cols].head(40)
    print("\n=== 候选清单(前40, 须经EM口径终审+四维人工验证) ===")
    print(out.to_string(index=False))

    path = rf"{REPORT_DIR}\universe_scan_{date_str}.csv"
    result.to_csv(path, index=False, encoding="utf-8-sig")
    print(f"\n[落盘] {path} 共{len(result)}条")
    print("[SOP] 终审步骤: westock data_fund_flow 批量复核东财口径5日/10日主力净流入,")
    print("      THS与EM双口径同正才放行; 再走四维验证(位置/量价/板块/流动性)。")


if __name__ == "__main__":
    sys.exit(main())
