# -*- coding: utf-8 -*-
"""PRELAUNCH_EMBRYO_SHADOW 胚胎域影子日更(观测层零交易权)
对应 data/PREREG_PRELAUNCH_EMBRYO_SHADOW_DRAFT_20261019.json(文件名含DRAFT为创建日锚,
状态以status字段为准)。🔴20261019撤回: prereg终态=TERMINATED_PRE_ACTIVATION——实证研究前视缺陷(卷积切片算出未来板数)致胚胎域正增量为伪影, 激活在挂链前撤回, 零OOS观测零交易。
本文件保留=实现为正确纯回看口径(shift+rolling, 已对001206直数ZT核验), 供未来有效状态域复用。
胚胎态=pos60>=60 & 0<ret20<20 & 60日板史>=2 & 近5日无板 & 当日未板(与
reports/prelaunch_lowpos_formal_20261019.md PartC/PartF 逐位一致口径)。
参数单一真源=prereg params 字段(缺字段 fail-closed 退出, 禁止回退硬编码)。
数据源=dragon_quant_qfq.db stock_daily_meta_qfq(唯一回测价格源); 宇宙=60+00+30(剔科创/北交)。
幂等: 全窗口重算+台账原子覆盖, 重跑安全; 只记 activation_since 起 OOS 事件(样内不进台账)。
记账语义=信号日收盘持有, 无止损(纯持有口径); ZT阈值=主板9.7/创业19.5(low_repair 同构,
未做 ST 4.85 特判=与 stage 研究 4.85 口径的微小边界差, prereg boundaries 已声明)。
OOS 验收门(LOCKED)=激活 20261019 起 60 交易日或到期去簇事件 n>=30 先到,
pass=fwd20 med>=+2.0% 且 win>=55%; kill=前 10 个到期事件 fwd20 全<0。
边界: 纯观测影子层零交易权, 禁止接交易闸门/交易 cron/executor; pass/kill 仅呈报主人。

用法:
  python analysis/embryo_shadow_daily.py            # 全窗口重算+台账原子覆盖+快照
  python analysis/embryo_shadow_daily.py --report   # 同上并打印简报
  python analysis/embryo_shadow_daily.py --prereg=X.json  # 指定prereg(fail-closed/复现测试用)
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.stdout.reconfigure(encoding="utf-8")

PREREG = Path(r"D:\zcodesj\data\PREREG_PRELAUNCH_EMBRYO_SHADOW_DRAFT_20261019.json")
DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
LEDGER = Path(r"D:\zcodesj\data\embryo_shadow_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\embryo_shadow_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\embryo_shadow_brief_latest.txt")


def load_prereg(path: Path) -> dict:
    """参数单一真源: prereg params 字段; 缺字段 fail-closed。"""
    p = json.loads(path.read_text(encoding="utf-8"))
    if p.get("status") != "ACTIVE":
        raise SystemExit(f"[FAIL-CLOSED] prereg status={p.get('status')} 非ACTIVE")
    pa = p.get("params") or {}
    need = ["pos_min", "ret20_lo", "ret20_hi", "hist_min", "quiet_bars", "dedup_days",
            "horizons", "warmup_bars", "activation_since", "lock_days", "lock_n",
            "pass_med", "pass_win", "kill_first_n", "zt_thr_main", "zt_thr_gem", "entry"]
    for k in need:
        if k not in pa:
            raise SystemExit(f"[FAIL-CLOSED] prereg params 缺字段 {k}")
    if pa["entry"] != "signal_day_close(持有语义)":
        raise SystemExit("[FAIL-CLOSED] prereg entry 非 signal_day_close(持有语义)")
    return {"pa": pa, "since": int(pa["activation_since"]), "name": p.get("name"),
            "fp": p.get("fingerprint")}


def atomic_write_text(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def main() -> int:
    args = sys.argv[1:]
    prereg_path = PREREG
    report = False
    for a in args:
        if a == "--report":
            report = True
        elif a.startswith("--prereg="):
            prereg_path = Path(a.split("=", 1)[1])
    cfg = load_prereg(prereg_path)
    pa = cfg["pa"]

    conn = sqlite3.connect(r"file:" + DB + "?mode=ro", uri=True)
    df = pd.read_sql(
        "SELECT code,date,open,high,low,close,volume,change_pct FROM stock_daily_meta_qfq "
        "WHERE date>='20160101' AND code NOT LIKE '688%' AND code NOT LIKE '689%' "
        "AND substr(code,1,2) NOT IN ('42','83','87','92')", conn)
    conn.close()
    df = df[(df["volume"] > 0) & (df["open"] > 0)].copy()
    df["date"] = df["date"].astype(str)
    df = df.sort_values(["code", "date"]).reset_index(drop=True)
    g = df.groupby("code", sort=False)
    df["prev_close"] = g["close"].shift(1)
    df["pct"] = (df["close"] / df["prev_close"] - 1) * 100.0
    df["rowid"] = df.groupby("code").cumcount()
    gem = df["code"].str.startswith(("300", "301"))
    thr = np.where(gem, float(pa["zt_thr_gem"]), float(pa["zt_thr_main"]))
    df["iszt"] = df["pct"] >= thr
    zt_i = df["iszt"].astype(int)
    cmax = g["close"].transform(lambda s: s.rolling(60, min_periods=40).max())
    cmin = g["close"].transform(lambda s: s.rolling(60, min_periods=40).min())
    df["pos60"] = np.where((cmax - cmin) > 0, (df["close"] - cmin) / (cmax - cmin) * 100.0, np.nan)
    df["ret20"] = g["close"].transform(lambda s: (s / s.shift(20) - 1) * 100.0)
    df["zt5"] = g["iszt"].transform(lambda s: s.shift(1).rolling(int(pa["quiet_bars"]), min_periods=1).sum())
    df["zt60"] = g["iszt"].transform(lambda s: s.shift(1).rolling(60, min_periods=1).sum())
    HZ = [int(x) for x in pa["horizons"]]
    for k in HZ:
        df[f"fwd{k}"] = g["close"].transform(lambda s, k=k: (s.shift(-k) / s - 1) * 100.0)

    emb = ((df["pos60"] >= float(pa["pos_min"])) & (df["ret20"] > float(pa["ret20_lo"]))
           & (df["ret20"] < float(pa["ret20_hi"])) & (df["zt60"] >= int(pa["hist_min"]))
           & (df["zt5"] == 0) & (~df["iszt"]) & (df["rowid"] >= int(pa["warmup_bars"])))
    since_s = str(cfg["since"])
    sub = df[emb & (df["date"] >= since_s)].copy()
    DD = int(pa["dedup_days"])
    if len(sub):
        keep = np.zeros(len(sub), dtype=bool)
        last = {}
        scodes = sub["code"].to_numpy()
        srows = sub["rowid"].to_numpy()
        for i in range(len(sub)):
            c = scodes[i]
            if srows[i] - last.get(c, -10 ** 9) >= DD:
                keep[i] = True
                last[c] = srows[i]
        sub = sub[keep]

    rows = []
    for _, r in sub.iterrows():
        rec = {"code": r["code"], "event_date": int(r["date"]),
               "pos60": round(float(r["pos60"]), 1) if np.isfinite(r["pos60"]) else None,
               "ret20": round(float(r["ret20"]), 2) if np.isfinite(r["ret20"]) else None,
               "hist60": int(r["zt60"]) if np.isfinite(r["zt60"]) else None}
        for k in HZ:
            v = r.get(f"fwd{k}")
            rec[f"fwd{k}"] = round(float(v), 3) if np.isfinite(v) else None
        rows.append(rec)

    led = pd.DataFrame(rows)
    if len(led):
        led = led.sort_values(["event_date", "code"]).reset_index(drop=True)
    tmp = LEDGER.with_suffix(".jsonl.tmp")
    led.to_json(tmp, orient="records", lines=True, force_ascii=False)
    os.replace(tmp, LEDGER)

    mat = led[led["fwd20"].notna()] if "fwd20" in led.columns else led.iloc[0:0]
    trade_days = int(df[df["date"] >= since_s]["date"].nunique())
    first_n = mat.head(int(pa["kill_first_n"]))["fwd20"]
    kill = bool(len(first_n) == int(pa["kill_first_n"]) and (first_n < 0).all())
    lock_hit = trade_days >= int(pa["lock_days"]) or len(mat) >= int(pa["lock_n"])
    gate = None
    if len(mat) >= int(pa["lock_n"]):
        gate = bool(mat["fwd20"].median() >= float(pa["pass_med"])
                    and (mat["fwd20"] > 0).mean() >= float(pa["pass_win"]) / 100.0)
    snap = {"run": "prelaunch_embryo_shadow", "prereg": PREREG.name,
            "fingerprint": cfg["fp"], "activation_since": cfg["since"],
            "state_def": "pos60>=60 & 0<ret20<20 & 板史60>=2 & 近5日无板 & 当日未板",
            "booked_total": int(len(led)), "matured_n": int(len(mat)),
            "open_n": int(len(led) - len(mat)),
            "med20": round(float(mat["fwd20"].median()), 2) if len(mat) else None,
            "win20": round(float((mat["fwd20"] > 0).mean()) * 100, 1) if len(mat) else None,
            "trade_days_since_activation": trade_days,
            "lock_progress": f"{trade_days}/{int(pa['lock_days'])}交易日, 到期n={len(mat)}/{int(pa['lock_n'])}",
            "lock_hit": bool(lock_hit), "kill_switch": kill, "gate_pass_now": gate,
            "gate_pass_rule": f"med>={pa['pass_med']}% 且 win>={pa['pass_win']}%",
            "kill_rule": f"前{int(pa['kill_first_n'])}个到期事件fwd20全<0"}
    atomic_write_text(SNAPSHOT, json.dumps(snap, ensure_ascii=False, indent=1))

    brief = (
        f"PRELAUNCH_EMBRYO_SHADOW 影子简报 生成于 {pd.Timestamp.now():%Y-%m-%d %H:%M}\n"
        f"prereg={PREREG.name} 指纹={cfg['fp']} 激活={cfg['since']}\n"
        f"胚胎态={snap['state_def']} 记账=信号日收盘持有(无止损)\n"
        f"记账 n={snap['booked_total']} 到期 n={snap['matured_n']} 未到期={snap['open_n']} "
        f"med20={snap['med20']} win20={snap['win20']}\n"
        f"LOCKED 进度={snap['lock_progress']} kill_switch={kill} gate_pass_now={gate}\n"
        f"验收门: pass=med>=+2.0%且win>=55%; kill=前10到期事件fwd20全<0; 纯观测零交易权\n"
    )
    atomic_write_text(BRIEF, brief)

    print(f"[embryo_shadow] booked={snap['booked_total']} matured={snap['matured_n']} "
          f"open={snap['open_n']} med20={snap['med20']} win20={snap['win20']} "
          f"lock={snap['lock_progress']} kill_switch={kill} gate={gate}")
    if report:
        print(brief)
    return 0


if __name__ == "__main__":
    sys.exit(main())
