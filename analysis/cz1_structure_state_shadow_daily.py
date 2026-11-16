# -*- coding: utf-8 -*-
"""CZ1_STRUCTURE_STATE_SHADOW 资金结构三态影子日更(只核结构层候选C-Z1, 观测层零交易权)
对应 PREREG_CZ1_FUND_STRUCTURE_STATE_SHADOW_20261008, 主人2026-10-08「按推荐继续」批准激活影子层。
分类逻辑与初验 tmp/cz1_fund_structure_state_20261007.py 同源(透明瀑布)。
🔴口径主动变更(初验报告§四①/§五.1要求): 阈值从全样本三分位改为滚动252日分位,
  阈值窗口=截至T-1日(shift(1), 当日值不进自身阈值窗口)=零前视;
  因此历史分布与初验(全样本口径)必然漂移, 方向对照(抱团态f5应仍最高)在快照如实呈现。
参数单一真源=data/PREREG_CZ1_FUND_STRUCTURE_STATE_SHADOW_20261008.json params字段
  (缺字段fail-closed退出, 禁止回退硬编码)。
数据源: ths_sector.db(概念板块K线) + fundamental_data.db(northbound_daily.hs300_close), 均只读。
幂等: 全史重算+台账原子覆盖(写tmp后os.replace), 重跑安全。
台账两类行: type=daily_state(每日状态+阈值) / type=event(爆发日事件×态, f5到期标记)。
OOS影子层=event_date>=activation_since 且 f5到期; 之前=样内参考层(滚动口径)。
三门中间读数(仅记录, 判定权=影子末主人裁决):
  G1=抱团态OOS f5中位>=+1.0pp 且 win>=55%;
  G2=非抱团态(趋势/轮动/过渡合计)f5中位<=抱团态中位-0.5pp(判别差>=0.5pp);
  G3=抱团态OOS到期样本n>=30。
kill监视: 抱团态OOS按时间序前20个到期样本med<0 → 呈请废案; 影子窗结束n=0 → 呈请废案。
边界: 纯观测影子层零交易权, 禁止接闸门/交易cron/executor;
板块指数收益≠可交易收益; 结构表征≠资金归属; 信号权=主人。

用法:
  python analysis/cz1_structure_state_shadow_daily.py            # 全史重算+台账原子覆盖+快照
  python analysis/cz1_structure_state_shadow_daily.py --report   # 同上并打印简报
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

sys.stdout.reconfigure(encoding="utf-8")

PREREG = Path(r"D:\zcodesj\data\PREREG_CZ1_FUND_STRUCTURE_STATE_SHADOW_20261008.json")
THS_DB = r"D:\zcodesj\data\ths_sector.db"
FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
LEDGER = Path(r"D:\zcodesj\data\cz1_structure_state_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\cz1_structure_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\cz1_structure_state_brief_latest.txt")
PORTRAIT_BRIEF_GLOB = r"D:\zcodesj\data\universe_portrait_brief_*.json"
STATES = ["趋势态", "抱团态", "轮动态", "过渡态"]


def load_prereg() -> dict:
    """参数单一真源: prereg params 字段; 缺字段fail-closed。"""
    p = json.loads(PREREG.read_text(encoding="utf-8"))
    if p.get("status") != "ACTIVE":
        raise SystemExit(f"[FAIL-CLOSED] prereg status={p.get('status')} 非ACTIVE")
    pa = p.get("params") or {}
    need = ["churn_rank_span", "disp_span", "roll_window", "disp_hi_q", "churn_lo_q",
            "churn_hi_q", "burst_pct_min", "min_boards", "fwd_horizons", "hs300_ma_span"]
    for k in need:
        if k not in pa:
            raise SystemExit(f"[FAIL-CLOSED] prereg params 缺字段 {k}")
    if not pa["fwd_horizons"]:
        raise SystemExit("[FAIL-CLOSED] prereg fwd_horizons 为空")
    since = int(json.loads(p["approval"].replace("'", '"'))["activation_since"])
    acc = json.loads(p["acceptance_locked"].replace("'", '"'))
    return {"pa": pa, "since": since, "acc": acc,
            "name": p.get("name"), "status": p.get("status")}


def build(cfg: dict) -> dict:
    pa, since = cfg["pa"], cfg["since"]
    RS = int(pa["churn_rank_span"])       # churn 排名跨度
    DS = int(pa["disp_span"])             # disp 跨度
    RW = int(pa["roll_window"])           # 滚动分位窗口
    Q_HI = float(pa["disp_hi_q"])
    Q_LO = float(pa["churn_lo_q"])
    Q_HI2 = float(pa["churn_hi_q"])
    BURST = float(pa["burst_pct_min"])
    MINB = int(pa["min_boards"])
    H = int(pa["fwd_horizons"][0])
    MA60 = int(pa["hs300_ma_span"])

    # ---- 概念板块矩阵 ----
    ct = sqlite3.connect(f"file:{THS_DB}?mode=ro", uri=True)
    boards = pd.read_sql("select ths_code, ths_name from ths_boards where module='concept'", ct)
    df = pd.read_sql("select ths_code, trade_date, close from ths_kline", ct)
    ct.close()
    df = df[df.ths_code.isin(set(boards.ths_code))].copy()
    df["d"] = df.trade_date.str.replace("-", "")
    px = df.pivot_table(index="d", columns="ths_code", values="close").sort_index()
    px = px.dropna(how="all")
    bname = dict(zip(boards.ths_code, boards.ths_name))

    bpct = px.pct_change() * 100
    ret20 = px.pct_change(DS) * 100
    disp = ret20.max(axis=1) - ret20.median(axis=1)

    # churn: 5日收益排名跨窗口 spearman(与初验逐位同源)
    r5 = bpct.rolling(RS).sum()
    dates = px.index.tolist()
    churn = {}
    for i in range(RS * 2, len(dates)):
        a = r5.iloc[i].rank()
        b = r5.iloc[i - RS].rank()
        m = a.notna() & b.notna()
        if m.sum() > MINB:
            churn[dates[i]] = 1 - spearmanr(a[m], b[m]).statistic
    churn = pd.Series(churn).sort_index()

    # ---- hs300 ----
    cf = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    hs = pd.read_sql(
        "select trade_date, hs300_close from northbound_daily where hs300_close is not null "
        "and trade_date>='20200101'", cf).set_index("trade_date")["hs300_close"]
    cf.close()
    hs.index = hs.index.astype(str)
    hsr20 = hs.pct_change(DS) * 100
    above60 = hs > hs.rolling(MA60).mean()

    # ---- 滚动分位阈值(截至T-1日, 零前视) ----
    disp_hi = disp.shift(1).rolling(RW, min_periods=RW).quantile(Q_HI)
    churn_lo = churn.shift(1).rolling(RW, min_periods=RW).quantile(Q_LO)
    churn_hi = churn.shift(1).rolling(RW, min_periods=RW).quantile(Q_HI2)

    frame = pd.DataFrame({"disp": disp, "churn": churn}).dropna()
    frame["disp_hi"] = disp_hi.reindex(frame.index)
    frame["churn_lo"] = churn_lo.reindex(frame.index)
    frame["churn_hi"] = churn_hi.reindex(frame.index)
    frame["hsr20"] = hsr20.reindex(frame.index)
    frame["above60"] = above60.reindex(frame.index)

    def state(r):
        if pd.notna(r.hsr20) and r.hsr20 > 0 and r.above60:
            return "趋势态"
        if pd.notna(r.disp_hi) and r.disp >= r.disp_hi and r.churn <= r.churn_lo:
            return "抱团态"
        if pd.notna(r.churn_hi) and r.churn >= r.churn_hi:
            return "轮动态"
        if pd.notna(r.disp_hi):
            return "过渡态"
        return "undetermined"

    frame["state"] = frame.apply(state, axis=1)

    # ---- 爆发日事件(板块当日>=BURST%, 收盘判定) ----
    f5 = px.shift(-H) / px - 1
    events = []  # (d, board, ret1d, f5)
    for d in bpct.index:
        if d not in frame.index:
            continue
        row = bpct.loc[d]
        for b in row[row >= BURST].index:
            v = f5.at[d, b] if d in f5.index else np.nan
            events.append((d, b, row[b], v * 100 if pd.notna(v) else None))
    ev = pd.DataFrame(events, columns=["date", "board", "ret1d", "f5"])
    ev["state"] = ev.date.map(frame.state)
    ev["matured"] = ev.f5.notna()
    ev["in_window"] = ev.date.astype(int) >= since
    ev["oos"] = ev.matured & ev.in_window
    ev["year"] = ev.date.str[:4]
    ev["board_name"] = ev.board.map(bname)

    # ---- 三门中间读数(仅记录) ----
    bull = ev[ev.oos & (ev.state == "抱团态")]
    nonbull = ev[ev.oos & ev.state.isin([s for s in STATES if s != "抱团态"])]
    g1_med = float(bull.f5.median()) if len(bull) else None
    g1_win = float((bull.f5 > 0).mean()) if len(bull) else None
    nb_med = float(nonbull.f5.median()) if len(nonbull) else None
    g2_gap = (g1_med - nb_med) if (g1_med is not None and nb_med is not None) else None
    g3_n = int(len(bull))
    kill20 = float(bull.sort_values("date").head(20).f5.median()) if len(bull) else None

    # 状态×结局全史参考层(滚动口径, 含OOS前)——方向对照
    ref = {}
    for st in STATES + ["undetermined"]:
        s = ev[ev.state == st]
        sm = s[s.matured]
        ref[st] = {"n_events": int(len(s)), "n_matured": int(len(sm)),
                   "f5_med": (round(float(sm.f5.median()), 2) if len(sm) >= 10 else None),
                   "f5_win": (round(float((sm.f5 > 0).mean()), 3) if len(sm) >= 10 else None)}

    return {"frame": frame, "ev": ev, "ref": ref,
            "gates": {"g1_med": g1_med, "g1_win": g1_win, "g2_gap": g2_gap,
                      "g3_n": g3_n, "nonbull_med": nb_med, "kill20_med": kill20},
            "asof": frame.index[-1], "H": H}


def write_ledger(res: dict) -> None:
    """全史重算+台账原子覆盖(最强幂等)。"""
    frame, ev = res["frame"], res["ev"]
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for d in frame.index:
            row = {
                "type": "daily_state", "date": d,
                "state": frame.at[d, "state"],
                "churn": (round(float(frame.at[d, "churn"]), 4)
                          if pd.notna(frame.at[d, "churn"]) else None),
                "disp": (round(float(frame.at[d, "disp"]), 2)
                         if pd.notna(frame.at[d, "disp"]) else None),
                "disp_hi": (round(float(frame.at[d, "disp_hi"]), 2)
                            if pd.notna(frame.at[d, "disp_hi"]) else None),
                "churn_lo": (round(float(frame.at[d, "churn_lo"]), 4)
                             if pd.notna(frame.at[d, "churn_lo"]) else None),
                "churn_hi": (round(float(frame.at[d, "churn_hi"]), 4)
                             if pd.notna(frame.at[d, "churn_hi"]) else None),
                "hsr20": (round(float(frame.at[d, "hsr20"]), 2)
                          if pd.notna(frame.at[d, "hsr20"]) else None),
                "above60": (bool(frame.at[d, "above60"])
                            if pd.notna(frame.at[d, "above60"]) else None),
            }
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
        for _, r in ev.iterrows():
            row = {"type": "event", "date": r.date, "board": r.board,
                   "board_name": r.board_name, "ret1d": round(float(r.ret1d), 2),
                   "f5": (round(float(r.f5), 2) if pd.notna(r.f5) else None),
                   "state": r.state, "matured": bool(r.matured),
                   "in_window": bool(r.in_window), "oos": bool(r.oos)}
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(tmp, LEDGER)


def read_gate_ac() -> dict:
    """画像门A/C对照(只读, 缺失=N/A, 仅记录不门控)。"""
    try:
        files = sorted(Path(r"D:\zcodesj\data").glob("universe_portrait_brief_*.json"))
        if not files:
            return {"gateA": "N/A", "gateC": "N/A", "src": "无画像brief"}
        j = json.loads(files[-1].read_text(encoding="utf-8"))
        return {"gateA": j.get("gateA", "N/A"), "gateC": j.get("gateC", "N/A"),
                "src": files[-1].name}
    except Exception as e:  # noqa: BLE001 — 对照层缺失允许降级为N/A
        return {"gateA": "N/A", "gateC": "N/A", "src": f"读取失败:{e}"}


def write_snapshot_brief(res: dict, cfg: dict, ref_rank: str) -> dict:
    pa, since, acc = cfg["pa"], cfg["since"], cfg["acc"]
    frame, ev = res["frame"], res["ev"]
    g = res["gates"]
    asof = res["asof"]
    last60 = frame.index[-60:]
    dist60 = frame.loc[last60, "state"].value_counts().to_dict()
    dist_all = frame.state.value_counts().to_dict()
    n_since_days = int((frame.index >= str(since)).sum())
    oos_open = int((ev.in_window & ~ev.matured).sum())
    gate_ac = read_gate_ac()

    snap = {
        "version": "cz1_structure_state_shadow_v1", "asof": asof,
        "prereg": cfg["name"], "activation_since": since,
        "shadow_day": f"{n_since_days}/{pa['window_days']}",
        "state_dist_all": dist_all, "state_dist_last60": dist60,
        "events": {"n_all": int(len(ev)), "n_oos_matured": int(ev.oos.sum()),
                   "n_oos_open": oos_open, "by_state_ref": res["ref"]},
        "gates_reading": g, "gate_ac_ref": gate_ac,
        "direction_check": ref_rank,
        "boundary": "零交易权纯观测; 板块指数收益非可交易收益; 判定权=主人",
    }
    SNAPSHOT.write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")

    cur = frame.iloc[-1]
    lines = [
        f"[CZ1资金结构态影子] asof={asof} (影子第{n_since_days}/{pa['window_days']}交易日)",
        f"今日结构态: {cur.state} (disp={cur.disp:.1f}pp 阈值{cur.disp_hi if pd.notna(cur.disp_hi) else 'NA'} | "
        f"churn={cur.churn:.3f} 阈值[{cur.churn_lo if pd.notna(cur.churn_lo) else 'NA'},"
        f"{cur.churn_hi if pd.notna(cur.churn_hi) else 'NA'}])",
        f"近60日态分布: {dist60}",
        f"OOS三门读数(仅记录,判定权=影子末主人): G1抱团f5 med="
        f"{g['g1_med'] if g['g1_med'] is not None else 'NA'}% win="
        f"{g['g1_win'] if g['g1_win'] is not None else 'NA'} (门线+1.0/55) | "
        f"G2判别差={g['g2_gap'] if g['g2_gap'] is not None else 'NA'}pp (门线>=0.5) | "
        f"G3 n={g['g3_n']} (门线>=30)",
        f"kill监视: OOS抱团前20个med={g['kill20_med'] if g['kill20_med'] is not None else 'NA'} "
        f"(kill线<0)",
        f"方向对照: {ref_rank} (初验=抱团态f5最高)",
        f"当日画像门A/C对照: {gate_ac['gateA']}/{gate_ac['gateC']} ({gate_ac['src']})",
        "边界: 零交易权纯观测; 板块指数收益非可交易收益; 结构表征非资金归属; 信号权=主人",
    ]
    BRIEF.write_text("\n".join(lines), encoding="utf-8")
    return snap


def main() -> None:
    report = "--report" in sys.argv
    cfg = load_prereg()
    res = build(cfg)
    write_ledger(res)
    ref = res["ref"]
    meds = {st: ref[st]["f5_med"] for st in STATES if ref[st]["f5_med"] is not None}
    if meds:
        top = max(meds, key=meds.get)
        rank = f"抱团态f5排名={'第1' if top == '抱团态' else '非第1(' + top + '最高)'} med对照={meds}"
    else:
        rank = "成熟样本不足, 方向对照暂不可判"
    snap = write_snapshot_brief(res, cfg, rank)
    if report:
        print(json.dumps(snap, ensure_ascii=False, indent=1))
    print(f"[cz1] OK asof={snap['asof']} 台账={LEDGER} 事件n={snap['events']['n_all']} "
          f"OOS成熟={snap['events']['n_oos_matured']}")


if __name__ == "__main__":
    main()
