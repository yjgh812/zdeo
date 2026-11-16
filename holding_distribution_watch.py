#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
holding_distribution_watch.py — 持仓筹码派发观测标签 (2026-09-01)

定位: 纯观测层, 不进交易链, 不降档; 输出提示行供复盘/C6 参考。
来源: 主人裁决 P2(000893 亚钾国际案例, reports/CASE_ENGINE_BLINDSPOT_000893_20260901.md)

标签语义(锁死):
  FLOW-OUT     超大单持续派发: 近10交易日中 r0_net(超大单净额,元)<0 天数>=7
               且 近5日 r0_net 合计<0
  FLOW-OUT-STR 加强: FLOW-OUT 且 近5日超大单累计净流出 <= -3亿元
               (阈值校准: 000893 2026/04/14 回放实测 5日累计-2.83亿, 见 SOP 注记)
  HOLDER-SPIKE 筹码分散化: 最新披露股东户数环比 > +20% (季度频率, 披露后才可见=PIT)
  UNAVAILABLE  数据不可得(结构化, 禁伪健康)

数据源:
  资金流  新浪 MoneyFlow 历史(vip.stock.finance.sina.com.cn, 项目既有新浪源家族)
  户数    akshare stock_zh_a_gdhs_detail_em (EM datacenter 域, 与 push2his 行情域不同)

用法:
  python holding_distribution_watch.py --codes 000893,600519
  python holding_distribution_watch.py --codes 000893 --replay 20260414   # 历史回放(数据截断至该日)
输出: stdout + 追加 data/holding_watch_tags.jsonl (code+asof 幂等覆盖)
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

import pandas as pd

DATA_OUT = Path(r"D:\zcodesj\data\holding_watch_tags.jsonl")
SINA_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
            "MoneyFlow.ssl_qsfx_lscjfb?page=1&num={num}&sort=opendate&asc=0&daima={daima}")
WINDOW_DAYS = 10
WINDOW_OUT_MIN = 7
RECENT_DAYS = 5
STR_R5_YUAN = -3e8   # FLOW-OUT-STR: 近5日超大单累计净流出 <= 3亿元(000893 4/14回放校准)
HOLDER_SPIKE = 0.20


def fetch_sina_flow(code: str, num: int = 140) -> pd.DataFrame:
    """新浪个股资金流历史: r0_net=超大单净额(元), netamount=主力净额(元)"""
    daima = ("sh" if code.startswith(("6", "9")) else "sz") + code
    req = urllib.request.Request(SINA_URL.format(num=num, daima=daima),
                                 headers={"User-Agent": "Mozilla/5.0",
                                          "Referer": "https://finance.sina.com.cn"})
    raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="ignore")
    rows = json.loads(raw)
    df = pd.DataFrame(rows)
    df["date"] = pd.to_datetime(df["opendate"])
    df = df.sort_values("date").reset_index(drop=True)
    for c in ("r0_net", "netamount", "turnover"):
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def fetch_holder_num(code: str) -> pd.DataFrame | None:
    """股东户数序列(季度披露), 失败返回 None(结构化 N/A, 不阻断)"""
    try:
        import akshare as ak
        df = ak.stock_zh_a_gdhs_detail_em(symbol=code)
        df = df.copy()
        df["截止日"] = pd.to_datetime(df["股东户数统计截止日"])
        df["披露日"] = pd.to_datetime(df["股东户数公告日期"])
        df["户数"] = pd.to_numeric(df["股东户数-本次"], errors="coerce")
        return df.sort_values("截止日").reset_index(drop=True)
    except Exception:
        return None


def judge_flow(hist: pd.DataFrame) -> tuple[str, dict]:
    w = hist.tail(WINDOW_DAYS)
    r5 = hist.tail(RECENT_DAYS)
    out_days = int((w["r0_net"] < 0).sum())
    r5_sum = float(r5["r0_net"].sum())          # 元
    details = {"out_days_in10": out_days, "r5_super_net_yuan": round(r5_sum, 1)}
    if out_days >= WINDOW_OUT_MIN and r5_sum < 0:
        tag = "FLOW-OUT-STR" if r5_sum <= STR_R5_YUAN else "FLOW-OUT"
    else:
        tag = "OK"
    return tag, details


def judge_holder(h: pd.DataFrame | None, asof: pd.Timestamp) -> tuple[str, dict]:
    if h is None:
        return "N/A", {"reason": "holder_num_source_unavailable"}
    vis = h[h["披露日"] <= asof]
    if len(vis) < 2:
        return "N/A", {"reason": "no_visible_disclosure", "visible": int(len(vis))}
    last, prev = vis.iloc[-1], vis.iloc[-2]
    if pd.isna(last["户数"]) or pd.isna(prev["户数"]) or prev["户数"] == 0:
        return "N/A", {"reason": "bad_numbers"}
    chg = float(last["户数"]) / float(prev["户数"]) - 1
    details = {"截止日": str(last["截止日"].date()), "披露日": str(last["披露日"].date()),
               "户数环比": round(chg, 4), "上期截止日": str(prev["截止日"].date())}
    return ("HOLDER-SPIKE" if chg > HOLDER_SPIKE else "OK"), details


def save_tags(rows: list[dict]) -> None:
    if not rows:
        return
    keys = {(r["code"], r["asof"]) for r in rows}
    old: list[dict] = []
    if DATA_OUT.exists():
        for line in DATA_OUT.read_text(encoding="utf-8").splitlines():
            try:
                o = json.loads(line)
                if (o.get("code"), o.get("asof")) not in keys:
                    old.append(o)
            except Exception:
                continue
    old.extend(rows)
    DATA_OUT.write_text("\n".join(json.dumps(o, ensure_ascii=False) for o in old) + "\n",
                        encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--codes", required=True, help="逗号分隔6位代码")
    ap.add_argument("--replay", default=None, help="YYYYMMDD 历史回放(资金流截断至该日)")
    args = ap.parse_args()
    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    asof = (pd.to_datetime(args.replay, format="%Y%m%d") if args.replay
            else pd.Timestamp(datetime.now().date()))
    asof8 = asof.strftime("%Y%m%d")
    out_rows = []
    print(f"== 持仓筹码派发观测 asof={asof.date()} ==")
    for code in codes:
        try:
            hist = fetch_sina_flow(code)
            hist = hist[hist["date"] <= asof]
            if len(hist) < WINDOW_DAYS:
                raise ValueError(f"flow_history_insufficient({len(hist)})")
            flow_tag, flow_d = judge_flow(hist)
        except Exception as e:
            flow_tag, flow_d = "UNAVAILABLE", {"reason": str(e)[:80]}
        h = fetch_holder_num(code)
        h_tag, h_d = judge_holder(h, asof)
        tags = [t for t in (flow_tag, h_tag) if t not in ("OK",)]
        final = "+".join(tags) if tags else "OK"
        print(f"  {code}: {final}  flow={flow_tag} {flow_d}  holder={h_tag} {h_d}")
        out_rows.append({"code": code, "asof": asof8, "tag": final,
                         "flow": {"tag": flow_tag, **flow_d},
                         "holder": {"tag": h_tag, **h_d},
                         "ts": datetime.now().isoformat(timespec="seconds")})
    save_tags(out_rows)
    print(f"[落库] {DATA_OUT} (+{len(out_rows)} rows, 幂等覆盖)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
