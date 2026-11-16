# -*- coding: utf-8 -*-
"""批次作战计划生成器 (20261025, 零交易权 — 提请制, 买入须主人逐笔令 + trade_executor --execute)

把已验证零件拼成可执行批次计划:
  选股 = lowconsol_signal_latest.json (A格低位横盘, OOS_CONFIRMED +2.75~4.55pp)
  组合 = 主线命中优先(streak降序,低价升序) + 背景池种子随机补足; 同L1行业上限(默认1/3批次)
  建仓 = 一次性等权小仓(预算/只数, 100股整倍); 金字塔加仓已证伪(20261025测试)不用
  价格 = 限价=收盘x1.01(接受<=+1%跳空,不追高); 止损=限价x0.92 (-8%铁律); 出场=第20个交易日收盘
  排雷 = R42商誉/R43大宗 inline 标注(WARN级呈报不剔除, 与闸门语义一致)
用法: python analysis/batch_battle_plan.py --budget 72000 [--max-picks 12] [--max-per-industry 4]
      [--exclude 601939,600000,...] [--seed 42]
输出: 控制台表 + data/batch_battle_plan_latest.json + 可粘贴executor命令(dry-run, 不含--execute)
"""
import argparse
import json
import sqlite3
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
from pre_trade_check_v2 import check_r42_goodwill_high, check_r43_block_trade_anomaly  # noqa: E402


def _fetch_positions():
    """复用 stop_loss_executor 的 westock MCP 持仓直连(owner=执行链, 此处只读消费)。"""
    try:
        from stop_loss_executor import _fetch_positions_via_mcp
        return _fetch_positions_via_mcp()
    except Exception:
        return None


def _fetch_available_cash():
    """westock MCP 组合概要取可用现金 — 镜像 stop_loss_executor 的 config 发现模式(只读)。"""
    import os
    import urllib.request as _ur
    url = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
    token = ""
    try:
        _cfgp = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\Administrator"),
                             ".zcode", "cli", "config.json")
        cfg = json.load(open(_cfgp, encoding="utf-8"))
        ws = cfg.get("mcp", {}).get("servers", {}).get("westock-mcp", {})
        token = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
    except OSError:
        pass
    if not token:
        token = os.environ.get("WESTOCK_TOKEN", "")
    if not token:
        return None

    def rpc(payload):
        body = json.dumps(payload).encode()
        h = {"Authorization": f"Bearer {token}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream"}
        req = _ur.Request(url, data=body, headers=h, method="POST")
        with _ur.urlopen(req, timeout=30) as r:
            return r.read().decode("utf-8")
    try:
        rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "tulong-batchplan", "version": "1.0"}}})
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        raw = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "portfolio_paper_portfolio", "arguments": {}}})
        for line in raw.splitlines():
            if line.startswith("data:") and line.strip() != "data:":
                d = json.loads(line[5:].strip())
                if "result" in d:
                    for c in d["result"].get("content", []):
                        if c.get("type") == "text":
                            obj = json.loads(c["text"])
                            cash = obj.get("data", {}).get("portfolio", {}).get("availableCash")
                            return float(cash) if cash is not None else None
    except Exception:
        return None
    return None


QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
SCAN = "data/lowconsol_signal_latest.json"
OUT = "data/batch_battle_plan_latest.json"
COOLDOWN = 10
STOP_PCT = 0.08
LIMIT_GAP = 1.01      # 限价=收盘x1.01
STOP_MULT = 0.92      # -8% 止损铁律
STOP_PCT_FULL = 0.08  # 赔率分母=止损距离8%(与铁律一致)
HOLD_BARS = 20        # t20e 持有窗口


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--budget", type=float, default=72000.0)
    ap.add_argument("--max-picks", type=int, default=12)
    ap.add_argument("--max-per-industry", type=int, default=4)
    ap.add_argument("--exclude", default="")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--weighting", choices=["eq", "inv_vol"], default="inv_vol",
                    help="定权: eq=等权(等风险, 止损距离统一8%%) | inv_vol=逆波动率(20261025测试双窗小幅占优)")
    ap.add_argument("--auto-exclude", action="store_true",
                    help="自动排除 westock 当前持仓(实时MCP只读)")
    ap.add_argument("--auto-budget", action="store_true",
                    help="预算自动=可用现金/3(实时MCP只读, 失败回退--budget值)")
    ap.add_argument("--highwin", action="store_true",
                    help="大概率cohort模式: 市场前置20日<=-5%时优先选vol20>0.45候选"
                         "(winrate_frontier_20261026: train 63.7%/test 71.5% 双窗稳健)")
    ap.add_argument("--sector-momentum", action="store_true",
                    help="行业轮动过滤: 只选当日行业涨幅>大盘的板块内股票(20261026复盘教训)")
    ap.add_argument("--require-stabilize", action="store_true",
                    help="市场企稳闸: 当日大盘等权收益<=0时暂停买入(防接落刀, 20261026教训)")
    ap.add_argument("--min60-gate", action="store_true",
                    help="60分钟确认闸: 死叉剔除, 金叉/中性/无数据放行留痕(owner=timeframe_60min_20261029, 与revival扫描器同语义)")
    args = ap.parse_args()

    # ---- westock 实时联动(只读): 持仓排除 + 预算 + 50只上限 ----
    n_pos = None
    if args.auto_exclude or args.auto_budget:
        pos = _fetch_positions()
        if pos is not None:
            n_pos = len(pos)
            if args.auto_exclude:
                excluded_auto = {p["code"][-6:] for p in pos}
                args.exclude = ",".join(sorted(set(args.exclude.split(",")) | excluded_auto - {""})) if args.exclude else ",".join(sorted(excluded_auto))
        else:
            print("[WARN] westock持仓拉取失败, auto-exclude降级为仅手动--exclude清单")
    if args.auto_budget:
        cash = _fetch_available_cash()
        if cash:
            args.budget = round(cash / 3.0, -2)
            print(f"[auto-budget] 可用现金={cash:,.0f} → 批次预算={args.budget:,.0f}")
        else:
            print("[WARN] 可用现金拉取失败, 沿用--budget")
    if n_pos is not None and n_pos + args.max_picks > 50:
        args.max_picks = max(6, 50 - n_pos)
        print(f"[50上限] 现持仓{n_pos}只 → 本批裁至{args.max_picks}只")

    scan = json.load(open(SCAN, encoding="utf-8"))
    scan_date = scan["scan_date"]
    excluded = {c[-6:] for c in args.exclude.split(",") if c.strip()}

    # ---- 候选装配: 主线命中(排序取) + 背景池(种子随机) ----
    mh = [r for r in scan["main_hit_list"] if r["code"][-6:] not in excluded]
    bg_all = [r for r in scan.get("background_pool_full", [])
              if not r.get("star") and r["code"][-6:] not in excluded]

    # ---- 大概率cohort(winrate_frontier_20261026): m20<=-5%时优先vol20>0.45 ----
    highwin_note = "OFF"
    m20_val = None
    if args.highwin:
        from datetime import datetime as _dt, timedelta as _td
        _d130 = (_dt.strptime(str(int(scan_date)), "%Y%m%d") - _td(days=130)).strftime("%Y%m%d")
        vcon = sqlite3.connect(QFQ, uri=True)
        vw = pd.read_sql_query(
            f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='{_d130}'",
            vcon)
        md = vw.groupby("date").agg(m=("close", "mean")).sort_index()
        m20 = float(md["m"].iloc[-1] / md["m"].iloc[-21] - 1) if len(md) >= 21 else np.nan
        m20_val = round(m20, 4) if np.isfinite(m20) else None
        vw["code6"] = vw["code"].str[-6:]
        vol_all = vw.sort_values("date").groupby("code6")["close"].apply(
            lambda s: s.pct_change().iloc[-20:].std() * np.sqrt(250) if len(s) >= 21 else np.nan)
        vcon.close()
        if np.isfinite(m20) and m20 <= -0.05:
            hv_mh = [r for r in mh if (vol_all.get(r["code"][-6:]) or 0) > 0.45]
            hv_bg = [r for r in bg_all if (vol_all.get(r["code"][-6:]) or 0) > 0.45]
            if len(hv_mh) + len(hv_bg) >= args.max_picks:
                mh, bg_all = hv_mh, hv_bg
                highwin_note = f"ON(m20={m20:+.1%}<=-5%, 高波候选mh={len(hv_mh)}/bg={len(hv_bg)})"
            else:
                highwin_note = f"DEGRADED(m20={m20:+.1%}但高波候选不足{args.max_picks}, 维持全池)"
        else:
            highwin_note = f"OFF(m20={m20:+.1%}>-5%, 非深跌窗)"

    # ---- 市场企稳闸(20261026教训: m20深跌但当日仍跌=接落刀) ----
    stabilize_note = "OFF"
    if args.require_stabilize:
        vcon = sqlite3.connect(QFQ, uri=True)
        _d30 = (_dt.strptime(str(int(scan_date)), "%Y%m%d") - _td(days=30)).strftime("%Y%m%d")
        vw2 = pd.read_sql_query(
            f"SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE date>='{_d30}'",
            vcon)
        vcon.close()
        md2 = vw2.groupby("date").agg(m=("close", "mean")).sort_index()
        if len(md2) >= 2:
            mkt_today = float(md2["m"].iloc[-1] / md2["m"].iloc[-2] - 1)
            if mkt_today <= 0:
                print(f"[FATAL] 市场企稳闸: 当日等权收益{mkt_today:+.2%}<=0 → 市场仍在跌, 暂停买入")
                return 2
            stabilize_note = f"ON(当日{mkt_today:+.2%}>0, 企稳确认)"
        else:
            stabilize_note = "no_data"

    # ---- 行业轮动过滤器(20261026教训: 军工/通信集中=当日最弱板块) ----
    sector_note = "OFF"
    kline_note = "OFF"
    min60_note = "OFF"
    if args.sector_momentum:
        # 计算各行业20日动量+当日表现 → 构建行业白名单
        icon = sqlite3.connect("data/industry_group_map_snapshot.db")
        imap = pd.read_sql_query("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0", icon)
        icon.close()
        imap["code6"] = imap["code"].astype(str).str[-6:]
        l1_map2 = dict(zip(imap["code6"], imap["l1"]))

        vcon = sqlite3.connect(QFQ, uri=True)
        _d60 = (_dt.strptime(str(int(scan_date)), "%Y%m%d") - _td(days=60)).strftime("%Y%m%d")
        vw3 = pd.read_sql_query(
            f"SELECT code,date,close,volume FROM stock_daily_meta_qfq WHERE date>='{_d60}'",
            vcon)
        vcon.close()
        vw3["code6"] = vw3["code"].str[-6:]
        vw3["l1"] = vw3["code6"].map(l1_map2)
        vw3 = vw3.dropna(subset=["l1"])
        # 行业等权收益(20日动量)
        ind_ret = vw3.groupby(["l1", "date"])["close"].mean().reset_index()
        ind_piv = ind_ret.pivot(index="date", columns="l1", values="close").sort_index()
        ind_mom20 = ind_piv.iloc[-1] / ind_piv.iloc[-21] - 1 if len(ind_piv) >= 21 else None
        # 行业当日涨跌
        ind_today = ind_piv.iloc[-1] / ind_piv.iloc[-2] - 1 if len(ind_piv) >= 2 else None

        if ind_mom20 is not None and ind_today is not None:
            # 全市场当日均值
            mkt_avg = float(ind_today.mean())
            # 白名单: 当日跑赢大盘的行业
            whitelist = set(ind_today[ind_today > mkt_avg].index)
            # 黑名单: 20日跌幅最深的后20%行业(除非当日反弹)
            worst_20pct = ind_mom20.nsmallest(max(1, int(len(ind_mom20) * 0.2))).index
            blacklist = {s for s in worst_20pct if s not in whitelist}
            # 应用过滤
            mh_before, bg_before = len(mh), len(bg_all)
            mh = [r for r in mh if r.get("l1", "") in whitelist or not r.get("l1")]
            bg_all = [r for r in bg_all if l1_map2.get(r["code"][-6:], "") in whitelist]
            sector_note = (f"ON(白名单{len(whitelist)}行业: "
                          f"{', '.join(sorted(whitelist)[:5])}... | "
                          f"过滤mh {mh_before}→{len(mh)}, bg {bg_before}→{len(bg_all)})")
            if len(mh) + len(bg_all) < args.max_picks:
                # 白名单不够用, 补充中性行业
                neutral = set(ind_today[abs(ind_today - mkt_avg) < 0.005].index) - blacklist
                mh_extra = [r for r in scan["main_hit_list"]
                           if r["code"][-6:] not in excluded and r.get("l1", "") in neutral
                           and r not in mh]
                bg_extra = [r for r in scan.get("background_pool_full", [])
                           if not r.get("star") and r["code"][-6:] not in excluded
                           and l1_map2.get(r["code"][-6:], "") in neutral]
                mh.extend(mh_extra[:args.max_picks])
                bg_all.extend(bg_extra[:args.max_picks * 2])
                sector_note += f" → 补中性行业后mh={len(mh)}, bg={len(bg_all)}"
        else:
            sector_note = "OFF(行业数据不足)"

    # ---- K线形态过滤(20261029主人令「选股为什么没用上K线」: 剔除大阴线/上影线过长/十字星) ----
    if args.sector_momentum:  # 与行业轮动联用
        kline_bad = []  # 剔除: 大阴线/十字星/上影线过长
        kline_good = []  # 优选: 大阳线/放量阳线
        mh_kl, bg_kl = [], []
        kline_codes = {r["code"][-6:] for r in mh} | {r["code"][-6:] for r in bg_all}
        from analysis.kline_pattern_module import calc_kline_features, calc_volume_pattern
        for r in mh + bg_all:
            c6 = r["code"][-6:]
            if c6 not in kline_codes:
                continue
            # 从qfq取最新3日OHLC
            vcon = sqlite3.connect(QFQ, uri=True)
            ohlc = pd.read_sql_query(
                f"SELECT date,open,high,low,close,volume FROM stock_daily_meta_qfq "
                f"WHERE code LIKE '%{c6}' ORDER BY date DESC LIMIT 6", vcon)
            vcon.close()
            if len(ohlc) < 6:
                mh_kl.append(r); bg_kl.append(r); continue
            ohlc = ohlc.sort_values("date", ascending=False).reset_index(drop=True)
            o, h, l, cl = float(ohlc.iloc[0]["open"]), float(ohlc.iloc[0]["high"]), \
                          float(ohlc.iloc[0]["low"]), float(ohlc.iloc[0]["close"])
            prev_vol5 = float(ohlc.iloc[1:6]["volume"].mean())
            kl = calc_kline_features(o, h, l, cl)
            vp = calc_volume_pattern(float(ohlc.iloc[0]["volume"]), prev_vol5)
            # 剔除: 大阴线 或 十字星 或 上影线过长
            if kl["pattern"] in ("大阴线", "十字星") or kl["upper_warn"]:
                kline_bad.append(f"{c6}({kl['pattern']})")
            else:
                mh_kl.append(r); bg_kl.append(r)
                if kl["pattern"] == "大阳线" and vp["vol_tag"] == "放量":
                    kline_good.append(c6)
        if kline_bad:
            mh, bg_all = mh_kl, bg_kl
            kline_note = (f"ON(剔除{kline_bad.__len__()}只大阴线/十字星/上影过长: "
                         f"{', '.join(kline_bad[:3])}... | "
                         f"优选{kline_good.__len__()}只大阳线放量)")
        else:
            kline_note = "ON(无大阴线候选)"

    # ---- 60分钟确认闸(20261030主人令「选股要用上日线和60分钟线」) ----
    # 死叉剔除; 金叉/中性/无数据放行留痕。owner=timeframe_60min_20261029(与revival扫描器同语义)。
    if args.min60_gate:
        from analysis.timeframe_60min_20261029 import batch_60min_status_auto as _tf60_batch
        vcon60 = sqlite3.connect(QFQ, uri=True)
        # 20261030 深夜勘误: DESC直传=倒放序列(c[-1]=最旧日), MA交叉全错(鲁阳节能8.41vs日线7.74暴露);
        # 模块按传入顺序拼接, 必须升序。取近8日再sorted升序。
        dates60 = sorted(int(r[0]) for r in vcon60.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date DESC LIMIT 8"))
        vcon60.close()
        seen60 = []
        for r in mh + bg_all:
            c6 = r["code"][-6:]
            if c6 not in seen60:
                seen60.append(c6)
        st60 = _tf60_batch(seen60, dates60)
        mh_before60, bg_before60 = len(mh), len(bg_all)
        mh = [r for r in mh if st60.get(r["code"][-6:], {}).get("trend") != "死叉"]
        bg_all = [r for r in bg_all if st60.get(r["code"][-6:], {}).get("trend") != "死叉"]
        _v = [st60.get(r["code"][-6:], {}).get("trend", "无数据") for r in mh + bg_all]
        min60_note = (f"ON(候选{mh_before60 + bg_before60}只→保留{len(_v)}只: "
                      f"金叉{_v.count('金叉')}/中性{_v.count('中性')}/"
                      f"无数据{_v.count('无数据')} | 死叉剔除{mh_before60 + bg_before60 - len(_v)}只)")

    rng = np.random.default_rng(args.seed)
    bg_idx = rng.permutation(len(bg_all))

    picked, ind_cnt = [], {}
    for r in sorted(mh, key=lambda x: (-x.get("streak", 0), x["close"])):
        if len(picked) >= args.max_picks:
            break
        l1 = r.get("l1") or "?"
        if ind_cnt.get(l1, 0) >= args.max_per_industry:
            continue
        r["_pool"] = "main_hit"
        picked.append(r)
        ind_cnt[l1] = ind_cnt.get(l1, 0) + 1
    for i in bg_idx:
        if len(picked) >= args.max_picks:
            break
        r = dict(bg_all[i])
        l1 = r.get("l1") or "?"
        if ind_cnt.get(l1, 0) >= args.max_per_industry:
            continue
        r["_pool"] = "background"
        picked.append(r)
        ind_cnt[l1] = ind_cnt.get(l1, 0) + 1

    codes = [r["code"] for r in picked]
    if not codes:
        print("[FATAL] 无可装配候选"); return 2

    # ---- 未来交易日历: qfq实际日期优先(扫描日之后已入库的), 不足则工作日近似(±节假日, 10-11月无长假=精确) ----
    con = sqlite3.connect(QFQ, uri=True)
    ph = ",".join(f"'{c}'" for c in codes)
    px = pd.read_sql_query(
        f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE code IN ({ph}) "
        f"AND date>='{int(scan_date)-20000}' ORDER BY code,date", con)
    known = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>? ORDER BY date LIMIT 40",
        (int(scan_date),)).fetchall()]
    con.close()
    cal = list(known)
    nxt = pd.Timestamp(str(int(scan_date)))
    while len(cal) < HOLD_BARS:
        nxt += pd.Timedelta(days=1)
        if nxt.weekday() < 5:
            d = int(nxt.strftime("%Y%m%d"))
            if d not in cal:
                cal.append(d)
    px["code6"] = px["code"].str[-6:]
    ma20 = px.groupby("code6")["close"].apply(lambda s: s.rolling(20, min_periods=20).mean().iloc[-1])
    last_close = px.groupby("code6")["close"].last()
    # 空间指标(20261026主人框架采纳): 距250日高点空间 + 赔率=空间/止损距离8%
    mx250 = px.groupby("code6")["close"].apply(lambda s: s.rolling(250, min_periods=120).max().iloc[-1])
    # 波动率定权数据: 信号日滞后20日已实现波动(年化) — inv_vol案用, 20261025测试双窗一致小幅占优
    vol20 = px.sort_values("date").groupby("code6")["close"].apply(
        lambda s: s.pct_change().rolling(20, min_periods=20).std().shift(1).iloc[-1] * np.sqrt(250))
    entry_date, exit_date = cal[0], cal[HOLD_BARS - 1]  # 入场日=第1根, 出场=第20根收盘

    # ---- 单票计划: 定权(eq=等权 | inv_vol=逆波动率, 缺波动率票回退等权) ----
    vols = {}
    for r in picked:
        c6 = r["code"][-6:]
        v = float(vol20.get(c6, np.nan))
        vols[c6] = v if np.isfinite(v) and v > 0 else None
    valid = [c for c in vols if vols[c]]
    if args.weighting == "inv_vol" and len(valid) >= len(picked) // 2:
        base = {c: (1.0 / vols[c] if vols[c] else float(np.nanmean([1.0 / v for v in vols.values() if v]))) for c in vols}
        tot = sum(base.values())
        weight = {c: base[c] / tot for c in vols}
    else:
        weight = {c: 1.0 / len(picked) for c in vols}
    rows, cmds = [], []
    for r in picked:
        c6 = r["code"][-6:]
        cl = float(last_close.get(c6, np.nan))
        if not np.isfinite(cl) or cl <= 0:
            continue
        limit = round(cl * LIMIT_GAP, 2)
        per = args.budget * weight[c6]
        shares = int(per / limit / 100) * 100
        if shares < 100:
            continue
        stop = round(limit * STOP_MULT, 2)
        m250 = float(mx250.get(c6, np.nan))
        upside = (m250 / cl - 1.0) if np.isfinite(m250) and m250 > cl else np.nan
        odds = round(upside / STOP_PCT_FULL, 1) if np.isfinite(upside) else None
        r42 = check_r42_goodwill_high(r["code"])
        r43 = check_r43_block_trade_anomaly(r["code"])
        flags = [k for k, v in (("R42", r42), ("R43", r43)) if v.get("status") == "warn"]
        row = {"code": r["code"], "name": r["name"], "l1": r.get("l1"), "pool": r["_pool"],
               "streak": r.get("streak"), "close": cl, "ma20": round(float(ma20.get(c6, np.nan)), 2),
               "upside_to_250d_high": round(upside, 3) if np.isfinite(upside) else None,
               "odds_vs_stop": odds,
               "vol20": round(vols.get(c6) or float("nan"), 3), "weight": round(weight[c6], 4),
               "limit": limit, "shares": shares, "amount": round(limit * shares, 0),
               "stop": stop, "entry_date": int(entry_date), "exit_date": int(exit_date),
               "mine_flags": flags, "r42": r42["status"], "r43": r43["status"]}
        rows.append(row)
        cmds.append(f"python trade_executor.py buy --code {r['code']} --name {r['name']} "
                    f"--volume {shares} --price {limit} --source lowconsol_batch "
                    f"--sector {r.get('l1','')}")

    out = {"generated": pd.Timestamp.now().strftime("%Y-%m-%d %H:%M"), "scan_date": scan_date,
           "budget": args.budget, "max_picks": args.max_picks, "max_per_industry": args.max_per_industry,
           "weighting": args.weighting, "highwin_cohort": highwin_note,
           "min60_gate": min60_note,
           "m20_at_scan": m20_val,  # 相位聚集诊断锚(主人验证协议阶段四: 连亏按相位归因)
           "excluded_codes": sorted(excluded), "seed": args.seed,
           "entry_date": int(entry_date), "exit_date": int(exit_date),
           "phase_switch": scan.get("phase_switch"),
           "evidence": {"选股": "低位横盘A格 OOS_CONFIRMED +2.75~4.55pp",
                        "止损": "-8%铁律 t20e超额+5.46pp(vs无止损+2.81pp)",
                        "持有": "20交易日窗口(右偏分布吃右尾)",
                        "禁加仓": "金字塔已证伪(20261025, 胜率-3.5pp/均值-0.42pp)",
                        "零交易权": "本计划为提请件, 执行须主人逐笔令+executor --execute"},
           "plan": rows, "executor_commands": cmds}
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 同步产出 buy-batch 执行文件(周一09:31 BatchExec_Mon0931消费, --source=lowconsol_batch已登记)
    exec_plan = [{"code": r["code"], "name": r["name"], "volume": int(r["shares"]),
                  "price": r["limit"], "limit_price": r["limit"],  # 20261031补: R1b熔断/O1截价输入(无此字段则熔断恒不触发, W23教训)
                  "source": "lowconsol_batch",
                  "sector": r["l1"], "prev_close": r["close"]} for r in rows]
    json.dump(exec_plan, open("data/batch_exec_plan_next.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    # 归档: batch_exit_monitor.py 消费(同scan_date覆盖=幂等最新)
    import os
    os.makedirs("data/batch_plan_archive", exist_ok=True)
    json.dump(out, open(f"data/batch_plan_archive/batch_plan_{scan_date}.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)

    print(f"[批次计划] 基于扫描{scan_date} | 预算{args.budget:,.0f}元/{len(rows)}只 每只约{per:,.0f}元")
    print(f"[大概率cohort] {highwin_note}")
    print(f"[行业轮动] {sector_note}")
    print(f"[市场企稳] {stabilize_note}")
    print(f"[K线形态] {kline_note}")
    print(f"[60分钟闸] {min60_note}")
    print(f"[窗口] 入场={entry_date}(次日开盘,限价<=收盘+1%) | 出场={exit_date}(第20交易日收盘) | 止损=限价x0.92")
    print(f"[行业上限] 每L1最多{args.max_per_industry}只 | 主线命中{sum(1 for x in rows if x['pool']=='main_hit')}只+背景池{sum(1 for x in rows if x['pool']=='background')}只")
    df = pd.DataFrame(rows)
    print(df[["code", "name", "l1", "pool", "close", "vol20", "weight", "limit", "shares", "amount", "stop", "mine_flags"]].to_string(index=False))
    flagged = [x for x in rows if x["mine_flags"]]
    if flagged:
        print(f"\n[排雷标注] {len(flagged)}只带WARN(呈报不剔除, 主人裁决): " +
              ", ".join(f"{x['name']}({'+'.join(x['mine_flags'])})" for x in flagged))
    print(f"\n[执行] 以下命令为dry-run模板, 主人逐笔批准后追加 --execute:")
    for c in cmds[:3]:
        print(f"  {c}")
    if len(cmds) > 3:
        print(f"  ... 共{len(cmds)}条, 全量见 {OUT}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
