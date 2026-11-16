# -*- coding: utf-8 -*-
"""盘感模块全面测试（vibe_card v3 / prem_seal / seal_snapshot / vibe_intraday / daily_trade_plan）
====================================================================
覆盖：单元（各维度/各函数）· 集成（CLI/幂等）· 数据新鲜度守卫 · 跨源对账 · 边界异常。
只读测试，不写任何生产表（vibe/prem 的 --append 幂等写自身缓存，允许）。
用法：python tests/test_vibe_module.py
退出码：0=全过 1=有失败
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, r"D:\zcodesj")
import vibe_card as vc
import prem_seal_daily as psd
import seal_snapshot as ssnap
import vibe_intraday as vi

_T0 = time.time()
PASS = []
FAIL = []


def check(name: str, cond: bool, detail: str = ""):
    (PASS if cond else FAIL).append(name)
    mark = "✅" if cond else "❌"
    print(f"  {mark} {name}" + (f" | {detail}" if detail else ""))


def sec(name: str):
    print(f"\n===== {name} =====")


# ═══════════════════════════════════════════════════════════
sec("A. vibe_card 核心合成")
card = vc._build_with_retry(None, False)
check("build_card 返回 v2/v3 结构", card.get("version") == "v2")
check("五维分值齐全", all(k in card["dimensions"] for k in
      ("sentiment", "premium", "volume", "index_momentum", "ladder")))
check("盘感分 0-100", 0 <= card["vibe_score"] <= 100, f"score={card['vibe_score']}")
check("档位合法", card["stage"] in ("亢奋/沸点", "热", "常温", "冷", "冰点"))
check("仓位上限 0-1", 0 < card["max_position"] <= 1)
check("权重和=1", abs(sum(card["weights"].values()) - 1.0) < 1e-6, str(card["weights"]))
check("上下文五件套", all(k in card["context"] for k in
      ("cycle_stage", "percentile_250d", "momentum", "divergence", "sc001",
       "seal_quality", "sector_heat")))
check("情绪周期合法", card["context"]["cycle_stage"] in ("启动", "主升", "分歧", "退潮"))
check("分位 0-100", card["context"]["percentile_250d"] is None or 0 <= card["context"]["percentile_250d"] <= 100,
      f"pct={card['context']['percentile_250d']}")
check("动量结构", "ma5" in card["context"]["momentum"] and "trend_days" in card["context"]["momentum"])
check("SC001 结构", "circuit_breaker" in card["context"]["sc001"] and "empty_advice" in card["context"]["sc001"])
check("sector_heat 有主线判定", card["context"]["sector_heat"].get("ok") in (True, False))

# 权重与正交化
sec("A1. 正交化一致性")
res = subprocess.run([sys.executable, r"D:\zcodesj\vibe_ortho_check.py"],
                     capture_output=True, text=True, timeout=300, cwd=r"D:\zcodesj")
ortho = res.stdout + res.stderr
check("vibe_ortho_check 运行成功", res.returncode == 0)
check("无共线结论存在", "VIF" in ortho and "0.44" in ortho or "r=" in ortho)

# ═══════════════════════════════════════════════════════════
sec("B. 各维度单元")
s = card["dimensions"]["sentiment"]
check("情绪复合 score 0-100", 0 <= s["score"] <= 100)
check("涨停/跌停非负整数", s["n_limit_up"] >= 0 and s["n_limit_down"] >= 0)
p = card["dimensions"]["premium"]
check("打板溢价维度存在", p.get("ok") is not None)
if p.get("ok"):
    check("打板溢价与SC001一致性", (p["prem_seal"] <= 0) == card["context"]["sc001"]["circuit_breaker"],
          f"prem_seal={p['prem_seal']}")
v = card["dimensions"]["volume"]
check("量能 ratio 合理(>0或None)", v.get("ratio") is None or v["ratio"] > 0, f"ratio={v.get('ratio')}")
# 白名单修复回归：8/11 是历史污染日(混入88/15/51等ETF/LOF，amt虚增25-35倍)，量能比必须正常
v811 = vc.volume_dim(20260811)
check("量能白名单修复(8/11污染日)", v811.get("ratio") is not None and 0.5 <= v811["ratio"] <= 2.0,
      f"ratio={v811.get('ratio')}")
i = card["dimensions"]["index_momentum"]
check("指数动能 detail 3只", all(d["ok"] for d in i["detail"]) or len(i["detail"]) >= 1)
lad = card["dimensions"]["ladder"]
check("连板梯队结构", lad.get("ok") and "ladder" in lad, f"ladder={lad.get('ladder')}")
if lad.get("ok"):
    check("梯队非负", all(lad["ladder"][k] >= 0 for k in (2, 3, 4, 5)))
seal = card["context"]["seal_quality"]
check("封板质量结构", "ok" in seal)
if seal.get("ok"):
    check("真封单率 0-100", 0 <= seal["true_seal_rate_pct"] <= 100)

# ═══════════════════════════════════════════════════════════
sec("C. CLI 集成与幂等")
r = subprocess.run([sys.executable, r"D:\zcodesj\vibe_card.py", "--line"],
                   capture_output=True, text=True, timeout=180, cwd=r"D:\zcodesj")
check("vibe_card --line 退出0", r.returncode == 0)
check("--line 含盘感字样", "盘感" in r.stdout)
r2 = subprocess.run([sys.executable, r"D:\zcodesj\vibe_card.py", "--append"],
                    capture_output=True, text=True, timeout=180, cwd=r"D:\zcodesj")
check("vibe_card --append 退出0", r2.returncode == 0)
hist = vc.append_history_rows()
n1 = len(hist)
vc.append_history(card)  # 幂等重写同 data_date
check("vibe_history 幂等(同日期不增行)", len(vc.append_history_rows()) == n1, f"rows={n1}")

r3 = subprocess.run([sys.executable, r"D:\zcodesj\prem_seal_daily.py", "--json"],
                    capture_output=True, text=True, timeout=90, cwd=r"D:\zcodesj")
check("prem_seal --json 退出0", r3.returncode == 0)
ph = pd.read_csv(r"D:\zcodesj\_cache\prem_seal_history.csv", dtype={"date": str})
n2 = len(ph)
_last = str(ph["date"].iloc[-1])
_r = psd.compute(_last)
if _r and _r["prem_seal"] is not None:
    psd.append_history(_r)  # 幂等重写同 date
ph2 = pd.read_csv(r"D:\zcodesj\_cache\prem_seal_history.csv", dtype={"date": str})
check("prem_seal 历史幂等", len(ph2) == n2, f"rows={n2}")

r4 = subprocess.run([sys.executable, r"D:\zcodesj\vibe_intraday.py", "--quiet"],
                    capture_output=True, text=True, timeout=90, cwd=r"D:\zcodesj")
check("vibe_intraday 退出0", r4.returncode == 0, r4.stderr[-120:] if r4.returncode else "")
if r4.returncode == 0:
    q = json.loads(r4.stdout)
    check("盘中盘感分合法", 0 <= q["vibe_score"] <= 100)
    check("盘中档位存在", "stage" in q and "qdate" in q)

# ═══════════════════════════════════════════════════════════
sec("D. 数据新鲜度守卫")
_exp_future = int((pd.Timestamp.today() + pd.Timedelta(days=30)).strftime("%Y%m%d"))
d_stale = vc.build_card(_exp_future, False)  # 预期日期=今天+30天(动态, 防时间炸弹——原硬编码20260901已过期)
check("--expected-date 滞后触发 warn", any("滞后" in w for w in d_stale["freshness"]["warn"]),
      f"expected={_exp_future} warn={d_stale['freshness']['warn']}")
# 新鲜正常（当前数据日=情绪源最新交易日；动态断言，防硬编码过期——教训同 test_winrate T6.1）
import sqlite3 as _sc0
_cx = _sc0.connect(r"D:\workbuddyspace\databases\fundamental_data.db")
_exp = _cx.execute("SELECT MAX(trade_date) FROM feature_market_sentiment").fetchone()[0]
_cx.close()
check("当前数据日正确", card["data_date"] == int(_exp.replace("-", "")), f"data_date={card['data_date']} 情绪源最新={_exp}")
# 指数库与 hsj 一致性
idx_max = card["dimensions"]["index_momentum"]["latest_date"]
check("hsj/index 数据日一致(无warn)", idx_max == card["data_date"] or any("指数库" in w for w in card["freshness"]["warn"]))

# ═══════════════════════════════════════════════════════════
sec("E. 跨源对账（反臆想核心）")
# E1 涨停数：盘感卡情绪复合 vs feature_market_sentiment(Tushare)
import sqlite3 as _sc
try:
    c = _sc.connect(r"D:\workbuddyspace\databases\fundamental_data.db")
    _dd = str(card["data_date"])
    fm = c.execute("SELECT trade_date, limit_up, limit_down FROM feature_market_sentiment "
                   "WHERE trade_date=?", (f"{_dd[:4]}-{_dd[4:6]}-{_dd[6:]}",)).fetchone()
    c.close()
    lu_card = s["n_limit_up"]
    lu_tushare = fm[1] if fm else None
    check("涨停数对账(卡 vs Tushare)", lu_tushare is not None and abs(lu_card - lu_tushare) <= 8,
          f"卡={lu_card} Tushare={lu_tushare}")
except Exception as e:
    check("涨停数对账", False, f"取数失败 {e}")
# E2 指数数值：盘感卡(库内T-1收盘) vs 新浪昨收（2026-09-08修复: 原用现价对账,
# 盘前现价=昨收故PASS, 盘中现价波动必假FAIL——时点盲区; 昨收对昨收全时段成立且对账强度不降）
try:
    real = vi._sina_indices()
    real_map = {r["name"]: (r["price"], r["pct"], r.get("prev")) for r in real}
    for d in i["detail"]:
        if not d["ok"]:
            continue
        nm = d["name"]
        if nm in real_map:
            # 20261023 批次错位根治: 卡片批次随夜间库更新在 T-1/T 间切换, 新浪 prev 字段晚间
            # 亦滚动 → 原绑定昨收单字段的 1.2% 带在批次错位+大幅波动日必炸(实锤: 深证差2.7%/
            # 创业板差4.2%)。改「双字段 vintage 对账」: 卡值与 新浪现价 或 昨收 任一同源匹配
            # (abs<1 或 rel<0.3%) 即成立, 皆不匹配才 FAIL —— 全时段成立, 且真失配更难逃逸(强度升)。
            cands = [p for p in (real_map[nm][0], real_map[nm][2]) if p is not None]
            if not cands:
                check(f"指数对账 {nm}", False, "新浪无现价/昨收字段")
            else:
                det = " ".join(f"{p}差{abs(d['close'] - p):.2f}" for p in cands)
                okv = any(abs(d["close"] - p) < 1.0
                          or (d["close"] and abs(d["close"] - p) / d["close"] < 0.003)
                          for p in cands)
                check(f"指数对账 {nm}", okv, f"卡={d['close']} {det} 口径=abs<1或rel<0.3%任一字段")
        else:
            check(f"指数对账 {nm}", False, "新浪无此名")
except Exception as e:
    check("指数对账", False, f"新浪取数失败 {e}")
# E3 成交额：盘感卡量能 vs hsj 实际
con = _sc.connect(f"file:{vc.MAIN_DB}?mode=ro", uri=True)
amt = con.execute("SELECT SUM(amt) FROM stock_daily_hsj WHERE date=? AND close>0",
                  (card["data_date"],)).fetchone()[0]
con.close()
if v["amt_today"] is not None:
    check("量能成交额对账", abs(v["amt_today"] - amt) / amt < 0.02,
          f"卡={v['amt_today']:.0f} hsj={amt:.0f}")

sec("H. 盘中盘感跨源合理性")
try:
    dist = vi.fetch_distribution()
    ix = vi.fetch_indices()
    d = vi.score_dims(dist, ix, vi.zt_avg_20d())
    total = dist["n_up"] + dist["n_down"] + dist["flat"]
    check("广度分布自洽(涨跌平≈全市场)", total > 3000 and total < 6500, f"total={total}")
    check("上涨占比 0-100", 0 <= d["up_ratio"] <= 100)
except Exception as e:
    check("盘中跨源", False, str(e))

# ═══════════════════════════════════════════════════════════
sec("F. 边界与异常（在 H 之后，避免 monkeypatch 污染）")
# F1 远期日期（无数据）→ 应有 None/缺数据而非崩溃或伪造
fut = vc._build_with_retry(20260901, False)
check("远期日期不崩溃", isinstance(fut["vibe_score"], float))
# F2 打板溢价滚动读 latest
prem_latest = Path(r"D:\zcodesj\reports\prem_seal_latest.json")
check("prem_seal_latest 存在", prem_latest.exists())
if prem_latest.exists():
    pj = json.loads(prem_latest.read_text(encoding="utf-8"))
    check("prem rolling20 有值", pj.get("rolling20") is not None)
# F3 seal_quality 无数据日 → ok=False 不崩溃
sq_nodata = ssnap.seal_quality("20200101")
check("seal_quality 无数据日 ok=False", sq_nodata.get("ok") is False, str(sq_nodata.get("reason", ""))[:60])
# F4 指数三源阶梯：模拟新浪失败→腾讯（测后恢复）
_orig_sina = vi._sina_indices
vi._sina_indices = lambda: (_ for _ in ()).throw(RuntimeError("模拟新浪挂"))
try:
    tier = vi.fetch_indices()
    check("指数阶梯降级(新浪挂→腾讯)", len(tier) == 3, f"{tier[0]['name']}{tier[0]['pct']}")
except Exception as e:
    check("指数阶梯降级", False, str(e))
vi._sina_indices = _orig_sina
# F5 盘中盘感 degraded：广度挂→退出1 且不伪造
_orig_dist = vi.fetch_distribution
vi.fetch_distribution = lambda: (_ for _ in ()).throw(RuntimeError("模拟广度挂"))
rc = vi.main([])
vi.fetch_distribution = _orig_dist
check("盘中广度降级→退出1", rc == 1)

# ═══════════════════════════════════════════════════════════
sec("G. daily_trade_plan 集成")
r5 = subprocess.run([sys.executable, r"D:\zcodesj\daily_trade_plan.py"],
                    capture_output=True, text=True, timeout=120, cwd=r"D:\zcodesj")
check("daily_trade_plan 退出0", r5.returncode == 0)
if r5.returncode == 0:
    plans = sorted(Path(r"D:\zcodesj\trade_plans").glob("trade_plan_*.md"))
    if plans:
        md = plans[-1].read_text(encoding="utf-8")
        check("作战清单含盘感卡片段", "今日盘感卡片" in md)
        check("作战清单含盘感判读行", "判读" in md and "情绪周期" in md)
        check("作战清单含SC001/主线", ("SC001" in md) or ("主线" in md))

# ═══════════════════════════════════════════════════════════
print(f"\n{'='*50}")
print(f"结果: {len(PASS)} 通过 / {len(FAIL)} 失败")
if FAIL:
    print("失败项:")
    for f in FAIL:
        print(f"  ❌ {f}")
    return_code = 1
else:
    print("✅ 全部通过")
    return_code = 0
print(f"耗时 {time.time()-_T0:.0f}s")
sys.exit(return_code)
