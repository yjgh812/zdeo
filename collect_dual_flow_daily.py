# -*- coding: utf-8 -*-
"""collect_dual_flow_daily.py — 22:35 东财(EM) vs 同花顺(THS) 双口径逐日采集 (2026-08-18)

背景（R22 双口径资金闸数据源，日发精机案）:
  单口径资金流结论不可信——EM+1.50亿 vs THS-1.92亿方向相反（4/4样本泛化:
  日发精机/中石科技/农发种业方向反, 罗牛山幅度差11倍）。独立算法只有 EM/THS
  两家（westock/efinance/ftshare 只是 EM 的运输线, 数字一致）。
  铁律: 资金流窗口含涨停日时必须拆逐日; 无双口径不输出"资金强"结论。

本脚本分两段, 由 22:35 cron 会话编排:
  --phase ths            : 本地 akshare 拉同花顺全市场个股资金流 3/5/10日排行
                           + vipdoc 停板标记 + 生成 EM 待查清单(当日候选)
  --phase em --input F   : 合并 cron 会话用 MCP 查回的东财口径(em_results.json)
                           → UPSERT 写入 em_flow_snapshot.db (幂等, 逐日累积)

EM 待查清单(data/em_todo_YYYYMMDD.json) 生成后, cron 会话须把当前持仓
(westock portfolio_paper_positions)附加进 "codes" 再去查 EM 口径:
  EM 口径 MCP 工具: westock data_fund_flow / ftshare eastmoney_stock
  （em_results.json 格式: [{"code":"002385","em_today":..,"em_5d":..,"em_10d":..,"em_20d":..}, ...]）

用法:
  python collect_dual_flow_daily.py --phase ths [--scanner-report <csv路径>]
  python collect_dual_flow_daily.py --phase em --input data/em_results_YYYYMMDD.json
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import struct
import sys

import akshare as ak
import pandas as pd

DATA_DIR = r"D:\zcodesj\data"
REPORT_DIR = r"D:\zcodesj\reports"
DB_PATH = r"D:\zcodesj\data\em_flow_snapshot.db"
VIPDOC = r"D:\anzhuang\tdx\vipdoc"          # 通达信本地日线(32字节/记录, 价格*100)
THS_SYMBOLS = {"ths_3d": "3日排行", "ths_5d": "5日排行", "ths_10d": "10日排行"}


def _parse_amt(s):
    """'2.02亿'/'-1.92亿'/'520.3万' → 元(float)。"""
    if not isinstance(s, str):
        return None
    s = s.replace(",", "")
    m = __import__("re").match(r"(-?[\d.]+)(亿|万)", s)
    if not m:
        return None
    return float(m.group(1)) * (1e8 if m.group(2) == "亿" else 1e4)


def norm6(code) -> str:
    """常州前缀统一为6位纯数字: 'sz002385'/'sh600519'/'002385.SZ' → '002385'。"""
    c = str(code).strip().lower()
    for p in ("sh", "sz", "bj"):
        if c.startswith(p):
            c = c[len(p):]
            break
    return c.split(".")[0].zfill(6)


_DATE_OVERRIDE: dt.date | None = None  # --date 补历史日用（2026-08-23 补 8/21 双口径缺口）


def today_s() -> str:
    """交易日标签: ISO 格式 YYYY-MM-DD（与库内既有行一致, r22 闸门 fromisoformat 直读）。"""
    return (_DATE_OVERRIDE or dt.date.today()).isoformat()


def today8() -> str:
    return (_DATE_OVERRIDE or dt.date.today()).strftime("%Y%m%d")


def fetch_ths_rank(symbol: str) -> pd.DataFrame:
    """单窗口全市场 THS 净流入排行 → [code, 净流入元]。"""
    df = ak.stock_fund_flow_individual(symbol=symbol)
    amt_col = "净额" if "净额" in df.columns else "资金流入净额"
    df["code"] = df["股票代码"].astype(str).str.strip().str.zfill(6)
    df["flow"] = df[amt_col].map(_parse_amt)
    return df[["code", "flow"]].dropna().rename(columns={"flow": symbol})


def flag_limit_days(codes) -> dict:
    """读 vipdoc 日线, 标注近5交易日涨停/跌停天数（算法争议区标记, 与 scanner 同逻辑）。"""
    out = {}
    for code in codes:
        mkt = "sh" if code.startswith("6") else "bj" if code[0] in "48" else "sz"
        path = os.path.join(VIPDOC, mkt, "lday", f"{mkt}{code}.day")
        n = 0
        try:
            with open(path, "rb") as f:
                f.seek(0, 2)
                size = f.tell()
                f.seek(max(0, size - 6 * 32))          # 最近6条(算5个涨跌幅)
                buf = f.read(6 * 32)
            recs = [struct.unpack("<IIIIIfII", buf[i:i + 32]) for i in range(0, len(buf), 32)]
            closes = [(r[0], r[4] / 100) for r in recs]  # (date, close)
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


def phase_ths(scanner_report: str | None) -> None:
    """拉 THS 全市场 3/5/10日排行 → csv；由候选报告生成 EM 待查清单 json。"""
    print(f"=== 阶段1: THS 双口径采集 {today_s()} ===")
    merged = None
    for tag, symbol in THS_SYMBOLS.items():
        try:
            sub = fetch_ths_rank(symbol)
            print(f"[THS] {symbol}: {len(sub)} 只")
        except Exception as e:
            print(f"[WARN] THS {symbol} 获取失败: {type(e).__name__} {str(e)[:80]}")
            continue
        merged = sub.rename(columns={symbol: tag}) if merged is None \
            else merged.merge(sub.rename(columns={symbol: tag}), on="code", how="outer")
    if merged is None or merged.empty:
        print("ERROR: THS 全部窗口获取失败, 终止")
        sys.exit(1)
    merged["code"] = merged["code"].str.zfill(6)

    # 附带名称（同花顺即时榜最新一只任意取 → 直接用 5日排行再拉一次名称列）
    try:
        df_name = ak.stock_fund_flow_individual(symbol="5日排行")[["股票代码", "股票简称"]]
        df_name["code"] = df_name["股票代码"].astype(str).str.strip().str.zfill(6)
        merged = merged.merge(df_name[["code", "股票简称"]], on="code", how="left")
        merged = merged.rename(columns={"股票简称": "name"})
    except Exception as e:
        merged["name"] = ""
        print(f"[WARN] 名称补全失败: {type(e).__name__}")

    csv_path = os.path.join(DATA_DIR, f"ths_individual_flow_{today8()}.csv")
    merged.to_csv(csv_path, index=False, encoding="utf-8-sig")
    print(f"[落盘] THS 全市场宽表 {csv_path} 共{len(merged)}行")

    # ---- EM 待查清单: 当日候选(若有 scanner 报告) ----
    codes, names = [], {}
    if scanner_report and os.path.exists(scanner_report):
        cand = pd.read_csv(scanner_report, encoding="utf-8-sig")
        for _, r in cand.iterrows():
            c = norm6(r.get("code", ""))
            if c and c not in codes:
                codes.append(c)
            if c and c not in names:
                names[c] = str(r.get("name", "") or "")
        print(f"[待查] 来自{os.path.basename(scanner_report)}: {len(codes)} 只候选")
    else:
        print(f"[待查] 未找到 scanner 报告({scanner_report or '默认路径'}) → 仅使用 --add-codes 之外的 0 只; "
              f"会话须把持仓附加进 em_todo json 后逐只查 EM")

    todo_path = os.path.join(DATA_DIR, f"em_todo_{today8()}.json")
    with open(todo_path, "w", encoding="utf-8") as f:
        json.dump({"date": today_s(), "codes": codes, "names": names}, f,
                  ensure_ascii=False, indent=2)
    print(f"[落盘] EM 待查清单 {todo_path}")
    print(f"[SOP] 下一步: ①把当前持仓(westock portfolio_paper_positions) codes 附加进该 json; "
          f"②对其逐只调 westock data_fund_flow(近30个自然日区间取日序列, 自行累计当日/5/10/20日主力净流入, "
          f"单位元)汇总成 em_results json; ③python collect_dual_flow_daily.py --phase em --input <em_results>")


def phase_em(em_input: str) -> None:
    """合并 EM 口径结果与 THS 快照, UPSERT 写 em_flow_snapshot.db。"""
    print(f"=== 阶段2: EM 合并入库 {today_s()} ===")
    with open(em_input, encoding="utf-8") as f:
        em_rows = json.load(f)
    if not em_rows:
        print("ERROR: em_results 为空")
        sys.exit(1)

    # 归一键: 支持 em_5d / 5d / em5d
    def pick(r, *keys):
        for k in keys:
            if k in r and r[k] is not None:
                return float(r[k])
        return None

    em = {norm6(r["code"]): {
        "em_today": pick(r, "em_today", "today"),
        "em_5d": pick(r, "em_5d", "5d", "em5d"),
        "em_10d": pick(r, "em_10d", "10d", "em10d"),
        "em_20d": pick(r, "em_20d", "20d", "em20d"),
        "name": r.get("name", ""),
    } for r in em_rows if r.get("code")}
    print(f"[EM] 读取 {len(em)} 只 MCP 查回结果")

    ths_path = os.path.join(DATA_DIR, f"ths_individual_flow_{today8()}.csv")
    if not os.path.exists(ths_path):
        print(f"ERROR: 未找到 THS 快照 {ths_path}，先跑 --phase ths")
        sys.exit(1)
    ths = pd.read_csv(ths_path, encoding="utf-8-sig")
    ths_map = {norm6(r["code"]): dict(r) for _, r in ths.iterrows()}  # Series→dict, .get 取值

    ts_now = dt.datetime.now().isoformat(timespec="seconds")
    ld = flag_limit_days(list(em.keys()))

    import sqlite3
    con = sqlite3.connect(DB_PATH)
    n_ins, n_upd = 0, 0
    rows_for_report = []
    for code, e in em.items():
        t = ths_map.get(code, {})
        name = e["name"] or t.get("name", "")
        vals = (
            ts_now, today_s(), code, name,
            e["em_today"], e["em_5d"], e["em_10d"], e["em_20d"],
            t.get("ths_5d"), t.get("ths_3d"), t.get("ths_10d"),
            int(ld.get(code, 0)),
        )
        ex = con.execute(
            "SELECT 1 FROM flow_snapshot WHERE code=? AND trade_date=?", (code, today_s())
        ).fetchone()
        con.execute(
            "INSERT OR REPLACE INTO flow_snapshot "
            "(ts, trade_date, code, name, em_today, em_5d, em_10d, em_20d, "
            "ths_5d, ths_3d, ths_10d, limit_days_5) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            vals,
        )
        n_upd += 1 if ex else 0
        n_ins += 0 if ex else 1
        rows_for_report.append((code, name, e["em_5d"], t.get("ths_5d"),
                                int(ld.get(code, 0))))
    con.commit()
    con.close()
    print(f"[入库] 新增{n_ins} 更新{n_upd} → em_flow_snapshot.db (trade_date={today_s()})")

    # ---- 双口径分歧清单(反臆想铁律: 方向相反必报) ----
    div, same_neg, warn_ld = [], [], []
    for code, name, em5, ths5, ld5 in rows_for_report:
        if em5 is None or ths5 is None:
            continue
        if (em5 > 0 > ths5) or (em5 < 0 < ths5):
            div.append((code, name, em5, ths5))
        elif em5 <= 0 and ths5 <= 0:
            same_neg.append((code, name, em5, ths5))
        if ld5:
            warn_ld.append((code, name, ld5))
    if div:
        print(f"\n[🔴双口径分歧 {len(div)} 只 — 严禁引用'资金强'结论, 须拆逐日核实]:")
        for c, n, e5, t5 in div:
            print(f"  {c} {n}: EM5日={e5:+,.0f} vs THS5日={t5:+,.0f}")
    if same_neg:
        print(f"\n[双口径同负 {len(same_neg)} 只 — 资金弱势]:")
        for c, n, e5, t5 in same_neg[:20]:
            print(f"  {c} {n}: EM5日={e5:+,.0f} THS5日={t5:+,.0f}")
        if len(same_neg) > 20:
            print(f"  ... 共{len(same_neg)}只")
    if warn_ld:
        print(f"\n[⚠️近5日含停板日(算法争议区) {len(warn_ld)} 只 — 同正也须拆逐日]:")
        for c, n, d in warn_ld[:15]:
            print(f"  {c} {n}: 停板{d}天")
    print("\n[SOP] R22 闸门已就绪: pre_trade_check_v2.py --code <code> 会自动读本库做双口径判定")


def main() -> int:
    ap = argparse.ArgumentParser(description="22:35 东财vs同花顺双口径每日采集")
    ap.add_argument("--phase", required=True, choices=["ths", "em"], help="ths=拉同花顺+生成待查清单; em=合并EM结果入库")
    ap.add_argument("--scanner-report", default="", help="当日 scanner 报告 csv 路径(缺省 auto 探测 reports/universe_scan_YYYYMMDD.csv)")
    ap.add_argument("--input", default="", help="--phase em 时: EM 口径查询结果 json 路径")
    ap.add_argument("--date", default="", help="补历史日 YYYY-MM-DD(默认今日); 非交易日补最近交易日数据用, 覆盖 today_s/today8")
    a = ap.parse_args()

    if a.date:
        global _DATE_OVERRIDE
        _DATE_OVERRIDE = dt.date.fromisoformat(a.date)

    if a.phase == "ths":
        if not a.scanner_report:
            probe = os.path.join(REPORT_DIR, f"universe_scan_{today8()}.csv")
            if os.path.exists(probe):
                a.scanner_report = probe
        phase_ths(a.scanner_report or None)
    else:
        if not a.input or not os.path.exists(a.input):
            print(f"ERROR: --input 文件不存在: {a.input}")
            return 1
        phase_em(a.input)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())