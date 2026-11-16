# -*- coding: utf-8 -*-
"""tulong_dashboard — 屠龙轻量 Web 面板 v1 (FastAPI + SSE)  端口 3019"""

from __future__ import annotations
import asyncio, json, os, sqlite3, sys, numpy as np
from datetime import datetime
from pathlib import Path
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from sse_starlette.sse import EventSourceResponse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
app = FastAPI(title="屠龙面板 v1", version="1.0")

MAIN_DB = "D:/workbuddyspace/factor_engine/data/dragon_quant.db"
ATTR_DB = "D:/zcodesj/attribution/attribution.db"  # 2026-08-24 双库统一:指向唯一真源
TEMPLATE = Path(__file__).resolve().parent / "templates" / "dashboard.html"


def _pct_distribution():
    """pandas版涨跌幅分布，无SQL自连接问题。"""
    try:
        con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=15)
        import pandas as pd
        df = pd.read_sql("SELECT code, close, vol, date FROM stock_daily_hsj "
                         "WHERE date>20260720 AND vol>0", con)
        con.close()
    except Exception as e:
        return {"pcts": [], "error": f"DB: {e}"}
    try:
        df = df.sort_values(["code", "date"])
        df["prev"] = df.groupby("code")["close"].shift(1)
        latest = df[df.date == df.date.max()].dropna(subset=["prev"])
        pcts = ((latest["close"] / latest["prev"] - 1) * 100).tolist()
        pcts = [p for p in pcts if abs(p) < 50]
        return {"pcts": pcts, "date": str(df.date.max())}
    except Exception as e:
        return {"pcts": [], "error": f"calc: {e}"}


@app.get("/", response_class=HTMLResponse)
async def index():
    if TEMPLATE.exists():
        return TEMPLATE.read_text(encoding="utf-8")
    return "tpl missing"


@app.get("/api/overview")
async def overview():
    maxd, pos = "?", 0
    try:
        con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=10)
        maxd = str(con.execute("SELECT MAX(date) FROM stock_daily_hsj").fetchone()[0])
        pos = con.execute("SELECT COUNT(*) FROM positions WHERE quantity>0").fetchone()[0]
        con.close()
    except Exception:
        pass
    sealed = broken = 0
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=10)
        r = con.execute("SELECT COALESCE(SUM(sealed),0), COALESCE(SUM(broken),0) "
                        "FROM seal_snapshot WHERE date=(SELECT MAX(date) FROM seal_snapshot)").fetchone()
        if r:
            sealed, broken = int(r[0]), int(r[1])
        ev = con.execute("SELECT COUNT(*) FROM monitor_events").fetchone()[0]
        con.close()
    except Exception:
        ev = 0
    now = datetime.now()
    sess = now.weekday() < 5 and (930 <= now.hour * 100 + now.minute <= 1500)
    sr = round(sealed / (sealed + broken) * 100, 1) if (sealed + broken) else 0
    return {"date": maxd, "session": sess, "positions": pos,
            "sealed": sealed, "broken": broken, "seal_rate": sr, "events": ev}


@app.get("/api/emotion")
async def emotion():
    d = _pct_distribution()
    pcts = d["pcts"]
    if not pcts:
        return {"score": 50, "label": "震荡", "error": d.get("error", "no data")}
    tot = len(pcts)
    up = sum(1 for p in pcts if p > 0); down = tot - up
    su = sum(1 for p in pcts if p >= 3); sd = sum(1 for p in pcts if p <= -3)
    from emotion_radar import compute_radar
    r = compute_radar({
        "index_avg_pct": 0.0,
        "up_pct": round(up / tot * 100, 1),
        "avg_pct": round(float(np.mean(pcts)), 2),
        "median_pct": round(float(np.median(pcts)), 2),
        "strong_diff_pct": round((su - sd) / tot * 100, 1),
        "avg_vol_ratio": 1.0, "high_vol_ratio_pct": 0,
        "limit_up_count": 60, "seal_rate": 60, "max_boards": 5, "tier2_count": 18,
        "down_pct": round(down / tot * 100, 1),
        "strong_down_pct": round(sd / tot * 100, 1),
        "mainline_avg_pct": 3.0, "mainline_cover_pct": 7.0,
    })
    return r


@app.get("/api/rps/{kind}")
async def rps(kind: str):
    from rps_rotation import load_pct_matrix, load_dimension_map
    days = 8
    pct = load_pct_matrix(days)
    dmap = load_dimension_map(kind, from_db=True)
    j = pct.merge(dmap, on="code", how="inner")
    cnt = j.groupby(["date", "dim"])["code"].nunique()
    good = cnt[cnt >= 4].index
    j = j.set_index(["date", "dim"]).loc[good].reset_index()
    agg = j.groupby(["date", "dim"])["pct"].mean().rename("avg_pct").reset_index()
    agg["rank"] = agg.groupby("date")["avg_pct"].rank(ascending=False, method="min")
    n = agg.groupby("date")["dim"].transform("count")
    agg["rps"] = (1.0 - agg["rank"] / n) * 100
    dates = sorted(agg["date"].unique())
    if not dates:
        return {"today_top": [], "mainline": [], "as_of": ""}
    today = dates[-1]
    top10 = agg[agg.date == today].nsmallest(10, "rank")[["dim", "avg_pct", "rps"]].to_dict(orient="records")
    ml = agg[agg.date.isin(dates[-5:])].groupby("dim")["rps"].mean().nlargest(5).to_dict()
    return {"today_top": top10, "mainline": ml, "as_of": str(today)}


@app.get("/api/positions")
async def positions():
    try:
        con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True, timeout=10)
        rows = con.execute(
            "SELECT code, name, quantity, avg_cost, current_price, profit, market_value "
            "FROM positions WHERE quantity>0 ORDER BY profit").fetchall()
        con.close()
    except Exception:
        return {"items": [], "total": 0, "pnl": 0}
    items = [{"code": r[0][-6:], "name": r[1], "qty": r[2],
              "cost": round(r[3], 2), "price": round(r[4], 2) if r[4] else 0,
              "profit": round(r[5], 2) if r[5] else 0,
              "mv": round(r[6], 2) if r[6] else 0} for r in rows]
    return {"items": items, "total": len(items), "pnl": round(sum(i["profit"] for i in items), 2)}


@app.get("/api/seal")
async def seal():
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=10)
        r = con.execute("SELECT date, COALESCE(SUM(sealed),0), COALESCE(SUM(broken),0) "
                        "FROM seal_snapshot WHERE date=(SELECT MAX(date) FROM seal_snapshot)").fetchone()
        if not r:
            return {"date": "", "sealed": 0, "broken": 0, "seal_rate": 0, "top10": []}
        d, sealed, broken = str(r[0]), int(r[1]), int(r[2])
        top = con.execute("SELECT code, name, sealed_amount FROM seal_snapshot "
                          "WHERE date=? AND sealed=1 ORDER BY sealed_amount DESC LIMIT 10", (r[0],)).fetchall()
        con.close()
    except Exception:
        return {"date": "", "sealed": 0, "broken": 0, "seal_rate": 0, "top10": []}
    tot = sealed + broken
    return {"date": d, "sealed": sealed, "broken": broken,
            "seal_rate": round(sealed / tot * 100, 1) if tot else 0,
            "top10": [{"code": x[0], "name": x[1], "amount_yi": round(x[2] / 1e8, 2)} for x in top]}


@app.get("/api/events")
async def events(limit: int = 20):
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=10)
        rows = con.execute("SELECT ts, rule_id, rule_name, code, name, severity, msg "
                           "FROM monitor_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        con.close()
    except Exception:
        return {"items": []}
    return {"items": [{"ts": r[0], "rule_id": r[1], "rule_name": r[2], "code": r[3],
                       "name": r[4], "severity": r[5], "msg": r[6]} for r in rows]}


@app.get("/api/stream")
async def event_stream():
    async def gen():
        last_id = 0
        try:
            con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=10)
            last_id = con.execute("SELECT COALESCE(MAX(id),0) FROM monitor_events").fetchone()[0]
            con.close()
        except Exception:
            pass
        while True:
            try:
                con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True, timeout=10)
                new = con.execute("SELECT id, ts, rule_id, rule_name, code, name, severity, msg "
                                  "FROM monitor_events WHERE id>? ORDER BY id", (last_id,)).fetchall()
                con.close()
                for n in new:
                    last_id = n[0]
                    yield {"event": "monitor",
                           "data": json.dumps({"ts": n[1], "rule_id": n[2], "rule_name": n[3],
                                               "code": n[4], "name": n[5], "severity": n[6],
                                               "msg": n[7]}, ensure_ascii=False)}
            except Exception:
                pass
            await asyncio.sleep(5)
    return EventSourceResponse(gen())


# ═══════════════════════════════════════════════════════════════
# A6 纪律看板（2026-08-16 新增，数据源 discipline_stats.py → discipline_daily 表）
# ═══════════════════════════════════════════════════════════════

DISC_DB = "D:/zcodesj/attribution.db"   # 看板库（与 seal_snapshot 同库，3019 直读）
REPORTS_DIR = "D:/zcodesj/reports"


def _disc_con():
    return sqlite3.connect(f"file:{DISC_DB}?mode=ro", uri=True, timeout=10)


def _latest_discipline_date(con) -> str:
    try:
        return str(con.execute("SELECT MAX(date) FROM discipline_daily").fetchone()[0])
    except Exception:
        return ""


@app.get("/api/discipline")
async def discipline(date: str = ""):
    """当日纪律摘要：裸奔/合规率/四类违规。date 缺省取最新。"""
    try:
        con = _disc_con()
        if not date:
            date = _latest_discipline_date(con)
        r = con.execute(
            "SELECT * FROM discipline_daily WHERE date=?",
            (date,)).fetchone()
        con.close()
    except Exception as e:
        return {"error": str(e)}
    if not r:
        return {"error": f"no data for {date}", "date": date}
    cols = ["date", "level", "buy_total", "precheck_calls", "naked_buys",
            "compliance_rate", "batch_reject", "overprice", "offplan",
            "other_reject", "minute_peak", "minute_peak_min", "minute_level",
            "violations_json", "created_at"]
    d = dict(zip(cols, r))
    try:
        extra = json.loads(d.pop("violations_json") or "{}")
    except Exception:
        extra = {}
    return {**d, **{"details": extra.get("naked_detail", [])[:20],
                    "overprice_detail": extra.get("overprice_detail", [])[:20],
                    "offplan_detail": extra.get("offplan_detail", [])[:20]}}


@app.get("/api/discipline/trend")
async def discipline_trend(days: int = 20):
    """近 N 日纪律趋势：逐日合规率 + 违规计数堆叠。"""
    try:
        con = _disc_con()
        rows = con.execute(
            "SELECT date, level, buy_total, naked_buys, compliance_rate, "
            "batch_reject, overprice, offplan, other_reject, minute_peak "
            "FROM discipline_daily ORDER BY date DESC LIMIT ?", (min(max(days, 1), 90),)).fetchall()
        con.close()
    except Exception as e:
        return {"error": str(e)}
    return {"trend": [{
        "date": r[0], "level": r[1], "buy_total": r[2], "naked_buys": r[3],
        "compliance_rate": r[4], "batch": r[5], "overprice": r[6],
        "offplan": r[7], "other": r[8], "minute_peak": r[9],
    } for r in reversed(rows)]}


@app.get("/api/weekly")
async def weekly():
    """周报：近 7 日纪律聚合。"""
    try:
        con = _disc_con()
        rows = con.execute(
            "SELECT date, level, buy_total, naked_buys, compliance_rate, "
            "batch_reject, overprice, offplan, other_reject FROM discipline_daily "
            "ORDER BY date DESC LIMIT 7").fetchall()
        con.close()
    except Exception as e:
        return {"error": str(e)}
    rows = list(reversed(rows))
    tot_buy = sum(r[2] for r in rows)
    tot_naked = sum(r[3] for r in rows)
    comp = round((tot_buy - tot_naked) / tot_buy * 100, 1) if tot_buy else 100.0
    return {
        "days": len(rows),
        "total_buy": tot_buy, "total_naked": tot_naked, "compliance_rate": comp,
        "batch": sum(r[5] for r in rows), "overprice": sum(r[6] for r in rows),
        "offplan": sum(r[7] for r in rows), "other": sum(r[8] for r in rows),
        "daily": [{"date": r[0], "level": r[1], "buy_total": r[2], "naked_buys": r[3],
                   "compliance_rate": r[4], "batch": r[5], "overprice": r[6],
                   "offplan": r[7], "other": r[8]} for r in rows],
    }


@app.get("/api/health")
async def health():
    """健康巡检摘要：读 health_check_*.json + system_health_*.json 现成产物。"""
    import glob as _glob
    hc = sorted(_glob.glob(os.path.join(REPORTS_DIR, "health_check_*.json")))
    sh = sorted(_glob.glob(os.path.join(REPORTS_DIR, "system_health_*.json")))
    out = {"health_check": None, "system_health": None, "reports_dir": REPORTS_DIR}
    if hc:
        try:
            with open(hc[-1], encoding="utf-8") as f:
                d = json.load(f)
            out["health_check"] = {
                "file": os.path.basename(hc[-1]), "level": d.get("level"),
                "expected_trading_date": d.get("expected_trading_date"),
                "vipdoc_max_date": d.get("vipdoc_max_date"),
                "gate_smoke": d.get("gate_smoke"), "disk_free_gb": d.get("disk_free_gb"),
                "errors": d.get("errors", []), "warnings": d.get("warnings", []),
            }
        except Exception as e:
            out["health_check"] = {"error": str(e)}
    if sh:
        try:
            with open(sh[-1], encoding="utf-8") as f:
                d = json.load(f)
            out["system_health"] = {"file": os.path.basename(sh[-1]),
                                    "passed": d.get("passed"), "failed": d.get("failed"),
                                    "total": d.get("total")}
        except Exception as e:
            out["system_health"] = {"error": str(e)}
    return out