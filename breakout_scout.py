#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""低位首板蓄势扫描器 breakout_scout v1 (2026-09-06)

修复断层（主人裁决"全落地"2026-09-06，起因=金健米业 8/6 低位首板无人看、
8/7-8/16 整理期系统失忆、8/17 复盘虽精确识别"横盘+放量突破"范式但判定后无跨日动作）：
系统每个镜头都是当日状态机，缺"低位首板→蓄势→二波启动"的跨日记忆链。
本模块提供感知层唯一 owner：
  scan   扫指定交易日的新低位首板（缺省=库内最新），UPSERT 进跟踪台账
  update 全量补行情：判蓄势破位(expired)/二波启动(breakout)；
         breakout 自动升级主线龙头观察池(leader_watchlist.add，幂等)
  report 当前跟踪清单

信号定义（机械可测，与 8/17 复盘"横盘+放量突破"范式同源）：
  低位首板 = 当日涨停(主板 change_pct>=9.8 / 创业板>=19.8) 且 前10交易日无涨停
             且 60日累计涨幅<25%(低位) 且 当日量>=2×前20日均量(放量)
  蓄势     = 首板后收盘未跌破首板日开盘价×0.97 且 距首板收盘回撤<15%
  二波启动 = 蓄势期内再度涨停(同首板阈值) → 事件+升级观察池
  超时     = 首板后20交易日未二波 → stale 归档

铁律边界（宪法 §10）：
- 纯感知层，零交易权。breakout 升级=登记观察池提请跟踪，不产生任何买信号；
  "首板蓄势二波"作为可交易命题已另立 prereg（data/PREREG_BREAKOUT_SCOUT_20260906.json）
  与已证伪的 BOX-BREAKOUT-LOWPOS（9/4 三闸全 FAIL）划界：彼=横盘放量突破即买，
  此=首板资金确认+蓄势不破+二波再确认三层，验证通过前禁止接线。
- 价格源=qfq 库只读，fail-closed（0字节 exit2 禁静默换源），--qfq-path 指备份快照。
"""
import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
STORE = ROOT / "data" / "breakout_scout_track.jsonl"
DEFAULT_QFQ = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")  # 2026-09-07 统一单源: 生产主库(9/6 SOP 裁定), zcodesj data/ 副本停用
ATTR_DB = ROOT / "attribution" / "attribution.db"
LEADER_SCRIPT = ROOT / "leader_watchlist.py"

# 参数（fingerprint 入台账；变更须主人裁决留痕）
PARAMS = {
    "limit_up_main": 9.8,        # 主板涨停判定 %
    "limit_up_cyb": 19.8,        # 创业板涨停判定 %
    "no_limit_prior_days": 10,   # 前 N 交易日无涨停=低位首板（区别梯队连板）
    "low_pos_gain_max": 25.0,    # 60日累计涨幅上限 %（低位）
    "vol_ratio_min": 2.0,        # 当日量/前20日均量下限（放量）
    "hold_floor": 0.97,          # 蓄势下限=首板日开盘价×此值
    "hold_drawdown_max": 15.0,   # 蓄势期距首板收盘最大回撤 %
    "track_window": 20,          # 首板后 N 交易日未二波→stale
    "min_gap_days": 3,           # 二波须间隔≥N交易日（1-2日内再板=梯队连板，归复盘镜头管）
}
PARAM_FP = "|".join(f"{k}={v}" for k, v in PARAMS.items())


def _fail(msg, code=2):
    print(f"[breakout_scout][ERROR] {msg}")
    sys.exit(code)


def open_qfq(path: Path):
    if not path.exists() or path.stat().st_size == 0:
        _fail(f"qfq 价格源不可用: {path}（不存在或0字节；主库并行重建期用 --qfq-path 指备份快照）")
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        n = con.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' "
                        "AND name='stock_daily_meta_qfq'").fetchone()[0]
        if n == 0:
            _fail(f"qfq 价格源缺表: {path}")
    except sqlite3.DatabaseError as e:
        _fail(f"qfq 价格源损坏: {path}: {e}")
    return con


def limit_up_threshold(code: str) -> float:
    return PARAMS["limit_up_cyb"] if code.startswith(("30", "68")) else PARAMS["limit_up_main"]


def scan_new_boards(con, date: int):
    """扫 date 日新低位首板：涨停+前10日无涨停；低位/放量在 python 层逐只核（候选量小）。"""
    # 当日涨停候选（域：沪深主板+创业板；688 科创仅感知标注不排除）
    cand = con.execute(
        "SELECT code, open, high, close, volume, change_pct FROM stock_daily_meta_qfq "
        "WHERE date=? AND (change_pct>=? OR (code LIKE '30%' AND change_pct>=?) "
        "OR (code LIKE '68%' AND change_pct>=?))",
        (date, PARAMS["limit_up_main"], PARAMS["limit_up_cyb"], PARAMS["limit_up_cyb"]),
    ).fetchall()
    out = []
    for code, o, h, c, v, pct in cand:
        code = str(code)
        thr = limit_up_threshold(code)
        if float(pct) < thr:
            continue
        hist = con.execute(
            "SELECT date, open, high, close, volume FROM stock_daily_meta_qfq "
            "WHERE code=? AND date<? ORDER BY date DESC LIMIT 70",
            (code, date),
        ).fetchall()
        if len(hist) < 30:
            continue  # 次新/数据不足，跳过并静默（感知层宁缺勿滥）
        rows = hist[::-1]  # 时间升序
        # 前10交易日无涨停（SQL 计数；date 列 TEXT，参数传 int 由 affinity 转换）
        recent_pct = con.execute(
            "SELECT COUNT(*) FROM stock_daily_meta_qfq WHERE code=? AND date<? AND date>=? "
            "AND change_pct>=?", (code, date, rows[-10][0], PARAMS["limit_up_main"]),
        ).fetchone()[0]
        if recent_pct > 0:
            continue
        # 60日低位：close/60日前close-1 < 25%
        base_close = float(rows[0][3]) if len(rows) >= 60 else float(rows[0][3])
        gain60 = (float(c) / base_close - 1) * 100
        if gain60 >= PARAMS["low_pos_gain_max"]:
            continue
        # 放量：当日量 >= 2×前20日均量
        v20 = [float(r[4]) for r in rows[-20:]]
        avg_v20 = sum(v20) / len(v20)
        if avg_v20 <= 0 or float(v) < avg_v20 * PARAMS["vol_ratio_min"]:
            continue
        out.append({"code": code, "board_date": date, "board_open": float(o),
                    "board_high": float(h), "board_close": float(c),
                    "gain60_at_board": round(gain60, 2),
                    "vol_ratio": round(float(v) / avg_v20, 2),
                    "param_fp": PARAM_FP,
                    "status": "tracking", "last_update": date,
                    "last_close": float(c), "events": []})
    return out


def load_track():
    if not STORE.exists():
        return []
    recs = []
    for line in STORE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            r = json.loads(line)
            # 规范化：board_date/events 日期统一 int（TEXT 列 str 混入防线）
            r["board_date"] = int(r["board_date"])
            for e in r.get("events", []):
                e["date"] = int(e["date"])
            recs.append(r)
    # 同(日,码)去重（20260904 双扫遗留 15 行重复实锤：旧版本产物字段全同冻结在案；
    # 并发 scan 双写竞态也可能产生双份）。保留"进化最深"版本（events 多者优先，其次后现），
    # 单点保护全部消费方（update/report/health/研究脚本），不依赖各消费方自行去重。
    best = {}
    for r in recs:
        k = (r["code"], r["board_date"])
        if k not in best or len(r.get("events", [])) >= len(best[k].get("events", [])):
            best[k] = r
    return [r for r in recs if best[(r["code"], r["board_date"])] is r]


def save_track(recs):
    """合并写盘：读当前盘上台账，盘上已有键保留盘上版本（防并发 scan 的 load-append-覆盖
    丢更新竞态），仅追加内存中盘上没有的键，tmp+replace 原子落盘。"""
    STORE.parent.mkdir(parents=True, exist_ok=True)
    merged = {}
    if STORE.exists():
        for line in STORE.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                r = json.loads(line)
                merged[(str(r["code"]), int(r["board_date"]))] = r
    for r in recs:
        merged.setdefault((str(r["code"]), int(r["board_date"])), r)
    tmp = STORE.with_suffix(".jsonl.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        for r in merged.values():
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    tmp.replace(STORE)


def lookup_name(code: str) -> str:
    """stock_name_lookup.py = 项目唯一名称接口（宪法 §8）；失败返回 'UNKNOWN' 拒绝编造。"""
    r = subprocess.run([sys.executable, str(ROOT / "stock_name_lookup.py"), code],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    out = (r.stdout or "").strip()
    # 输出形如 "600127: 金健米业"
    if r.returncode == 0 and ":" in out:
        return out.split(":", 1)[1].strip()
    return "UNKNOWN"


def lookup_industry(date: int, code: str) -> str:
    """行业标注=涨停归因库当日反查（人工复盘口径，唯一可靠行业源；
    feature_industry_momentum 对改名股错标——000523 标"银行"——禁用）。
    反查不到（归因表缺该日/北交所等）→ 'UNKNOWN' fail-open，不阻断入池。"""
    try:
        con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
        try:
            row = con.execute("SELECT industry FROM limit_up_patterns "
                              "WHERE trade_date=? AND code=?", (date, code)).fetchone()
        finally:
            con.close()
        return (row[0] or "UNKNOWN") if row else "UNKNOWN"
    except sqlite3.Error:
        return "UNKNOWN"


def _portrait_pct_map():
    """画像呈现层(唯一对接=data/portrait_feed.json, owner=portrait_feed.py; 失败→空map不阻断)。
    零拦截: 仅报告标注, 不参与扫描/升格判定(PD-026④/S8同构, 2026-10-09 接线)。"""
    try:
        pf = json.load(open(ROOT / "data" / "portrait_feed.json", encoding="utf-8"))
        if pf.get("unavailable"):
            return {}, None
        return {str(k).zfill(6): float(v.get("pct", 0)) if isinstance(v, dict) else float(v)
                for k, v in (pf.get("scores_short") or {}).items()}, pf.get("snapshot_date")
    except Exception:
        return {}, None


def cmd_scan(args):
    con = open_qfq(Path(args.qfq_path) if args.qfq_path else DEFAULT_QFQ)
    try:
        # 库内 date 列 TEXT：MAX() 返回 str，必须强转 int，否则与 --date int 混用致幂等失效
        date = int(args.date.replace("-", "")) if args.date else \
            int(con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
        if con.execute("SELECT COUNT(*) FROM stock_daily_meta_qfq WHERE date=?", (date,)).fetchone()[0] == 0:
            _fail(f"{date} 全市场无行情（非交易日？）", 1)
        found = scan_new_boards(con, date)
    finally:
        con.close()
    track = load_track()
    seen = {(r["code"], r["board_date"]) for r in track}
    added = 0
    pt_map, _pt_date = _portrait_pct_map()
    from portrait_feed import winner5_tags_view
    w5, _w5e = winner5_tags_view()
    for r in found:
        if (r["code"], r["board_date"]) in seen:
            continue
        r["name"] = lookup_name(r["code"])
        r["industry"] = lookup_industry(date, r["code"])  # 2026-09-09 主人红棉复盘: 感知行须带主线归属线索
        track.append(r)
        added += 1
        pt_tag = f" | 画像{pt_map[r['code']]:.0f}" if r['code'] in pt_map else ""
        w5tag = f"·{w5['tags'][r['code']]}" if w5 and r['code'] in w5['tags'] else ""
        print(f"[breakout_scout] 新低位首板 {r['code']} {r['name']}[{r['industry']}] | {date} 开{r['board_open']:.2f} 收{r['board_close']:.2f} "
              f"| 60日涨幅{r['gain60_at_board']:.1f}%<25 量比{r['vol_ratio']}x>=2{pt_tag}{w5tag}")
    save_track(track)
    print(f"[breakout_scout] scan {date}: 候选 {len(found)}，新增 {added}，台账共 {len(track)} 条 → {STORE}")


def _is_upgrade_eligible(code: str, name: str) -> tuple:
    """观察池准入过滤：ST(±5%板/退市风险)、科创板(68)与北交所不在宪法买入域，升格跳过留痕。
    (20260906 检验发现 688152 混入：初版漏拦 68，已补——检验批次修复)"""
    if name.startswith(("*ST", "ST")) or "ST" in name[:4]:
        return False, "ST票不入观察池"
    if code.startswith(("68", "92", "82", "83", "87", "88", "43")):
        return False, "科创/北交所不入观察池"
    return True, ""


def upgrade_to_leader(code: str, name: str, reason: str, qfq_path, event_date: int):
    """二波启动→升级主线龙头观察池（幂等；已在池自动跳过）。零交易权，仅登记。
    锚定 breakout 事件日（非库内最新日），保证观察池峰值涨幅从二波启动起算。"""
    cmd = [sys.executable, str(LEADER_SCRIPT), "add", "--code", code, "--name", name,
           "--theme", "蓄势二波", "--ladder", "1", "--reason", reason, "--date", str(event_date)]
    if qfq_path:
        cmd += ["--qfq-path", qfq_path]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    ok = r.returncode == 0
    print(f"[breakout_scout] 升级观察池 {code} {'OK' if ok else 'FAIL(rc=%d)' % r.returncode}"
          f"{'（已在池幂等跳过）' if '已在池' in (r.stdout or '') else ''}")
    return ok


def cmd_update(args):
    con = open_qfq(Path(args.qfq_path) if args.qfq_path else DEFAULT_QFQ)
    try:
        max_date = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        track = load_track()
        changed = 0
        for r in track:
            if r["status"] in ("breakout", "expired", "stale"):
                continue
            code = r["code"]
            rows = con.execute(
                "SELECT date, open, high, close, volume FROM stock_daily_meta_qfq "
                "WHERE code=? AND date>? ORDER BY date", (code, r["board_date"]),
            ).fetchall()
            if not rows:
                continue
            floor = r["board_open"] * PARAMS["hold_floor"]
            board_close = r["board_close"]
            status, ev = r["status"], None
            for i, (d, o, h, c, v) in enumerate(rows, 1):  # i=首板后第几个交易日
                d, c, h = int(d), float(c), float(h)
                r["last_update"], r["last_close"] = d, c
                pct = con.execute("SELECT change_pct FROM stock_daily_meta_qfq "
                                  "WHERE code=? AND date=?", (code, d)).fetchone()
                # 20261011批5尾: change_pct=NULL是qfq合法状态(首日/除权跳变显式置NULL,
                # 全量重建后显形)——pct=(None,)为真值, 须判值非判行
                pct_val = pct[0] if pct else None
                is_limit = pct_val is not None and float(pct_val) >= limit_up_threshold(code)
                # 二波=蓄势≥min_gap个交易日后再度涨停（首放"收盘过首板高"65%触发率灌噪音已删；
                # 无间隔再板=梯队连板非二波；金健米业8/18=首板后第8交易日涨停，语义正例）
                if is_limit and i >= PARAMS["min_gap_days"]:
                    status, ev = "breakout", {
                        "type": "breakout", "date": d,
                        "detail": f"二波启动@{d} 首板后第{i}交易日涨停{float(pct_val):.1f}%"}
                    break
                if c < floor or (c / board_close - 1) * 100 < -PARAMS["hold_drawdown_max"]:
                    status, ev = "expired", {"type": "expired", "date": d,
                                             "detail": f"破蓄势下限{floor:.2f}或回撤超{PARAMS['hold_drawdown_max']}%"}
                    break
            # 全窗口扫完无事件且超窗口→stale（交易日计数=rows长度，YYYYMMDD整数差跨月失真不可用）
            if ev is None and len(rows) > PARAMS["track_window"]:
                status, ev = "stale", {"type": "stale", "date": int(rows[-1][0]),
                                       "detail": f"超跟踪窗口{PARAMS['track_window']}交易日未二波"}
            if ev:
                r["status"] = status
                r["events"].append(ev)
                if status == "breakout":
                    ok, why = _is_upgrade_eligible(code, r.get("name", "UNKNOWN"))
                    if ok:
                        upgrade_to_leader(code, r.get("name", "UNKNOWN"),
                                          f"蓄势二波启动{ev['date']}({ev['detail']})；首板{r['board_date']}",
                                          args.qfq_path, ev["date"])
                    else:
                        r["events"].append({"type": "upgrade_skipped", "date": ev["date"], "detail": why})
                        print(f"[breakout_scout] {code} breakout 但跳过升级：{why}")
            changed += 1
        save_track(track)
        print(f"[breakout_scout] update: {changed} 条跟踪更新至 {max_date}")
    finally:
        con.close()


def cmd_report(args):
    track = load_track()
    if not track:
        print("[breakout_scout] 台账为空")
        return
    pt_map, pt_date = _portrait_pct_map()
    from portrait_feed import winner5_tags_view
    w5, _w5e = winner5_tags_view()
    by = {}
    for r in track:
        by.setdefault(r["status"], []).append(r)
    print(f"低位首板蓄势台账（共 {len(track)} 条）— 纯感知零交易权 | 画像快照 {pt_date or 'N/A'}(呈现级零拦截)"
          + (f" | 五维标签asof {w5['asof']}(仅标注)" if w5 else ""))
    for st in ("tracking", "breakout", "expired", "stale"):
        for r in by.get(st, []):
            evs = "; ".join(f"{e['type']}@{e['date']}" for e in r["events"]) or "-"
            ptv = pt_map.get(r["code"])
            w5tag = w5["tags"].get(r["code"], "") if w5 else ""
            pt_cell = (f"{ptv:.0f}" if ptv is not None else "—") + (f"｜{w5tag}" if w5tag else "")
            print(f"{r['code']}  首板{r['board_date']} 开{r['board_open']:.2f} 收{r['board_close']:.2f} "
                  f"| 量比{r['vol_ratio']}x | 现价{r['last_close']:.2f}@{r['last_update']} "
                  f"| {st:<9} | {evs} | 画像{pt_cell}")


def cmd_daily(args):
    """一条龙：scan 库内最新交易日 + update 状态机（调度入口=daily_chain aux [scout] 环节，
    2026-09-09 接线；原"schtasks 每日 22:50"任务从未存活，致台账停 9/4 断链 3 日）。"""
    cmd_scan(args)
    cmd_update(args)


def main():
    # Windows 控制台/重定向缺省 GBK，股票名含不可编码字符(如 U+FFFD)时 print 直接
    # UnicodeEncodeError 杀死感知层(9/7 实锤: scan 打印崩溃→当日新首板丢失→台账停 9/4)。
    # 统一 UTF-8 输出；daily_chain aux 侧以 encoding="utf-8" 读取。
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="低位首板蓄势扫描器 v1（感知层零交易权）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan", help="扫新低位首板（缺省=库内最新交易日）")
    p.add_argument("--date")
    p.add_argument("--qfq-path")
    p.set_defaults(func=cmd_scan)
    p = sub.add_parser("update", help="补行情+蓄势/二波状态机+自动升级观察池")
    p.add_argument("--qfq-path")
    p.set_defaults(func=cmd_update)
    p = sub.add_parser("daily", help="scan+update 一条龙（调度入口）")
    p.add_argument("--date")
    p.add_argument("--qfq-path")
    p.set_defaults(func=cmd_daily)
    p = sub.add_parser("report", help="台账清单")
    p.set_defaults(func=cmd_report)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
