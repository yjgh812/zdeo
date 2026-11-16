# -*- coding: utf-8 -*-
"""
LSTM灰度轨道 — 深度时序翻案落地 (2026-08-16策略扩展工程)
================================================
依据: LSTM_ROLLING_RETRAIN_20260816 翻案确认(rolling归一严格无泄漏口径
      5年IC全正, 均值0.0600, 超同口径Ridge -0.0021)

轨道: 独立10万元账本, 月度换仓, 与生产bp+ep并行; 观察期 2026-09-01 建仓 ~ 2026-11-30 评估
信号: LSTM(2×64) 5量价特征×60日窗 → Fwd20截面rank → Top20等权
      过滤: ≤30元(原始价)+白名单+非ST; next-bar语义(信号月末→次月首交易日按收盘成交近似)

用法:
  --init                初始化账本
  --signal --date YYYYMMDD  仅生成信号文件
  --run [--date]        主流程(cron): 本月首交易日执行换仓+月报
产出: D:\\zcodesj\\gray_lstm_track\\ledger.json / signal_*.md / report_*.md
"""
from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import torch

sys.path.insert(0, r"D:\zcodesj")
from deep_sequence_retest_20260816 import SeqLSTM, DEV, WIN, FWD  # noqa: E402
from strategy_test_harness import load_qfq, load_raw_close, load_st_codes  # noqa

TRACK_DIR = Path(r"D:\zcodesj\gray_lstm_track")
LEDGER = TRACK_DIR / "ledger.json"
COST = 0.001  # 单边0.1%(与id_vol灰度一致)
MODEL_CACHE = Path(r"D:\zcodesj\_cache\lstm_gray_model.pt")


def ensure_model(train_end_year: int):
    """训练/加载LSTM(训练段=该年之前3年, 与翻案实验同协议)。"""
    if MODEL_CACHE.exists():
        m = SeqLSTM().to(DEV)
        m.load_state_dict(torch.load(MODEL_CACHE, weights_only=True))
        m.eval()
        return m
    from deep_sequence_retest_20260816 import prepare_data, train_year
    data = prepare_data()
    model = SeqLSTM().to(DEV)
    years = tuple(y for y in range(train_end_year - 3, train_end_year))
    train_year(model, data, years)
    torch.save(model.state_dict(), MODEL_CACHE)
    print(f"  模型已训并存: {MODEL_CACHE}")
    model.eval()
    return model, data


def model_with_data():
    if MODEL_CACHE.exists():
        m = SeqLSTM().to(DEV)
        m.load_state_dict(torch.load(MODEL_CACHE, weights_only=True))
        m.eval()
        from deep_sequence_retest_20260816 import prepare_data
        return m, prepare_data()
    return ensure_model(2026)


def gen_signal(asof: pd.Timestamp) -> list:
    """信号日截面 → Top20代码清单。"""
    model, data = model_with_data()
    codes = data["codes"]
    dates = data["dates"]
    # 找<=asof最近的位置
    pos = int(np.searchsorted(dates.values, np.datetime64(asof), side="right") - 1)
    if pos - WIN < 0:
        raise ValueError("数据不足")
    x = data["F"][:, :, pos - WIN:pos]     # (C,N,60) 严格不含asof后数据
    xb = torch.tensor(x.transpose(1, 0, 2)[None], dtype=torch.float32, device=DEV)
    with torch.no_grad():
        pred = model(xb)[0].cpu().numpy()
    s = pd.Series(pred, index=codes)
    raw_close = load_raw_close("20260101", "20261231")
    d_key = dates[pos]
    px_row = raw_close.loc[raw_close.index <= d_key].iloc[-1] if len(raw_close) else None
    st = load_st_codes()
    ok = [c for c in s.index
          if c not in st and (px_row is None or
                              (c in px_row.index and 0 < px_row[c] <= 30))]
    s = s.loc[ok]
    return list(s.sort_values(ascending=False).head(20).index), d_key


def write_signal_md(codes, d_key):
    from stock_name_lookup import require_name
    TRACK_DIR.mkdir(exist_ok=True)
    lines = [f"# LSTM灰度轨道信号 ({time.strftime('%Y-%m-%d')})", "",
             f"- 信号截面日: {pd.Timestamp(d_key):%Y-%m-%d}",
             "- 模型: LSTM 2×64, 5量价特征×60窗, rolling expanding归一",
             "- 口径: Top20等权, ≤30元, 非ST, 单边成本0.1%", "",
             "| # | 代码 | 名称 |", "|---|------|------|"]
    for i, c in enumerate(codes):
        lines.append(f"| {i+1} | {c} | {require_name(c)} |")
    p = TRACK_DIR / f"signal_{pd.Timestamp(d_key):%Y%m%d}.md"
    p.write_text("\n".join(lines), encoding="utf-8")
    return p


def load_ledger():
    return json.loads(LEDGER.read_text(encoding="utf-8")) if LEDGER.exists() else None


def init_ledger():
    TRACK_DIR.mkdir(exist_ok=True)
    led = {"cash": 100000.0, "positions": {}, "nav_history": [],
           "created": time.strftime("%Y-%m-%d"), "track": "lstm_gray"}
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"账本初始化: {LEDGER} (现金10万)")
    return led


def apply_rebalance(ledger, codes, exec_px):
    """按收盘价换仓(Top20等权目标)。"""
    from stock_name_lookup import require_name
    target_value = (ledger["cash"] +
                    sum(v["shares"] * exec_px.get(c, v["last_px"])
                        for c, v in ledger["positions"].items()))
    per = target_value / max(len(codes), 1)
    # 卖出不在新清单的
    for c in list(ledger["positions"]):
        if c not in codes:
            px = exec_px.get(c, ledger["positions"][c]["last_px"])
            ledger["cash"] += ledger["positions"][c]["shares"] * px * (1 - COST)
            del ledger["positions"][c]
    # 买入/调平
    for c in codes:
        px = exec_px.get(c)
        if px is None or px <= 0:
            continue
        cur = ledger["positions"].get(c, {"shares": 0.0, "last_px": px,
                                          "name": require_name(c)})
        want = per / px
        diff = want - cur["shares"]
        if abs(diff) * px < 200:   # 忽略微小调整
            continue
        if diff > 0:
            cost_need = diff * px * (1 + COST)
            if ledger["cash"] >= cost_need:
                ledger["cash"] -= cost_need
                cur["shares"] = want
        else:
            ledger["cash"] += (-diff) * px * (1 - COST)
            cur["shares"] = want
        cur["last_px"] = px
        cur["name"] = require_name(c)
        ledger["positions"][c] = cur
    return ledger


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--init", action="store_true")
    ap.add_argument("--signal", action="store_true")
    ap.add_argument("--run", action="store_true")
    ap.add_argument("--date", default=None)
    args = ap.parse_args()
    t0 = time.time()
    if args.init:
        init_ledger()
        return
    asof = pd.Timestamp(args.date) if args.date else pd.Timestamp.now()
    codes, d_key = gen_signal(asof)
    print(f"  信号: {len(codes)}只 @截面{pd.Timestamp(d_key):%Y%m%d} ({time.time()-t0:.0f}s)")
    p = write_signal_md(codes, d_key)
    print(f"  信号文件: {p}")
    if not args.run:
        return
    led = load_ledger() or init_ledger()
    q = load_qfq(asof.strftime("%Y%m%d"), asof.strftime("%Y%m%d"), fields=("close",))
    px = {c: float(q["close"].iloc[-1][c]) for c in codes
          if c in q["close"].columns and not np.isnan(q["close"].iloc[-1][c])}
    led = apply_rebalance(led, codes, px)
    nav = led["cash"] + sum(v["shares"] * v["last_px"] for v in led["positions"].values())
    led["nav_history"].append({"date": asof.strftime("%Y%m%d"), "nav": nav})
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"  换仓完成: NAV={nav:,.0f} 持仓{len(led['positions'])}只")


if __name__ == "__main__":
    main()
