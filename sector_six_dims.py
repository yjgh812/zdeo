# -*- coding: utf-8 -*-
"""板块六维状态计算 (direction_layer 呈现层升级, 呈请B 2026-10-18, git审计系列五报告)
================================================================================
定位: direction_layer_brief.py 的计算模块(owner=本文件); 编排层(方向层)只消费不实现。
呈现级零交易权——usage 合同不变(收敛视野+排除逆风,非过滤器); 不改任何既有库。

六维×数据源(全只读, N/A 不伪造):
  strength   = data/industry_volume_state_latest.json (夜链产物, volr20/hot5/c1 引用)
  trend      = workbuddyspace sw库 industry_kline_sw L1 close → MA20/MA60 → 多头/震荡/空头
               (判据预钉呈现参考: 多头=close>MA20>MA60; 空头=close<MA20<MA60; 其余=震荡)
  stage      = data/concept_stage_brief_latest.txt (PD-035 九概念白名单, 引用不重算)
  crowding   = industry_kline_sw L1 vol → 行业占比20日均 → 该行业250日窗分位
  flow       = 复盘 risk_warnings 行业流出行 (编排层注入 hint, 本模块只透传)
  valuation  = N/A (本地无行业PE/PB数据源, 待建, 如实声明)
  dispersion = qfq 个股ret20 → 行业内横截面std (映射=data/industry_group_map_snapshot.db
               当前快照非PIT, 历史切片近似, 只作方向性参考)

失败语义: 抛异常 → direction_layer 编排层捕获降级为 unavailable, 不阻塞主三源合同。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3

import numpy as np
import pandas as pd

ROOT = r"D:\zcodesj"
SW_DB = "file:D:/workbuddyspace/databases/sw_industry.db?mode=ro"
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
MAP_DB = "file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro"
IVS_JSON = os.path.join(ROOT, "data", "industry_volume_state_latest.json")
STAGE_TXT = os.path.join(ROOT, "data", "concept_stage_brief_latest.txt")
CROWD_WINDOW, CROWD_PCTILE_WIN = 20, 250
RET20_WINDOW = 20


def _load_strength() -> dict:
    if not os.path.exists(IVS_JSON):
        return {}
    d = json.load(open(IVS_JSON, encoding="utf-8"))
    return {r.get("industry"): r for r in d.get("industries", [])}


def _load_stage() -> dict:
    """PD-035 简报文本解析: 概念名→阶段词(主升/鱼尾/酝酿启动/退潮), 找不到行则空。"""
    out = {}
    if not os.path.exists(STAGE_TXT):
        return out
    pat = re.compile(r"([\u4e00-\u9fa5A-Za-z0-9\u2160-\u217f]+?)[：:]\s*(主升|鱼尾|酝酿启动|退潮)")
    for ln in open(STAGE_TXT, encoding="utf-8", errors="replace"):
        m = pat.search(ln)
        if m:
            out[m.group(1)] = m.group(2)
    return out


def _load_l1_kline() -> pd.DataFrame:
    conn = sqlite3.connect(SW_DB, uri=True)
    df = pd.read_sql_query(
        "SELECT name, trade_date, close, volume FROM industry_kline_sw WHERE level='L1'", conn)
    conn.close()
    df["trade_date"] = df["trade_date"].str.replace("-", "").astype(int)
    return df.sort_values(["name", "trade_date"]).reset_index(drop=True)


def _trend_state(g: pd.DataFrame) -> str:
    c = g["close"].astype(float)
    if len(c) < 61:
        return "N/A(历史不足)"
    ma20, ma60 = c.iloc[-20:].mean(), c.iloc[-60:].mean()
    px = c.iloc[-1]
    if px > ma20 > ma60:
        return "多头"
    if px < ma20 < ma60:
        return "空头"
    return "震荡"


def _crowd_pctile(share: pd.Series) -> dict:
    """share=该行业 volume 占全行业当日总和比序列(调用前已在全表算好, 20261018修: 单行业自身为分母会恒1.0)。"""
    if len(share) < CROWD_PCTILE_WIN:
        return {"share20": None, "pctile_250d": None, "note": "历史不足"}
    share20 = share.rolling(CROWD_WINDOW).mean()
    cur = share20.iloc[-1]
    hist = share20.iloc[-CROWD_PCTILE_WIN:]
    pctile = float((hist <= cur).mean() * 100)
    return {"share20": round(float(cur), 4) if pd.notna(cur) else None,
            "pctile_250d": round(pctile, 1) if pd.notna(cur) else None}


def _dispersion(ind_map: dict) -> dict:
    q = sqlite3.connect(QFQ_DB, uri=True)
    px = pd.read_sql_query(
        "SELECT code, date, close FROM stock_daily_meta_qfq WHERE date >= ?", q,
        params=(int(pd.Timestamp.now().strftime("%Y%m%d")) - 60,),)
    q.close()
    px["code"] = px["code"].astype(str).str.zfill(6)
    last = px.groupby("code").tail(1).set_index("code")["close"]
    prev = px.sort_values("date").groupby("code").head(1).set_index("code")["close"]
    common = last.index.intersection(prev.index)
    ret = (last[common] / prev[common] - 1) * 100
    df = pd.DataFrame({"code": common, "ret_win": ret.values})
    df["l1"] = df["code"].map(ind_map)
    df = df[df["l1"].notna()]
    return df.groupby("l1")["ret_win"].std(ddof=1).to_dict()


def build_six_dims(flow_hints: list[str] | None = None) -> dict:
    import datetime as _dt
    strength = _load_strength()
    stage = _load_stage()
    kl = _load_l1_kline()
    latest_day = int(kl["trade_date"].max())

    import sqlite3 as _s3
    mp = _s3.connect(MAP_DB, uri=True)
    members = mp.execute("SELECT code,l1 FROM l1_member WHERE ambiguous_flag=0").fetchall()
    mp.close()
    ind_map = {str(c).zfill(6): l1 for c, l1 in members}
    disp = _dispersion(ind_map)
    disp_vals = np.array([v for v in disp.values() if pd.notna(v)])

    flow_re = re.compile(r"(行业流出|行业流入)[:：]\s*([^\(-]+?)\s*\(")
    flow_map = {}
    for h in flow_hints or []:
        m = flow_re.search(h)
        if m:
            flow_map[m.group(2).strip()] = m.group(1)

    # crowding: 全行业当日 volume 总和为分母(单行业自身为分母会恒1.0, 20261018 修)
    total_by_day = kl.groupby("trade_date")["volume"].transform("sum")
    kl = kl.assign(share=kl["volume"].astype(float) / total_by_day.replace(0, np.nan))

    industries = []
    for name, g in kl.groupby("name"):
        st = strength.get(name, {})
        crowd = _crowd_pctile(g.sort_values("trade_date")["share"])
        d_std = disp.get(name)
        d_pct = (float((disp_vals <= d_std).mean() * 100)
                 if pd.notna(d_std) and len(disp_vals) else None)
        industries.append({
            "industry": name,
            "strength": {"volr20": st.get("volr20"), "hot5": st.get("hot5"),
                         "c1": st.get("c1", False)},
            "trend": _trend_state(g),
            "crowding": crowd,
            "flow_hint": flow_map.get(name, "N/A"),
            "valuation": "N/A(行业PE/PB数据源待建)",
            "dispersion": {"ret20_std": round(float(d_std), 2) if pd.notna(d_std) else None,
                           "pctile": round(d_pct, 1) if d_pct is not None else None},
        })

    return {
        "status": "available",
        "generated_at": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "data_day": latest_day,
        "window": {"crowd_pctile_days": CROWD_PCTILE_WIN, "dispersion_win": "近60自然日ret20横截面"},
        "boundary": "呈现级零交易权; usage合同不变(收敛视野+排除逆风非过滤器); "
                    "trend/拥挤度为呈现参考非信号; valuation数据源待建; 行业映射非PIT; "
                    "PD-035阶段为概念域(非申万L1域)独立呈现于pd035_concept_stage, 禁强行映射",
        "n_industries": len(industries),
        "industries": industries,
        "pd035_concept_stage": stage,
    }
