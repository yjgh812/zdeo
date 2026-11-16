#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
earnings_event_brief.py — 业绩预告/快报事件博弈速读（纯呈现层，零交易权）

源自外部「业绩分析师(yjygby)」skill 的五步推演框架移植（2026-10-20 主人「全部全量继续」令）。
数据=东财 datacenter 业绩预告 RPT_PUBLIC_OP_NEWPREDICT（类型/区间/变动原因）+ qfq 库股价位置。

五步链（呈现框架，非预测）：
  ①性质判断（预告类型+区间幅度中值 vs 上年同期）
  ②是否已被交易（公告日前20日涨幅=提前定价；公告日至今涨幅=事后反应）
  ③核心博弈点（变动原因原文 + 预告下限符号）
  ④多情景推演（框架性三情景，非预测：仅列出该性质下传统上的路径分类）
  ⑤策略视角提示（持仓者/观望者双视角模板）

⚠️ 一致预期缺失声明：本件无机构一致预期源，"超预期/低于预期"定性基于预告类型与幅度，
非一致预期口径；结论禁止作为买卖依据。

用法：python earnings_event_brief.py --code 601127 [--lookback 20]
退出码：0=成功; 2=名称查无/无预告记录
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj")
from financial_quick_read import em_datacenter, num, pct_s, yi  # noqa: E402
from stock_name_lookup import get_name  # noqa: E402

QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
REPORT_DIR = Path(r"D:\zcodesj\reports")
DISCLAIMER = (
    "> ⚠️ 纯呈现零交易权：本件为业绩事件速读，不触发任何交易动作。🔴一致预期缺失声明："
    "「超预期/低于预期」定性基于预告类型与幅度区间，非机构一致预期口径；"
    "好业绩也可能跌、利空也可能不反弹；情景推演为呈现框架非预测。"
)


def price_context(code: str, notice_date: str, lookback: int):
    """公告日前 lookback 日涨幅、公告日至今涨幅、现价相对公告日 250 日区间位置。"""
    cn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    rows = cn.execute(
        "SELECT date, close FROM stock_daily_meta_qfq WHERE code LIKE ? AND date<=? "
        "ORDER BY date DESC LIMIT 320", (code + "%", notice_date.replace("-", "")),
    ).fetchall()
    cn.close()
    if len(rows) < lookback + 60:
        return None
    rows = rows[::-1]                       # 升序, 末行=公告日
    closes = [r[1] for r in rows]
    d_notice = rows[-1][0]
    pre_chg = (closes[-1] / closes[-1 - lookback] - 1) * 100
    # 公告日至今
    cn = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
    after = cn.execute(
        "SELECT date, close FROM stock_daily_meta_qfq WHERE code LIKE ? AND date>=? "
        "ORDER BY date", (code + "%", notice_date.replace("-", "")),
    ).fetchall()
    cn.close()
    post_chg = (after[-1][1] / closes[-1] - 1) * 100 if after else None
    last_close = after[-1][1] if after else closes[-1]
    hi, lo = max(closes[-250:]), min(closes[-250:])
    pos = (last_close - lo) / (hi - lo) * 100 if hi > lo else None
    return {"notice_date": d_notice, "pre_chg": pre_chg, "post_chg": post_chg, "pos250": pos}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--code", required=True)
    ap.add_argument("--lookback", type=int, default=20)
    args = ap.parse_args()
    if sys.stdout and hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    code = args.code.strip()
    name = get_name(code)
    if not name:
        print(f"[FAIL] 名称接口查无 {code}")
        sys.exit(2)
    secucode = code + (".SH" if code.startswith(("6", "9")) else ".SZ")
    try:
        rows, _ = em_datacenter("RPT_PUBLIC_OP_NEWPREDICT", f'(SECUCODE="{secucode}")')
    except Exception as e:
        print(f"[FAIL] 东财接口获取失败({type(e).__name__}: {e})——多为间歇限流, 请稍后重跑; 不做编造降级")
        sys.exit(2)
    if not rows:
        print(f"[N/A] {name}({code}) 东财无业绩预告记录")
        sys.exit(2)
    r = rows[0]                              # 最近一次预告

    def _nz(v):
        """预告净额/上年同期语境: 0 值视为未披露(按EPS等口径披露时该字段被填0)"""
        return None if v in (None, 0) else v

    nd = (r.get("NOTICE_DATE") or "")[:10]
    ptype = r.get("PREDICT_TYPE") or "N/A"
    fin_label = (r.get("PREDICT_FINANCE") or "净利润").strip()   # 口径: 净利润/每股收益
    is_eps = "每股" in fin_label or "EPS" in fin_label.upper()
    lo_a, hi_a = num(r.get("ADD_AMP_LOWER")), num(r.get("ADD_AMP_UPPER"))
    lo_v, hi_v = num(r.get("PREDICT_AMT_LOWER")), num(r.get("PREDICT_AMT_UPPER"))
    mid_a = (lo_a + hi_a) / 2 if (lo_a is not None and hi_a is not None) else None
    prev_year = num(r.get("PREYEAR_SAME_PERIOD"))
    ctx = price_context(code, nd, args.lookback) or {}
    age_days = (datetime.now() - datetime.strptime(nd, "%Y-%m-%d")).days if nd else None

    L = [f"# 业绩预告博弈速读 · {name}({code})", "", DISCLAIMER, ""]
    L.append("## ① 事件性质")
    L.append(f"- 预告类型 **{ptype}** | 报告期 {(r.get('REPORT_DATE') or '')[:10]} | 公告日 {nd}"
             + (f" | ⚠️ 距今 {age_days} 天（该股最近一次预告, 非近期事件时注意时效）"
                if age_days and age_days > 180 else ""))
    if mid_a is not None:
        L.append(f"- 净利幅度区间 {pct_s(lo_a)} ~ {pct_s(hi_a)}（中值 {pct_s(mid_a)}）")
    if lo_v is not None or hi_v is not None:
        if is_eps:
            L.append(f"- 预告口径={fin_label}: EPS 区间 {lo_v} ~ {hi_v} 元（**按每股收益披露, 非净额**）")
        else:
            L.append(f"- 预告口径={fin_label}: 净额区间 {yi(lo_v)} ~ {yi(hi_v)}")
    if r.get("PREDICT_CONTENT"):
        L.append(f"- 原文：{str(r.get('PREDICT_CONTENT'))[:160]}")
    if prev_year is not None:
        L.append(f"- 上年同期{fin_label} {prev_year if is_eps else yi(prev_year)}")
    elif not is_eps:
        L.append("- 上年同期净利 N/A")
    else:
        L.append("- 上年同期 N/A(按EPS等口径披露)")
    L.append("")
    L.append("## ② 是否已被交易（股价位置）")
    if ctx:
        L.append(f"- 公告日前{args.lookback}日涨幅 {pct_s(ctx.get('pre_chg'))}"
                 f"（>10% 提示已提前定价）")
        L.append(f"- 公告日({ctx.get('notice_date')})至今涨幅 {pct_s(ctx.get('post_chg'))}")
        L.append(f"- 现价处公告日近250日区间 {pct_s(ctx.get('pos250'), 1)} 分位"
                 f"（>80%=高位 <20%=低位 <0%=已跌破区间下沿）")
    else:
        L.append("- qfq 价格窗口不足 N/A")
    L.append("")
    L.append("## ③ 核心博弈点")
    if r.get("CHANGE_REASON_EXPLAIN"):
        L.append(f"- 变动原因：{str(r.get('CHANGE_REASON_EXPLAIN'))[:200]}")
    if lo_v is not None and lo_v < 0:
        L.append("- 预告下限为负（亏损区间），关注亏损是否含一次性项目与扣非口径")
    L.append("- 一致预期缺失：超预期定性仅为类型与幅度口径（见声明），非机构预期差")
    L.append("")
    L.append("## ④ 多情景推演（框架性分类，非预测）")
    L.append(f"- 强路径：{ptype} 且未被充分定价（②前涨幅低）→ 传统上以业绩兑现延续观察")
    L.append(f"- 中路径：性质已被定价（②前涨幅高）→ 传统上以兑现/回落分化观察")
    L.append(f"- 弱路径：{('利空类' if (mid_a or 0) < 0 else '利好落空')} + 已深跌 → "
             f"传统上以利空出尽反弹条件验证观察（须右侧确认，禁止左侧抄底假设）")
    L.append("")
    L.append("## ⑤ 策略视角提示（模板，非建议）")
    L.append("- 持仓者视角：关注②的位置分位与③博弈点变化，卖出决策走既有卖出链（趋势走坏/S-5）")
    L.append("- 观望者视角：本件不产生买点；任何买入须走 pre_trade_check_v2 闸门与信号源准入")
    L.append("")

    out = "\n".join(L)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    rp = REPORT_DIR / f"EARNINGS_EVENT_{code}_{nd.replace('-', '')}.md"
    rp.write_text(out, encoding="utf-8")
    print(out)
    print(f"[OK] {rp}")


if __name__ == "__main__":
    main()
