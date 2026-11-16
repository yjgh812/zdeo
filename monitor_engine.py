# -*- coding: utf-8 -*-
"""
monitor_engine.py — 盯盘股监控规则引擎 (移植自 TSP monitor_rules DSL, 轻量版)
===============================================================================
来源: TSP backend/app/strategy/monitor_rules.py 规则模型
      {field, op, value} 条件 + and/or + cooldown + severity + fail-closed
适配: 屠龙盯盘场景, 腾讯实时快照, JSON 一规则一文件, 事件持久化。

规则目录: D:/zcodesj/monitor_rules/*.json  (首跑自动创建示例)
字段白名单(实时可得):
  pct          涨跌幅 %            (op: > >= < <= == !=)
  price        现价
  vol_hand     成交量(手)
  amount_wan   成交额(万元)
  limit_up     当前封涨停(bool, op 固定 truth)
  limit_touched 已触涨停价(bool)
  broken       曾触涨停现已开板(bool)  ← 涨停开板预警核心字段
  near_limit   距涨停空间 %(涨停价/现价-1)*100)

冷却: 同 (rule_id, code) 在 cooldown_minutes 内只报一次; 重启不丢(读库)。
哨兵: symbols: ["AUTO_POSITIONS"] = 自动跟随主库 positions 表当前持仓(调仓免维护)。
守卫: 默认仅交易日时段(周一~五 09:30-11:30/13:00-15:00)且快照为当日数据才评估;
      --force 绕过守卫(测试/盘后回看用)。

用法:
  python monitor_engine.py                  # 评估全部启用规则(交易时段守卫)
  python monitor_engine.py --force          # 绕过守卫(测试)
  python monitor_engine.py --cron           # 静默模式(只写库, 供定时任务)
  python monitor_engine.py --list           # 列出规则
事件表: attribution.db monitor_events(id, ts, rule_id, rule_name, code, name,
                                      severity, msg)
建议挂载: 交易日 cron 每5分钟, 命中即知微播报。
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import subprocess
import sys
import time
import urllib.request
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from price_limits import is_limit_up, limit_price, price_limit_pct  # noqa: E402

RULE_DIR = Path(r"D:\zcodesj\monitor_rules")
ATTR_DB = Path(r"D:\zcodesj\attribution\attribution.db")  # 2026-08-24 双库统一:指向唯一真源
MAIN_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
POSITIONS_SENTINEL = "AUTO_POSITIONS"

ALLOWED_FIELDS = {"pct", "price", "vol_hand", "amount_wan",
                  "limit_up", "limit_touched", "broken", "near_limit"}
BOOL_FIELDS = {"limit_up", "limit_touched", "broken"}
OPS = {">", ">=", "<", "<=", "==", "!="}
LOGICS = {"and", "or"}
SEVERITIES = {"info", "warn", "critical"}
ID_RE = re.compile(r"^[a-z0-9_]{1,40}$")

EVENTS_TBL = """
CREATE TABLE IF NOT EXISTS monitor_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, rule_id TEXT, rule_name TEXT,
    code TEXT, name TEXT, severity TEXT, msg TEXT
)"""

SAMPLE_RULES = [
    {
        "id": "drop_3pct", "name": "持仓跌超3%", "enabled": True,
        "symbols": [POSITIONS_SENTINEL],
        "conditions": [{"field": "pct", "op": "<=", "value": -3.0}],
        "logic": "and", "cooldown_minutes": 30, "severity": "warn",
        "_说明": "AUTO_POSITIONS=自动跟随主库持仓表; 手工名单则填6位码数组",
    },
    {
        "id": "limit_broken", "name": "涨停开板预警", "enabled": True,
        "symbols": [], "conditions": [{"field": "broken", "op": "truth"}],
        "logic": "and", "cooldown_minutes": 15, "severity": "critical",
        "_说明": "曾触涨停现已回落 → 开板/炸板即时预警",
    },
    {
        "id": "near_limit_1pct", "name": "逼近涨停(距板<1%)", "enabled": True,
        "symbols": [], "conditions": [{"field": "near_limit", "op": "<=", "value": 1.0},
                                      {"field": "pct", "op": ">", "value": 5.0}],
        "logic": "and", "cooldown_minutes": 10, "severity": "info",
        "_说明": "现价距涨停价不足1%且已涨5%+",
    },
]


def validate(rule: dict) -> None:
    """fail-closed 校验(TSP 同款思想): 任何字段非法 → 拒载该规则。"""
    rid = rule.get("id", "")
    if not ID_RE.match(str(rid)):
        raise ValueError(f"id 非法: {rid!r}")
    if not str(rule.get("name", "")).strip():
        raise ValueError("name 不能为空")
    if rule.get("logic", "and") not in LOGICS:
        raise ValueError("logic 必须是 and/or")
    if rule.get("severity", "info") not in SEVERITIES:
        raise ValueError(f"severity 必须是 {SEVERITIES}")
    syms = rule.get("symbols", [])
    ok_sym = lambda s: s == POSITIONS_SENTINEL or bool(re.match(r"^\d{6}$", s))  # noqa: E731
    if not isinstance(syms, list) or not all(ok_sym(s) for s in syms):
        raise ValueError("symbols 必须是6位码数组或 ['AUTO_POSITIONS']")
    conds = rule.get("conditions", [])
    if not isinstance(conds, list) or not conds:
        raise ValueError("conditions 不能为空")
    for c in conds:
        if c.get("field") not in ALLOWED_FIELDS:
            raise ValueError(f"field 白名单外: {c.get('field')}")
        if c["field"] in BOOL_FIELDS:
            if c.get("op") != "truth":
                raise ValueError(f"{c['field']} 只支持 op=truth")
        elif c.get("op") not in OPS:
            raise ValueError(f"op 非法: {c.get('op')}")


def current_positions() -> list[str]:
    """主库 positions 表当前持仓(6位码)。哨兵展开用, 调仓自动跟随。"""
    con = sqlite3.connect(f"file:{MAIN_DB}?mode=ro", uri=True)
    rows = [r[0] for r in con.execute("SELECT code FROM positions WHERE quantity > 0").fetchall()]
    con.close()
    return sorted({c[-6:] for c in rows})


def load_rules() -> list[dict]:
    RULE_DIR.mkdir(exist_ok=True)
    if not any(RULE_DIR.glob("*.json")):
        for r in SAMPLE_RULES:
            (RULE_DIR / f"{r['id']}.json").write_text(
                json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[monitor] 初始化示例规则 {len(SAMPLE_RULES)} 条 → {RULE_DIR}")
    out = []
    for fp in sorted(RULE_DIR.glob("*.json")):
        try:
            r = json.loads(fp.read_text(encoding="utf-8"))
            validate(r)
            if r.get("enabled", True) and r.get("symbols"):
                out.append(r)
        except Exception as e:
            print(f"[warn] 规则 {fp.name} 校验失败跳过(fail-closed): {e}")
    return out


def _suffix(code: str) -> str:
    return ("sh" if code.startswith(("6", "9")) else "sz") + code


def fetch_quotes(codes: list[str]) -> dict[str, dict]:
    """腾讯批量快照 → {code: 指标dict}。腾讯全失败/空时用小石全市场快照兜底。"""
    out: dict[str, dict] = {}
    today = date.today()
    for i in range(0, len(codes), 60):
        q = ",".join(_suffix(c) for c in codes[i:i + 60])
        try:
            req = urllib.request.Request(
                f"https://qt.gtimg.cn/q={q}",
                headers={"User-Agent": "Mozilla/5.0", "Referer": "https://gu.qq.com/"})
            raw = urllib.request.urlopen(req, timeout=15).read().decode("gbk", errors="replace")
        except Exception as e:
            print(f"[warn] 批次拉取失败: {e}")
            continue
        for seg in raw.strip().split(";"):
            if "=" not in seg:
                continue
            f = seg.split("=")[1].strip('"').split("~")
            if len(f) < 40:
                continue
            try:
                code, name = f[2], f[1]
                price, prev, high = float(f[3] or 0), float(f[4] or 0), float(f[33] or 0)
                pct, vol = float(f[32] or 0), float(f[6] or 0)
                amount = float(f[37] or 0) * 10000  # f[37]=成交额(万)
                lp = limit_price(prev, price_limit_pct(code, today)) if prev > 0 else 0.0
                sealed = bool(lp and price >= lp - 0.005)
                touched = bool(lp and high >= lp - 0.005)
                out[code] = {
                    "name": name, "price": price, "pct": pct, "vol_hand": vol,
                    "amount_wan": amount / 1e4, "data_ts": f[30] if len(f) > 30 else "",
                    "limit_up": sealed, "limit_touched": touched,
                    "broken": touched and not sealed,
                    # 已封板 → 99(不触发"逼近涨停"; 封板另有 limit_up 字段可配规则)
                    "near_limit": (lp / price - 1) * 100 if (lp and 0 < price < lp - 0.005) else 99.0,
                }
            except (ValueError, IndexError):
                continue
        time.sleep(0.1)
    # 小石兜底: 腾讯全部失败/空, 且确有需要监控的代码时(非交易时段也保持可用, 由调用方守卫判断)
    if not out and codes:
        try:
            sys.path.insert(0, r"D:\zcodesj\xiaoshi")
            from xiaoshi_market_bridge import fetch_quotes_fallback
            out = fetch_quotes_fallback(codes)
            print(f"[monitor] 腾讯快照失败, 已用小石全市场快照兜底 {len(out)}/{len(codes)} 只")
        except Exception as e:
            print(f"[warn] 小石兜底也失败: {e}")
    return out


def _hit(cond: dict, m: dict) -> bool:
    if cond["op"] == "truth":
        return bool(m[cond["field"]])
    v, t = m[cond["field"]], cond["value"]
    return {">": v > t, ">=": v >= t, "<": v < t, "<=": v <= t,
            "==": v == t, "!=": v != t}[cond["op"]]


def _cooled(con: sqlite3.Connection, rid: str, code: str, minutes: int) -> bool:
    row = con.execute(
        "SELECT ts FROM monitor_events WHERE rule_id=? AND code=? ORDER BY id DESC LIMIT 1",
        (rid, code)).fetchone()
    if not row:
        return True
    try:
        last = datetime.fromisoformat(row[0])
        return (datetime.now() - last).total_seconds() >= minutes * 60
    except ValueError:
        return True


def evaluate(verbose: bool = True, force: bool = False) -> list[dict]:
    rules = load_rules()
    if verbose:
        print(f"[monitor] 启用规则 {len(rules)} 条: {[r['id'] for r in rules]}")
    if not rules:
        return []
    # 哨兵展开: AUTO_POSITIONS → 当前持仓(调仓自动跟随)
    symbols: set[str] = set()
    for r in rules:
        for s in r["symbols"]:
            if s == POSITIONS_SENTINEL:
                pos = current_positions()
                if verbose:
                    print(f"[monitor] 规则 {r['id']} 哨兵展开 → 持仓 {len(pos)} 只")
                symbols.update(pos)
            else:
                symbols.add(s)
    quotes = fetch_quotes(sorted(symbols))
    if not quotes:
        return []
    # 守卫: 交易日时段 + 快照为当日数据(防节假日/盘后旧数据误报); --force 绕过
    if not force:
        sample_ts = next(iter(quotes.values())).get("data_ts", "")[:8]
        now = datetime.now()
        t = now.hour * 100 + now.minute
        in_session = (now.weekday() < 5) and (930 <= t <= 1130 or 1300 <= t <= 1500)
        fresh = sample_ts == date.today().strftime("%Y%m%d")
        if not (in_session and fresh):
            if verbose:
                print(f"[monitor] 守卫拦截(非交易时段或快照非当日 {sample_ts}), 退出; --force 可绕过")
            return []
    con = sqlite3.connect(str(ATTR_DB), timeout=30)
    con.execute("PRAGMA busy_timeout=30000")
    con.execute(EVENTS_TBL)
    events = []
    for r in rules:
        # 2026-08-24 P0修复: AUTO_POSITIONS 哨兵此前只在取数阶段展开，评估阶段
        # quotes.get("AUTO_POSITIONS") 恒 None → 全持仓保护规则(drop_3pct等)永不
        # 触发。评估循环内同样按当前持仓展开。
        rule_symbols: list[str] = []
        for s in r["symbols"]:
            if s == POSITIONS_SENTINEL:
                rule_symbols.extend(current_positions())
            else:
                rule_symbols.append(s)
        for s in rule_symbols:
            m = quotes.get(s)
            if not m:
                continue
            hits = [_hit(c, m) for c in r["conditions"]]
            ok = all(hits) if r.get("logic", "and") == "and" else any(hits)
            if ok and _cooled(con, r["id"], s, int(r.get("cooldown_minutes", 15))):
                msg = (f"{s} {m['name']} 现价{m['price']:.2f} ({m['pct']:+.2f}%) "
                       f"量{m['vol_hand']:.0f}手 → {r['name']}")
                ts = datetime.now().isoformat(timespec="seconds")
                con.execute(
                    "INSERT INTO monitor_events(ts,rule_id,rule_name,code,name,severity,msg) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (ts, r["id"], r["name"], s, m["name"], r.get("severity", "info"), msg))
                events.append({"rule": r["id"], "severity": r.get("severity"), "msg": msg})

    # ── 持仓已验证风险特征扫描（2026-08-18 主人指令「接」接入）──
    # verified_risk_flags = 2026-08-18 全市场验证的四类下跌特征(qfq最近收盘日信号)，
    # 与 DSL 的 broken(盘中实时炸板)互补；冷却240分钟≈每交易日提醒一次
    try:
        import verified_risk_flags as vrf
        for s in current_positions():
            fl = vrf.compute_risk_flags(s)
            if fl.get("error"):
                continue
            hints = vrf.flags_to_hints(fl)
            if hints and _cooled(con, "vrf_risk", s, 240):
                nm = quotes.get(s, {}).get("name", "")
                ts2 = datetime.now().isoformat(timespec="seconds")
                msg2 = f"{s} {nm} 最近收盘日信号({fl.get('date')}): {'; '.join(hints)}"
                con.execute(
                    "INSERT INTO monitor_events(ts,rule_id,rule_name,code,name,severity,msg) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (ts2, "vrf_risk", "持仓已验证风险特征", s, nm, "warn", msg2))
                events.append({"rule": "vrf_risk", "severity": "warn", "msg": msg2})
    except Exception as e:
        if verbose:
            print(f"[monitor] vrf风险扫描异常(不影响主流程): {type(e).__name__} {e}")

    # 盘中技术信号扫描（轻量，~2s/20只持仓，填补技术信号监控空白）
    try:
        r = subprocess.run(
            [sys.executable, r"D:\zcodesj\intraday_tech_scanner.py",
             "--positions", "--alert-only", "--json"],
            capture_output=True, text=True, timeout=30,
            encoding="utf-8", errors="replace")
        if r.returncode == 0 and r.stdout.strip().startswith("["):
            techs = json.loads(r.stdout)
            for te in techs:
                sigs = te.get("signals") or []
                if not sigs:
                    continue  # --alert-only 只作用于scanner的markdown报告，JSON仍含全量持仓，须在此过滤
                code = te.get("code", "")
                if not _cooled(con, "tech_signal", code, 240):
                    continue
                detail = "; ".join(s.get("detail", "") for s in sigs)
                msg3 = f"{code} {te.get('name', '')} {te.get('overall', '')}: {detail}"
                ts3 = datetime.now().isoformat(timespec="seconds")
                con.execute(
                    "INSERT INTO monitor_events(ts,rule_id,rule_name,code,name,severity,msg) "
                    "VALUES (?,?,?,?,?,?,?)",
                    (ts3, "tech_signal", "盘中技术信号",
                     code, te.get("name", ""),
                     "warn", msg3))
                events.append({"rule": "tech_signal", "severity": "warn",
                               "msg": msg3})
    except Exception as e:
        if verbose:
            print(f"[monitor] 技术信号扫描异常(不影响主流程): {type(e).__name__} {e}")

    con.commit()
    con.close()
    return events


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="盯盘股监控规则引擎(TSP DSL 轻量版)")
    ap.add_argument("--cron", action="store_true", help="静默模式(命中才输出)")
    ap.add_argument("--force", action="store_true", help="绕过交易时段/数据新鲜度守卫(测试用)")
    ap.add_argument("--list", action="store_true", help="列出规则")
    a = ap.parse_args()
    if a.list:
        for r in load_rules():
            print(f"  {r['id']:<18} {r['name']:<16} {len(r['symbols'])}symbols "
                  f"{len(r['conditions'])}条件 cooldown={r.get('cooldown_minutes')}min")
    else:
        evs = evaluate(verbose=not a.cron, force=a.force)
        for e in evs:
            tag = {"critical": "🔴", "warn": "🟡", "info": "🔵"}[e["severity"]]
            print(f"{tag} {e['msg']}")
        if not a.cron:
            print(f"[monitor] 命中 {len(evs)} 条事件" + ("(已写库)" if evs else ""))
