# -*- coding: utf-8 -*-
"""garch_shadow_daily.py —— GARCH 波动预测·风险层影子期每日落盘 v2（PREREG_GARCH_SHADOW_20260905）

v2 改造（实测迭代）：①收益序列增量缓存 data/garch_shadow_returns.json（日常仅 SQL 缺口行,秒级；
首次 bootstrap 全量约1-2分钟属预期）②防并发锁 data/garch_shadow.lock（陈旧锁>1小时自动失效）
每日动作：GARCH(1,1) 近1000日拟合→次日波动预测 + vola20/EWMA 对照 → data/garch_shadow.csv 幂等 UPSERT
（先回填昨日实现值再追加今日预测行）→ 滚动 QLIKE 累计。
v3 增量观察臂（2026-09-20 数学学习落地 math_knowledge_full_study_20260920）：新增 GJR(1,1,1)
非对称观察臂列 sigma_gjr + 双模型波动半衰期列（α+β / α+γ/2+β 口径, owner=tulong_math_toolkit）。
**只加不改**: prereg(PREREG_GARCH_SHADOW_20260905) 主口径=QLIKE 三方对比冻结不动; GJR 失败仅降级
NaN 不 FAIL。半衰期换算"波动事件衰减半程天数"对齐 2-6 周持有期风险预算（外部数学: A股杠杆效应显著）。
exit：0 正常 / 2 结构化失败。零拦截零执行权，口径以 PREREG design_LOCKED 为准。
"""
import glob
import json
import math
import os
import sqlite3
import sys
import time
import warnings
from datetime import datetime

from tulong_math_toolkit import garch_halflife, gjr_persistence

warnings.filterwarnings("ignore")
import numpy as np
import pandas as pd
from arch import arch_model

ROOT = os.path.dirname(os.path.abspath(__file__))
QFQ_DB = "D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
CSV = os.path.join(ROOT, "data", "garch_shadow.csv")
RET_CACHE = os.path.join(ROOT, "data", "garch_shadow_returns.json")
LOCK = os.path.join(ROOT, "data", "garch_shadow.lock")
TODAY = datetime.now().strftime("%Y%m%d")
FIT_WINDOW = 1000
result = {"date": TODAY, "status": "OK", "notes": []}


def qlike(rv, sig):
    rv, sig = np.asarray(rv, float), np.asarray(sig, float)
    m = np.isfinite(rv) & np.isfinite(sig) & (sig > 0) & (rv > 0)
    if m.sum() < 10:
        return None
    r, s = rv[m], sig[m]
    return float(np.mean(r ** 2 / s ** 2 - np.log(r ** 2 / s ** 2) - 1))


def acquire_lock():
    if os.path.exists(LOCK):
        if time.time() - os.path.getmtime(LOCK) < 3600:
            print(f"[garch_shadow {TODAY}] 已有实例运行中(lock新鲜), 退出")
            sys.exit(0)
    with open(LOCK, "w", encoding="utf-8") as f:
        f.write(TODAY)


def release_lock():
    try:
        os.remove(LOCK)
    except OSError:
        pass


def load_returns():
    """收益序列缓存: {date: ret小数}; 返回 dict 与是否需要 bootstrap"""
    if os.path.exists(RET_CACHE):
        d = json.load(open(RET_CACHE, encoding="utf-8"))
        return d.get("returns", {}), False
    return {}, True


def sql_avg(date_from=None):
    con = sqlite3.connect(QFQ_DB)
    if date_from:
        rows = con.execute(
            "SELECT date, AVG(change_pct) FROM stock_daily_meta_qfq "
            "WHERE date>? AND (code LIKE '6%' OR code LIKE '0%' OR code LIKE '3%') "
            "GROUP BY date ORDER BY date", (date_from,)).fetchall()
    else:
        rows = con.execute(
            "SELECT date, AVG(change_pct) FROM stock_daily_meta_qfq "
            "WHERE date>='20170101' AND (code LIKE '6%' OR code LIKE '0%' OR code LIKE '3%') "
            "GROUP BY date ORDER BY date").fetchall()
    con.close()
    return {d: a / 100.0 for d, a in rows if a is not None}


def main():
    acquire_lock()
    try:
        returns, need_bootstrap = load_returns()
        if need_bootstrap or len(returns) < FIT_WINDOW + 40:
            t0 = time.time()
            returns.update(sql_avg(None))  # bootstrap 全量(约1-2分钟, 仅首次)
            result["notes"].append(f"bootstrap 全量SQL完成 {len(returns)}日 耗时{time.time()-t0:.0f}s")
        else:
            last = max(returns)
            gap = sql_avg(last)
            returns.update(gap)
            result["notes"].append(f"增量SQL +{len(gap)}日")
        dates = sorted(returns)
        rets = np.array([returns[d] for d in dates])
        if len(dates) < FIT_WINDOW + 10:
            result["status"] = "FAIL"
            result["notes"].append(f"样本不足 {len(dates)}")
            return 2
        # 收益缓存落盘
        with open(RET_CACHE, "w", encoding="utf-8") as f:
            json.dump({"returns": {d: round(float(r), 8) for d, r in zip(dates, rets)}}, f)

        t_idx = len(dates) - 1
        t_date = dates[t_idx]
        train = rets[t_idx - FIT_WINDOW + 1: t_idx + 1] * 100
        try:
            am = arch_model(train, vol="GARCH", p=1, q=1, dist="normal").fit(disp="off")
            sigma_garch = float(am.forecast(horizon=1, reindex=False).variance.values[-1, 0]) ** 0.5 / 100
            params = {"omega": round(float(am.params["omega"]), 8),
                      "alpha": round(float(am.params["alpha[1]"]), 4),
                      "beta": round(float(am.params["beta[1]"]), 4)}
        except Exception as e:
            result["status"] = "FAIL"
            result["notes"].append(f"GARCH fit 失败: {e}")
            return 2
        vola20 = float(np.std(rets[t_idx - 19: t_idx + 1], ddof=1))
        ewma = float(pd.Series(rets[max(0, t_idx - 250): t_idx + 1]).ewm(alpha=1 - 0.94).std().iloc[-1])

        # ---- v3 增量观察臂: GJR(1,1,1) 非对称 + 双模型半衰期（只加不改, 失败降级不 FAIL）----
        sigma_gjr = halflife_garch = halflife_gjr = None
        gjr_params = {}

        def _hl(persistence: float):
            h = garch_halflife(persistence)
            if h != h:
                return None
            return 9999.0 if h == float("inf") else round(h, 1)

        try:
            am2 = arch_model(train, vol="GARCH", p=1, o=1, q=1, dist="normal").fit(disp="off")
            sigma_gjr = float(am2.forecast(horizon=1, reindex=False).variance.values[-1, 0]) ** 0.5 / 100
            gjr_params = {"omega": round(float(am2.params["omega"]), 8),
                          "alpha": round(float(am2.params["alpha[1]"]), 4),
                          "gamma": round(float(am2.params["gamma[1]"]), 4),
                          "beta": round(float(am2.params["beta[1]"]), 4)}
            halflife_garch = _hl(params["alpha"] + params["beta"])
            halflife_gjr = _hl(gjr_persistence(gjr_params["alpha"], gjr_params["gamma"], gjr_params["beta"]))
        except Exception as e:
            result["notes"].append(f"GJR 观察臂拟合失败(降级NaN): {e}")

        cols = ["date", "sigma_garch", "vola20", "ewma", "rv1_next", "rv20_next", "degraded",
                "sigma_gjr", "halflife_garch", "halflife_gjr"]
        df = pd.read_csv(CSV, dtype={"date": str}) if os.path.exists(CSV) else pd.DataFrame(columns=cols)
        for _c in ("sigma_gjr", "halflife_garch", "halflife_gjr"):
            if _c not in df.columns:  # 旧 CSV 无 v3 列 → 补空列(旧行该三列 NaN 属预期)
                df[_c] = None
        if t_idx >= 1 and len(df):
            m = df["date"] == dates[t_idx - 1]
            if m.any():
                df.loc[m, "rv1_next"] = round(abs(float(rets[t_idx])), 6)
                df.loc[m, "rv20_next"] = round(float(np.std(rets[t_idx - 19: t_idx + 1], ddof=1)), 6)
        row = {"date": t_date, "sigma_garch": round(sigma_garch, 6), "vola20": round(vola20, 6),
               "ewma": round(ewma, 6), "rv1_next": None, "rv20_next": None, "degraded": 0,
               "sigma_gjr": round(sigma_gjr, 6) if sigma_gjr is not None else None,
               "halflife_garch": halflife_garch, "halflife_gjr": halflife_gjr}
        m = df["date"] == t_date
        if m.any():
            for k, v in row.items():
                df.loc[m, k] = v
        else:
            df = pd.concat([df, pd.DataFrame([row])], ignore_index=True)
        df = df.drop_duplicates(subset="date", keep="last").sort_values("date")
        df.to_csv(CSV, index=False)

        q = {"n_pairs": 0}
        dd = df.dropna(subset=["rv20_next"]).copy()
        if len(dd) >= 10:
            qg = qlike(dd["rv20_next"], dd["sigma_garch"])
            qv = qlike(dd["rv20_next"], dd["vola20"])
            qe = qlike(dd["rv20_next"], dd["ewma"])
            q["qlike_garch"] = round(qg, 5) if qg is not None else None
            q["qlike_vola20"] = round(qv, 5) if qv is not None else None
            q["qlike_ewma"] = round(qe, 5) if qe is not None else None
            if qg is not None and qv is not None:
                q["garch_minus_vola20"] = round(qg - qv, 5)
            q["n_pairs"] = int(len(dd))
            # v3 观察臂: GJR 的 QLIKE 仅作参考输出(主口径冻结不动); 覆盖日从 GJR 落列起自然累积
            if "sigma_gjr" in dd.columns:
                qgj = qlike(dd["rv20_next"], dd["sigma_gjr"])
                if qgj is not None:
                    result["gjr_observation"] = {"qlike_gjr": round(qgj, 5),
                                                 "gjr_minus_garch": round(qgj - qg, 5) if qg is not None else None,
                                                 "n_gjr_pairs": int(dd["sigma_gjr"].notna().sum())}
        result["garch_params"] = params
        result["gjr_params"] = gjr_params
        result["halflife_days"] = {"garch": halflife_garch, "gjr": halflife_gjr}
        result["today"] = {"date": t_date, "sigma_garch": round(sigma_garch, 6),
                           "vola20": round(vola20, 6), "ewma": round(ewma, 6)}
        result["rolling_qlike"] = q
        return 0
    finally:
        release_lock()


rc = main()
if rc == 2:
    result["status"] = "FAIL"
out_fp = os.path.join(ROOT, "data", f"garch_shadow_run_{TODAY}.json")
with open(out_fp, "w", encoding="utf-8") as fh:
    json.dump(result, fh, ensure_ascii=False, indent=1)
print(f"[garch_shadow {TODAY}] status={result['status']}", json.dumps(result.get("today", {})),
      json.dumps(result.get("rolling_qlike", {})))
for n in result["notes"]:
    print("  note:", n)
sys.exit(rc)
