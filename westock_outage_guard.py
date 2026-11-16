# -*- coding: utf-8 -*-
"""westock 断供降级守护 + 盘中三探针 (2026-09-06, A1/A2 实施设计稿落地)

裁决边界 (主人 9/6): 腾讯自选股模拟盘非实盘可买卖测试; 断供自动兜底=告警+自动直跑。
物理约束: westock API 断供期间无法下单 -> 自动兜底只能在恢复后执行 (flush)。
铁律不变: 一切买卖走 trade_executor/stop_loss_executor 双层防线, 本守护不直接下单。

模式:
  --probe     断供状态机探测 (挂 14:30 cron 末尾或独立 schtasks 每 30 分钟)
  --intraday  盘中三探针: westock 真值 + TickFlow:3018 + qfq 当日新鲜度 (10:35/13:35)
  --preopen   执行链直连通道开盘前探针 (08:45, 2026-11-09 新增)
  --dry-run   模拟断供全链验证 (WESTOCK_GUARD_FORCE=error|ok 强制分支, 不真改 token)

关键事实 (9/5-9/6 实证):
  - westock 端点签发 refresh_token 但拒绝 refresh grant(400), 且 WeStockClient.refresh
    返空会覆盖好 token -> refresh 绝对禁用, 恢复唯一路径 = 人工 PKCE (westock_oauth.py)
  - token 寿命 expires_in~100h, health 已有 <=6h 预警 -> 守护同步提前提醒
  - 恢复后 flush = 直接调 stop_loss_executor.py --execute (它自读实时持仓逐只算浮亏,
    <=-8% 自动卖, S-5 铁律本就自动; 本守护不重写卖单逻辑)

状态机:
  OK     -> 若 state 有 pending 清单: flush(stop_loss_executor --execute) + notify, 清 state
  WARN   -> 重签提醒 (同日去重)
  ERROR  -> 首次: notify 强告警 + 生成本地止损清单存 state; 后续: 清单变化才再 notify
退出码: 0 正常 / 3 断供中(已告警) / 2 内部失败(fail-closed)
"""
import argparse
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")

BASE = r"D:\zcodesj"
STATE_F = os.path.join(BASE, "data", "westock_outage_state.json")
POS_SNAP = os.path.join(BASE, "data", "positions_latest.json")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
TICKFLOW_HOST, TICKFLOW_PORT = "127.0.0.1", 3018
STOPLOSS_PCT = -8.0


def _notify(title: str, content: str, level: str = "warn") -> None:
    try:
        subprocess.run(
            [sys.executable, os.path.join(BASE, "notify.py"), "send",
             "--title", title, "--content", content, "--level", level],
            capture_output=True, text=True, timeout=30)
    except Exception as e:
        print(f"[WARN] notify 失败({e}) - 仅打日志")
    print(f"[notify:{level}] {title} | {content[:120]}")


def _load_state() -> dict:
    try:
        return json.load(open(STATE_F, encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(st: dict) -> None:
    json.dump(st, open(STATE_F, "w", encoding="utf-8"), ensure_ascii=False, indent=1)


def _qfq_latest_close() -> dict:
    """code(str 6位) -> (date, close)，取每只最新一行"""
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    codes = _pending_codes_from_positions()
    out = {}
    if not codes:
        con.close()
        return out
    ph = ",".join("?" * len(codes))
    rows = con.execute(
        f"""SELECT code, MAX(date), close FROM stock_daily_meta_qfq
            WHERE code IN ({ph}) GROUP BY code""", codes).fetchall()
    con.close()
    for code, d, close in rows:
        out[str(code)] = (d, float(close))
    return out


def _pending_codes_from_positions() -> list:
    try:
        snap = json.load(open(POS_SNAP, encoding="utf-8"))
        return [p["code"][-6:] for p in snap.get("positions", [])]
    except (OSError, ValueError, KeyError):
        return []


def gen_local_stoploss_list() -> list:
    """本地止损清单: positions_latest costPrice vs qfq 最新收盘, 跌幅<=-8% 触发。fail-closed: 缺数据如实空+标注。"""
    try:
        snap = json.load(open(POS_SNAP, encoding="utf-8"))
    except (OSError, ValueError):
        return []
    closes = _qfq_latest_close()
    out = []
    for p in snap.get("positions", []):
        code6 = str(p.get("code", ""))[-6:]
        try:
            cost = float(p.get("costPrice") or 0)
        except (TypeError, ValueError):
            continue
        px = closes.get(code6)
        if not cost or not px:
            continue
        chg = (px[1] / cost - 1) * 100
        if chg <= STOPLOSS_PCT:
            out.append({"code": code6, "name": p.get("name", ""), "cost": cost,
                        "last_close": px[1], "px_date": px[0], "pnl_pct": round(chg, 2)})
    return out


def _tickflow_alive() -> bool:
    try:
        with socket.create_connection((TICKFLOW_HOST, TICKFLOW_PORT), timeout=3):
            return True
    except OSError:
        return False


def _qfq_freshness() -> str:
    """qfq MAX(date) 与最近已收盘交易日差。
    🔴2026-10-01 修复: 原整数日期减法跨月 bug(10/1 的 today-1=20261000, 减 20260930=假 lag 70
    → 假期每日 FAIL 误报) + 无假日感知。改为: 日历化回退找最近已收盘交易日
    (trade_day_guard.is_trading_day 单一真源, 2781178 同法; 日历不可用 fail-open 用自然日回退)。"""
    con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True, timeout=60)
    mx = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    con.close()
    expect = None
    try:
        from trade_day_guard import is_trading_day
        from datetime import timedelta
        d = datetime.now().date()
        # 从今天起回退, 找最近一个已收盘交易日——最多回看 30 自然日
        for k in range(0, 31):
            cand = d - timedelta(days=k)
            c8 = int(cand.strftime("%Y%m%d"))
            if k == 0:
                if datetime.now().hour < 15:
                    continue   # 今日未收盘, 不作为预期日
            if is_trading_day(c8) is True:
                expect = c8
                break
    except Exception as e:
        logline("预期日: trade_day_guard 异常({}), 退化自然日规则".format(e))
    if expect is None:
        # fail-open: 自然日回退(跳过周末, 法定假日容忍为 WARN)
        from datetime import timedelta
        d = datetime.now().date()
        for k in range(1, 31):
            cand = d - timedelta(days=k)
            if cand.weekday() < 5:
                expect = int(cand.strftime("%Y%m%d"))
                break
    if expect is None:
        return f"qfq MAX={mx} (预期日计算失败, 跳过判定)"
    lag_days = None
    try:
        from datetime import datetime as _dt
        lag_days = (_dt.strptime(str(mx), "%Y%m%d") - _dt.strptime(str(expect), "%Y%m%d")).days
    except Exception:
        pass
    if lag_days is None:
        return f"qfq MAX={mx} (日期解析异常, 跳过判定)"
    if lag_days > 3:
        return f"qfq MAX={mx} 落后预期{expect} {lag_days}天 -> FAIL"
    if lag_days > 0:
        return f"qfq MAX={mx} 落后预期{expect} {lag_days}天 -> WARN(可能节假日)"
    return f"qfq MAX={mx} OK (预期{expect})"


def _flush_pending(st: dict) -> bool:
    """恢复后 flush: 直接调 stop_loss_executor --execute (S-5 硬止损自动执行, 双层防线内置)。

    2026-09-10 修复: 原实现无返回值——OK 分支无条件清 pending, probe 假阳时
    (check_westock 与 stop_loss_executor 直连路径不同)清单丢失。现返回 bool:
    True=止损执行器 exit0 完成; False=非0/异常 → 调用方保留 pending 下轮重试。
    """
    pend = st.get("pending_stoploss") or []
    _notify("westock 恢复 - flush 累积止损清单",
            f"断供期间累积清单 {len(pend)} 只: {[(x['code'], x['name']) for x in pend]}; "
            f"现调 stop_loss_executor --execute 按实时持仓重新判定并执行(S-5 自动)", "error")
    try:
        r = subprocess.run([sys.executable, os.path.join(BASE, "stop_loss_executor.py"), "--execute"],
                           capture_output=True, text=True, timeout=600)
    except Exception as e:
        _notify("flush 异常", f"stop_loss_executor 调用异常: {type(e).__name__}: {e} → pending 保留", "error")
        return False
    tail = (r.stdout or "").strip().splitlines()[-3:]
    ok = r.returncode == 0
    _notify("flush 完成" if ok else "flush 失败(保留 pending)",
            f"exit={r.returncode}; 输出尾: {' | '.join(tail)[:200]}", "warn" if ok else "error")
    return ok


def cmd_probe(force: str = "") -> int:
    st = _load_state()
    if force == "error":
        level, msg = "ERROR", "[dry-run 强制断供分支]"
    elif force == "ok":
        level, msg = "OK", "[dry-run 强制恢复分支]"
    else:
        from health_check_daily import check_westock
        level, msg = check_westock()
    print(f"[probe] westock={level}: {msg}")

    if level == "OK":
        if st.get("pending_stoploss"):
            # 2026-09-10: flush 失败(止损执行器非0/异常)保留 pending 下轮重试,
            # 不再无条件丢弃清单(probe 假阳场景防丢)
            if _flush_pending(st):
                st = {"status": "ok_flushed", "flushed_at": datetime.now().isoformat(timespec="seconds")}
            else:
                st = {"status": "ok_flush_failed",
                      "pending_stoploss": st.get("pending_stoploss"),
                      "flush_error_at": datetime.now().isoformat(timespec="seconds")}
                _save_state(st)
                print("[probe] flush 失败, pending 保留待下轮重试")
                return 1
        else:
            st = {"status": "ok", "last_ok_at": datetime.now().isoformat(timespec="seconds")}
        _save_state(st)
        print("[probe] 状态 OK, 无待 flush" if not st.get("flushed_at") else "[probe] 已 flush")
        return 0

    if level == "WARN":
        today = datetime.now().strftime("%Y-%m-%d")
        if st.get("last_warn_date") != today:
            _notify("westock token 即将过期 - 请提前重签", msg, "warn")
            st["last_warn_date"] = today
            st["status"] = "warn"
            _save_state(st)
        return 0

    # ERROR: 断供
    first = st.get("status") != "outage"
    plist = gen_local_stoploss_list()
    st["status"] = "outage"
    st["outage_since"] = st.get("outage_since") or datetime.now().isoformat(timespec="seconds")
    st["pending_stoploss"] = plist
    st["last_probe_msg"] = msg
    st["last_probe_at"] = datetime.now().isoformat(timespec="seconds")
    _save_state(st)
    if first:
        _notify("westock 断供 - 止损保护降级运行",
                f"{msg}\n本地已生成止损清单 {len(plist)} 只"
                f"{(': ' + str([(x['code'], x['name'], x['pnl_pct']) for x in plist[:5]])) if plist else '(当前无<=-8%标的)'}\n"
                f"恢复唯一路径=人工 PKCE 重签(westock_oauth.py, refresh 端点已证伪禁用);\n"
                f"恢复后本守护自动 flush(调 stop_loss_executor 按实时判定执行)。", "error")
    elif plist:
        _notify("断供持续 - 本地止损清单更新", f"{len(plist)} 只触发: "
                f"{[(x['code'], x['name'], x['pnl_pct']) for x in plist[:8]]}", "error")
    print(f"[probe] 断供中, 清单 {len(plist)} 只已存 state")
    return 3


def cmd_intraday() -> int:
    """盘中三探针: westock + TickFlow + qfq 新鲜度; 异常 notify (A2)"""
    from health_check_daily import check_westock
    problems = []
    level, msg = check_westock()
    if level in ("ERROR", "WARN"):
        problems.append(f"westock[{level}]: {msg[:80]}")
    if not _tickflow_alive():
        problems.append(f"TickFlow {TICKFLOW_HOST}:{TICKFLOW_PORT} 不通 -> 面板可能已挂(tickflow_panel_guard 会拉起, 若持续告警人工查)")
    fq = _qfq_freshness()
    if "FAIL" in fq:
        problems.append(f"数据链: {fq}")
    if problems:
        _notify("盘中探针异常", " ; ".join(problems), "error")
        print("[intraday] 异常: " + " ; ".join(problems))
        return 3
    print(f"[intraday] 三探针 OK ({fq})")
    return 0


def cmd_preopen() -> int:
    """执行链直连通道开盘前探针 (2026-11-09, 今晨事故盲区补件)。

    今晨实证: check_westock/intraday 探的是 MCP 通道, 而买入/卖出执行链走
    westock_client 直连 HTTP——同一 token 两条路径, MCP 探针 OK 但直连通道
    已硬死(token 过期+refresh 断供), 买入链全 skip 且伪装成"开盘价缺失"。
    本探针实测执行链同一条路(WeStockClient.quote), fail-closed:
    异常 -> error notify + rc=3; 正常 -> rc=0。
    附带 check_westock() 到期≤6h WARN 同步提醒 (每日一次, 天然去重)。
    """
    problems = []
    try:
        sys.path.insert(0, r"D:\workbuddyspace")
        from westock_client import WeStockClient
        ws = WeStockClient()
        q = ws.quote("sh600000")
        flat = q.get("data", {}) if isinstance(q, dict) else {}
        if isinstance(flat, dict) and "sh600000" in flat:
            flat = flat["sh600000"]
        if not (isinstance(flat, dict) and flat.get("price")):
            problems.append("westock_client.quote 返回无 price 字段(响应结构漂移?) -> 执行链挂价将 fail-safe 全跳")
    except Exception as e:
        problems.append("执行链直连通道异常 %s: %s -> 买卖执行链将全拦; "
                        "恢复唯一路径=人工 PKCE 重签 westock_oauth.py" % (type(e).__name__, e))
    try:
        from health_check_daily import check_westock
        level, msg = check_westock()
        if level in ("WARN", "ERROR"):
            problems.append("westock[%s]: %s" % (level, str(msg)[:100]))
    except Exception as e:
        problems.append("check_westock 异常: %s" % e)
    if problems:
        _notify("westock 执行链通道开盘前预警",
                " ; ".join(problems)[:300], "error")
        print("[preopen] 异常: " + " ; ".join(problems))
        return 3
    print("[preopen] 执行链直连通道 OK")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--probe", action="store_true", help="断供状态机探测")
    ap.add_argument("--intraday", action="store_true", help="盘中三探针")
    ap.add_argument("--preopen", action="store_true", help="执行链直连通道开盘前探针(08:45)")
    ap.add_argument("--dry-run", choices=["error", "ok"], default="", help="强制分支模拟(不真改 token)")
    a = ap.parse_args()
    if a.probe or a.dry_run:
        return cmd_probe(a.dry_run)
    if a.intraday:
        return cmd_intraday()
    if a.preopen:
        return cmd_preopen()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
