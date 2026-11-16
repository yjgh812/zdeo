# -*- coding: utf-8 -*-
"""prereg_watch_daily.py —— prereg 观察登记每日触发器（20260905 全面接入·机器触发层）

三个自动探针（每日 schtasks 08:45 直跑，只读零拦截）：
  P1 prereg 登记存活性：data/prereg_*_obs_*.json + PREREG_*.json 自动发现+schema 校验（防丢失/防损坏）
  P2 持仓披露窗探针：当前持仓中处于「ep 上升财报披露后 1-5 个交易日」逆风窗的标的
     （证据=prereg_earnings_upside_sell_obs：ep 升组披露后 1-5 日超额 -0.35% t-15.2 胜率 40%）
  P3 登记复核点到期提醒：review_checkpoint 含季度日期者到期提示

输出：stdout 摘要 + data/prereg_watch_{date}.json
exit：0=正常（含 WARN 提示）/ 2=结构化异常（登记缺失或损坏）
本脚本为只读巡检（除写自身产物），零拦截权——不修改任何信号/闸门/订单。
"""
import glob
import json
import os
import sqlite3
import sys
import time
from datetime import datetime

ROOT = os.path.dirname(os.path.abspath(__file__))
QFQ_DB = "D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db"
PIT_DB = "D:/zcodesj/factor_pit.db"
POS_FILE = os.path.join(ROOT, "data", "positions_latest.json")
TODAY = datetime.now().strftime("%Y%m%d")

result = {"date": TODAY, "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
          "probes": {}, "warn": [], "fail": []}


def save():
    fp = os.path.join(ROOT, "data", f"prereg_watch_{TODAY}.json")
    with open(fp, "w", encoding="utf-8") as fh:
        json.dump(result, fh, ensure_ascii=False, indent=1)


# ---------- P1: prereg 登记存活性 ----------
# P1 扩展(20260913): 除观察类 prereg_*_obs_* 外, 兼扫大写 PREREG_*.json(防线类登记:
# L-2席位walk/breakout-scout/garch-shadow/解禁族/画像启动条件等9件) — 此前仅automation单保险
# glob 在 Windows 不区分大小写 → 用文件名原样前缀判断(排除探针输出prereg_watch_/研究件)
def _is_registry(name):
    if name.startswith("prereg_watch_"):
        return False                       # 本探针输出
    if "_obs_" in name:
        return True                        # 观察类登记
    if name[:7] == "PREREG_":              # 防线类登记(名义大写, 原样名判断)
        return True
    return False
files = sorted(fp for fp in glob.glob(os.path.join(ROOT, "data", "*.json"))
               if _is_registry(os.path.basename(fp)))
# 防线类(PREREG_)schema 与观察类不同: 只做存在性+JSON可解析, 不要求观察类字段
STRICT_KEYS = ("id", "decision_rule_LOCKED", "boundary_LOCKED", "review_checkpoint")
p1 = {"expected_min": 3, "found": len(files), "items": []}
for fp in files:
    name = os.path.basename(fp)
    try:
        d = json.load(open(fp, encoding="utf-8"))
        if name[:7] == "PREREG_" or "登记日" in d:
            # 防线类/中文schema登记: 存在性+可解析即OK(字段schema不同族; band7先例)
            p1["items"].append({"file": name, "status": "OK", "class": "defense"})
            continue
        missing = [k for k in STRICT_KEYS if k not in d]
        if missing:
            p1["items"].append({"file": name, "status": "FAIL", "missing": missing})
            result["fail"].append(f"P1 {name} 缺字段 {missing}")
        else:
            p1["items"].append({"file": name, "status": "OK"})
    except Exception as e:
        p1["items"].append({"file": name, "status": "FAIL", "error": str(e)})
        result["fail"].append(f"P1 {name} 解析失败: {e}")
if len(files) < p1["expected_min"]:
    result["fail"].append(f"P1 prereg 登记数 {len(files)} < 预期 {p1['expected_min']}（疑似丢失）")
result["probes"]["P1_registry_alive"] = p1

# ---------- P2: 持仓披露窗探针 ----------
p2 = {"positions_scanned": 0, "in_window": []}
try:
    pos = json.load(open(POS_FILE, encoding="utf-8"))
    holdings = pos.get("positions", [])
    p2["positions_scanned"] = len(holdings)
    snap_at = str(pos.get("snapshot_at", ""))
    if snap_at and snap_at[:8] < TODAY and TODAY[4:6] == snap_at[4:6]:
        result["warn"].append(f"P2 持仓快照偏旧 snapshot_at={snap_at[:8]}（探针基于旧持仓,建议刷新 positions_latest.json）")
    codes = [h["code"][-6:] for h in holdings if h.get("code")]  # sz300077 -> 300077
    name_by = {h["code"][-6:]: h.get("name", "?") for h in holdings}
    if codes:
        con_q = sqlite3.connect(QFQ_DB)
        cal = [r[0] for r in con_q.execute(
            "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>='20260601' ORDER BY date")]
        con_q.close()
        def trade_lag(d0):
            """今日相对 d0 的交易日偏移(0=同日)"""
            d0 = str(d0)  # SQLite 可能返回 int
            if d0 is None or d0 > TODAY:
                return None
            after = [x for x in cal if d0 < x <= TODAY]
            return len(after)
        con_p = sqlite3.connect(PIT_DB)
        for c in codes:
            rows = con_p.execute(
                "SELECT pit_avail_date, ep FROM stock_factors_fundamental_pit_v2 "
                "WHERE code=? AND pit_avail_date<=? ORDER BY pit_avail_date DESC LIMIT 2", (c, TODAY)).fetchall()
            if len(rows) < 2:
                continue
            avail, ep = rows[0]
            ep_prev = rows[1][1]
            if ep is None or ep_prev is None:
                continue
            lag = trade_lag(avail)
            if lag is not None and 1 <= lag <= 5 and ep > ep_prev:
                item = {"code": c, "name": name_by.get(c, "?"), "pit_avail_date": avail,
                        "trade_days_since": lag, "ep": round(float(ep), 4)}
                p2["in_window"].append(item)
                result["warn"].append(
                    f"P2 持仓 {c} {name_by.get(c,'?')} 处于利好财报披露后第{lag}交易日(1-5日逆风窗,"
                    f"证据=ep升组披露后1-5日-0.35%/t-15.2/胜率40%——仅标注零拦截,处置由主人裁量)")
        con_p.close()
except Exception as e:
    result["fail"].append(f"P2 执行异常: {e}")
result["probes"]["P2_disclosure_window"] = p2

# ---------- P3: 复核点到期提醒（v2 通用化: 正则提取 checkpoint 中全部年月, 当月已过即 due） ----------
import re
p3 = {"checked": len(files), "due": []}
cur_ym = TODAY[:6]
for fp in files:
    try:
        d = json.load(open(fp, encoding="utf-8"))
        rc = str(d.get("review_checkpoint", ""))
        for ym in re.findall(r"20\d{2}-(?:0[1-9]|1[0-2])", rc):
            if cur_ym > ym.replace("-", ""):
                p3["due"].append({"file": os.path.basename(fp), "checkpoint_ym": ym, "raw": rc[:60]})
                result["warn"].append(f"P3 复核点已到期({ym}): {os.path.basename(fp)}")
                break
    except Exception:
        pass
result["probes"]["P3_review_due"] = p3

# ---------- P4: 市场级暴跌日探针（crash_rebound 登记的自动积累承诺） ----------
# 口径与 U5 一致: 全市场(60/00/30前缀)等权日收益 AVG(change_pct), z60 = 当日 / 滚动60日std
p4 = {"series_days": 0, "last_date": None, "last_z": None, "crash_days_recent": []}
try:
    con_q = sqlite3.connect(QFQ_DB)
    rows = con_q.execute(
        "SELECT date, AVG(change_pct) FROM stock_daily_meta_qfq "
        "WHERE date>='20260401' AND (code LIKE '6%' OR code LIKE '0%' OR code LIKE '3%') "
        "GROUP BY date ORDER BY date").fetchall()
    con_q.close()
    rows = [(d, a) for d, a in rows if a is not None]  # 修复: 剔除无change_pct的脏日(基金/指数行)
    dates = [r[0] for r in rows]
    avgs = [r[1] for r in rows]
    p4["series_days"] = len(dates)
    if len(dates) >= 61:
        import statistics
        # 每日 z60: 当日avg / 前60日(不含当日)avg序列std —— 与 U5 口径一致(rolling(60).std shift(1))
        zs = {}
        for i in range(60, len(dates)):
            mu = statistics.fmean(avgs[i - 60:i])
            sd = statistics.pstdev(avgs[i - 60:i])
            if sd > 0:
                zs[dates[i]] = (avgs[i] - mu) / sd
        p4["last_date"] = dates[-1]
        p4["last_z"] = round(zs.get(dates[-1], float("nan")), 3) if dates[-1] in zs else None
        for d, z in zs.items():
            if z < -3 and d >= "20260601":
                p4["crash_days_recent"].append({"date": d, "z": round(z, 2)})
        if dates[-1] in zs and zs[dates[-1]] < -3:
            result["warn"].append(
                "P4 市场级暴跌日(等权z60<-3)——引用 prereg_crash_rebound_obs:历史暴跌后20日+5.32%/胜率85.7%"
                "(n=28),本日计入case序列;仅统计背景零操作指令")
except Exception as e:
    result["fail"].append(f"P4 执行异常: {e}")
result["probes"]["P4_crash_probe"] = p4

# ---------- 汇总输出 ----------
status = "FAIL" if result["fail"] else ("WARN" if result["warn"] else "OK")
result["status"] = status
print(f"[prereg_watch {TODAY}] status={status}")
for w in result["warn"]:
    print("  WARN:", w)
for f_ in result["fail"]:
    print("  FAIL:", f_)
if not result["warn"] and not result["fail"]:
    print("  全部探针正常: 登记存活 + 持仓无披露窗逆风 + 复核点未到期")
save()
sys.exit(2 if result["fail"] else 0)
