# -*- coding: utf-8 -*-
"""概念主线四阶段状态机+分歧日核心股检测 (PD-035 / PREREG_CONCEPT_STAGE_20261007, 指纹9981cc8e618d4800)
规则 LOCKED: 主升=rank20前20%∧aboveMA20∧ret5>=0; 鱼尾=强∧aboveMA20∧ret5<0;
酝酿启动=aboveMA60∧rank20 40-80%; 退潮=belowMA20。分歧日=板块单日<=-2.5%∧aboveMA60。
板块指数=成分等权收益累计(qfq自算)。判据预钉(60交易日, 见prereg), 禁止改参数。

用法:
  python analysis/concept_stage_daily.py            # 扫描库内最新交易日(幂等)
  python analysis/concept_stage_daily.py --report   # 生成晨报简报
边界: 观测层零交易权; layer3(admitted/闸门)未满窗+未获主人第二道裁决前禁止接线。
"""
from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
MEMBER_DB = "file:D:/zcodesj/eastmoney_quant/sector/sector_data.db?mode=ro"
LEDGER = Path(r"D:\zcodesj\data\concept_stage_ledger.jsonl")
BRIEF = Path(r"D:\zcodesj\data\concept_stage_brief_latest.txt")
ACTIVE = ["人形机器人", "算力概念", "CPO概念", "固态电池", "创新药", "军工", "低空经济", "商业航天", "黄金概念"]
DIP_TH = -2.5
SHADOW_START = 20261008
GATES = {"main_f20_min": 1.0, "dip_f20_min": 1.0, "dip_win_min": 52.0, "dip_n_min": 10}


def load_members() -> pd.DataFrame:
    conn = sqlite3.connect(MEMBER_DB, uri=True)
    mem = pd.read_sql("SELECT sector_code,stock_code FROM sector_member", conn)
    names = dict(conn.execute("SELECT sector_code,sector_name FROM sector_basic").fetchall())
    conn.close()
    mem["stock_code"] = mem["stock_code"].astype(str).str.zfill(6)
    mem["sector_name"] = mem["sector_code"].map(names)
    hit = mem[mem["sector_name"].apply(lambda n: any(k in str(n) for k in ACTIVE))]
    return hit.drop_duplicates(["sector_code", "stock_code"])


def build_index(mem: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    conn = sqlite3.connect(QFQ, uri=True)
    codes = tuple(mem["stock_code"])
    q = pd.read_sql(
        f"SELECT code,date,close,change_pct,volume FROM stock_daily_meta_qfq "
        f"WHERE date>='20240101' AND code IN ({','.join('?'*len(codes))})",
        conn, params=codes)
    conn.close()
    q["date"] = q["date"].astype(int)
    q = q.sort_values(["code", "date"]).reset_index(drop=True)
    mm = dict(zip(mem["stock_code"], mem["sector_code"]))
    q["sector"] = q["code" if "code" in q else "code"].map(mm)
    q = q.dropna(subset=["sector"])
    res = []
    for sec, sub in q.groupby("sector"):
        ew = sub.groupby("date")["change_pct"].mean()
        px = (1 + ew / 100).cumprod() * 1000
        res.append(pd.DataFrame({"date": ew.index, "ew_ret": ew.values, "px": px.values, "sector": sec}))
    idx = pd.concat(res).sort_values(["sector", "date"]).reset_index(drop=True)
    g = idx.groupby("sector")
    idx["ret5"] = g["ew_ret"].transform(lambda s: s.rolling(5).sum())
    idx["ret20"] = g["ew_ret"].transform(lambda s: s.rolling(20).sum())
    for n in (20, 60):
        idx[f"ma{n}"] = g["px"].transform(lambda s, n=n: s.rolling(n).mean())
    idx["bar_n"] = g.cumcount()
    idx = idx[idx["bar_n"] >= 65].copy()
    idx["rank20"] = idx.groupby("date")["ret20"].rank(pct=True)
    idx["above20"] = idx["px"] > idx["ma20"]
    idx["above60"] = idx["px"] > idx["ma60"]
    strong = idx["rank20"] >= 0.8
    idx["stage"] = np.select(
        [strong & idx["above20"] & (idx["ret5"] >= 0), strong & idx["above20"] & (idx["ret5"] < 0),
         idx["above60"] & (idx["rank20"] >= 0.4) & (idx["rank20"] < 0.8), ~idx["above20"]],
        ["主升", "鱼尾", "酝酿/启动", "退潮"], default="其他")
    return idx, q


def detect_dips(idx: pd.DataFrame, q: pd.DataFrame, mem: pd.DataFrame, target: int) -> list[dict]:
    dips = idx[(idx["date"] == target) & (idx["ew_ret"] <= DIP_TH) & idx["above60"]]
    out = []
    if dips.empty:
        return out
    day = q[q["date"] == target].merge(
        dips[["sector", "ew_ret"]].rename(columns={"ew_ret": "sec_ret"}), on="sector", how="inner")
    if day.empty:
        return out
    day = day.copy()
    day["rel"] = day["change_pct"] - day["sec_ret"]
    day["vol_rank"] = day.groupby("sector")["volume"].rank(pct=True)
    core = day[(day["rel"] > 0) & (day["vol_rank"] >= 0.5)]
    stage_map = dict(zip(dips["sector"], dips["stage"]))
    nm = dict(zip(mem["stock_code"], mem["sector_name"]))
    for sec, sub in core.groupby("sector"):
        out.append({
            "date": int(target), "sector": sec, "sector_name": nm.get(sec, sec),
            "stage": stage_map.get(sec, "N/A"), "sec_ret": round(float(sub["sec_ret"].iloc[0]), 2),
            "core_stocks": [(r["code"], r.get("stock_name", "")) for r in
                            mem[mem["sector_code"] == sec].merge(
                                sub[["code"]], left_on="stock_code", right_on="code").to_dict("records")][:8],
        })
    return out


def evaluate_gates(rows: list[dict]) -> dict:
    fwd_rows = [r for r in rows if r.get("kind") == "dip_core_fwd"]
    if not fwd_rows:
        return {"verdict": "未满窗", "n": 0, "detail": "无样本"}
    f20 = np.array([r["f20"] for r in fwd_rows if r.get("f20") is not None])
    n = len(f20)
    med = float(np.median(f20)) if n else None
    win = float((f20 > 0).mean() * 100) if n else None
    ready = n >= 30 and max(r["date"] for r in rows) >= 0  # 满窗判定由60日自然积累,此处按n口径
    g1 = med is not None and med >= GATES["dip_f20_min"]
    g2 = win is not None and win >= GATES["dip_win_min"]
    g3 = n >= GATES["dip_n_min"]
    verdict = ("三门全过->呈请第二道裁决" if (g1 and g2 and g3) else "任一门不过->归档") if n >= 30 else "未满窗"
    return {"verdict": verdict, "n": n, "f20_med": med, "f20_win": win,
            "detail": f"G1(f20med>=+1.0):{g1} G2(win>=52):{g2} G3(n>=10):{g3}"}


def main() -> int:
    mem = load_members()
    idx, q = build_index(mem)
    target = int(idx["date"].max())
    rows = []
    if LEDGER.exists():
        rows = [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]

    # 幂等: 当日阶段行
    names = dict(zip(mem["sector_code"], mem["sector_name"]))
    stage_rows = [{"kind": "stage", "date": int(r.date), "sector": r.sector,
                   "sector_name": names.get(r.sector, ""),
                   "stage": r.stage, "ew_ret": round(float(r.ew_ret), 2), "ret20": round(float(r.ret20), 2)}
                  for r in idx[idx["date"] == target].itertuples()]
    dip_rows = [{"kind": "dip_core", **d} for d in detect_dips(idx, q, mem, target)]
    new_rows = stage_rows + dip_rows
    old_keys = {(r["kind"], r["date"], r.get("sector", "")) for r in rows}
    kept = [r for r in rows if (r["kind"], r["date"], r.get("sector", "")) not in
            {(x["kind"], x["date"], x.get("sector", "")) for x in new_rows}]
    all_rows = kept + new_rows
    tmp = LEDGER.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for r in sorted(all_rows, key=lambda x: (x["date"], x["kind"], x.get("sector", ""))):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(LEDGER)

    # 简报
    lines = [f"【概念主线阶段 {target}】"]
    for r in idx[idx["date"] == target].sort_values("rank20", ascending=False).itertuples():
        lines.append(f"  {names.get(r.sector, r.sector)}: {r.stage} (当日{r.ew_ret:+.2f}% 20日{r.ret20:+.1f}%)")
    if dip_rows:
        lines.append("🔴 今日分歧日(板块大跌但趋势未破):")
        for d in dip_rows:
            codes_str = " ".join(f"{c}" for c, _ in d["core_stocks"][:6])
            lines.append(f"  {d['sector_name']} 阶段={d['stage']} 板块{d['sec_ret']:+.1f}% 核心股(抗跌∧量前50%): {codes_str}")
        lines.append("  语义: 主升中分歧=回调观察窗(待T+1确认); 鱼尾中分歧=反弹逃命窗非上车点")
    else:
        lines.append("今日无分歧日信号")
    lines.append(f"(PD-035观测层零交易权; 指纹9981cc8e618d4800; 闸门候选须主人第二道裁决)")
    BRIEF.write_text("\n".join(lines), encoding="utf-8")

    print(f"[SCAN] {target} 9概念阶段判定+分歧日{len(dip_rows)}个落账; 台账{len(all_rows)}行")
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    sys.exit(main())
