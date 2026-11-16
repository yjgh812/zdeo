# -*- coding: utf-8 -*-
"""突破主线批次管线(prereg PREREG_BREAKOUT_MAINLINE_20261026 revision_20261027) 20261027 — 零交易权本体。
本脚本不直连任何交易原语; --execute-guard 仅做 fail-closed 守卫后透传 trade_executor buy-batch(唯一执行入口)。
调度: BreakoutScan_Daily_0830(--build, 周一~周五 08:30) / BatchExec_Breakout_Daily_0941(--execute-guard, 同 09:41)。

--build:          跑扫描器(子进程)→跳过已持仓(positions_latest.json)→定价=close*1.01/股数=close<15→200否则100
                  →写 data/batch_exec_breakout.json + data/batch_exec_breakout_meta.json(信号日/生成时间/executed=false)
--dry:            同 --build 但产物落 logs/_pipeline_dry/ 不触真实文件(机制验证用)
--execute-guard:  守卫三条全过才提交: ①meta存在 ②批次当日生成 ③信号日==qfq最新交易日 且批次非空且未执行过;
                  任一不过=SKIP落日志; 提交后 executor 自带当日幂等/R13加仓/R31偏离度等闸门(本脚本不复制闸门语义)。
已知边界: 节假日(MON-FRI但休市)会重建同一批次并再次触发, executor R13加仓拒绝兜底(全部REJECT, 噪声安全)。
"""
import json
import os
import sqlite3
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")

BASE = r"D:\zcodesj"
BATCH = os.path.join(BASE, "data", "batch_exec_breakout.json")
META = os.path.join(BASE, "data", "batch_exec_breakout_meta.json")
BATCH_A = os.path.join(BASE, "data", "batch_exec_breakout_a.json")
META_A = os.path.join(BASE, "data", "batch_exec_breakout_meta_a.json")
POS = os.path.join(BASE, "data", "positions_latest.json")
SCAN = os.path.join(BASE, "analysis", "breakout_mainline_scanner.py")
LATEST = os.path.join(BASE, "data", "breakout_mainline_latest.json")
QFQ = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"


def log(msg):
    # 只 print: .cmd 以 >> 重定向持有本日志句柄, 脚本再 append 会 Windows 共享冲突
    # (10/27 投掷实测 PermissionError); 单写者=cmd 重定向, 手动跑则落控制台。
    line = f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line)


def held_codes():
    try:
        p = json.load(open(POS, encoding="utf-8"))
        codes = {str(x.get("code", ""))[-6:] for x in p.get("positions", [])}
        return codes, str(p.get("snapshot_at", "?"))
    except Exception as e:
        log(f"WARN 持仓快照不可用({e}) → 不跳过已持仓, executor R13兜底")
        return set(), "unavailable"


def qfq_max_date():
    con = sqlite3.connect(QFQ, uri=True)
    mx = int(con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0])
    con.close()
    return mx


def build(dry=False):
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, SCAN], capture_output=True, text=True, cwd=BASE, env=env,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        log(f"FAIL 扫描器rc={r.returncode}: {(r.stderr or '')[-300:]} → 不触批次文件")
        return 2
    latest = json.load(open(LATEST, encoding="utf-8"))
    picked = latest.get("picked", [])
    pre_all = latest.get("candidates_prefilter", [])
    asof = int(latest.get("asof", 0))
    if not picked and not pre_all:
        log(f"NO_PICK asof={asof} 过滤前0只 → 不改批次文件")
        return 0
    held, snap_at = held_codes()
    # 持仓快照自愈(唯一写入方=write_positions_snapshot.py; 09:10晨链缺勤时builder自行刷新, 失败则沿用旧快照+R13兜底)
    snap_r = subprocess.run([sys.executable, os.path.join(BASE, "write_positions_snapshot.py")],
                            capture_output=True, text=True, cwd=BASE, env=env,
                            encoding="utf-8", errors="replace")
    if snap_r.returncode == 0:
        held, snap_at = held_codes()
        log(f"SNAPSHOT 已刷新({snap_r.stdout or 'ok'})")
    else:
        log(f"WARN SNAPSHOT 刷新失败rc={snap_r.returncode}: {(snap_r.stderr or '')[-120:]} → 沿用旧快照+R13兜底")
    from stock_name_lookup import get_name

    def mk_entry(p, source, tag):
        code = str(p["code"])
        name = get_name(code)
        if name is None:
            log(f"FAIL_SEAT[{tag}] {code} 名称校验失败(查不到) → 弃席")
            return None
        close = float(p["close"])
        return {"code": code, "name": name, "volume": 200 if close < 15 else 100,
                "price": round(close * 1.01, 2), "source": source,
                "sector": p.get("l1", ""), "prev_close": close}  # 全量冻结口径: r31不预审模拟盘法庭(9/8豁免口降WARN)

    # B臂: 主线共振过滤后候选(picked), 同行业最多1只, 跳已持仓
    entries_b = []
    for p in picked:
        code = str(p["code"])
        if code in held:
            log(f"SKIP_HELD[B] {code}")
            continue
        e = mk_entry(p, "breakout_mainline", "B")
        if e:
            entries_b.append(e)
    # A臂: 过滤前全集按成交额降序, 无行业限制, 跳已持仓, 剔B票(executor当日幂等), 取5
    pre = sorted(pre_all, key=lambda c: -float(c["amt_yi"]))
    b_codes = {e["code"] for e in entries_b}
    entries_a = []
    for p in pre:
        if len(entries_a) >= 5:
            break
        code = str(p["code"])
        if code in held or code in b_codes:
            continue
        e = mk_entry(p, "breakout_ab_A", "A")
        if e:
            entries_a.append(e)
    if not entries_b and not entries_a:
        log(f"NO_EXECUTABLE_SEAT asof={asof} → 不改批次文件")
        return 0
    try:
        from market_regime_tag import regime_on
        reg = regime_on()
        regime = reg.get("regime", "N/A")
        regime_detail = {k: reg.get(k) for k in ("adx14", "close_above_ma60", "ma60_slope_up")}
    except Exception as e:
        regime, regime_detail = "N/A", {}
        log(f"WARN regime标签失败({e}) → meta无regime")

    def mk_meta(arm, seats):
        return {"signal_date": asof, "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "executed": False, "arm": arm, "regime": regime, "regime_detail": regime_detail,
                "seats": [e["code"] for e in seats]}

    # 归类层(主人框架判据一·记录层v0): 证据归集+启发式归类, 判定前兑现标准待主人方法论定义
    try:
        from signal_classifier import classify_seats
        cls_map = classify_seats(entries_b + entries_a, asof)
        cls_rec = {"signal_date": asof, "classified_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "classifications": cls_map}
        if dry:
            cls_out = os.path.join(BASE, "logs", "_pipeline_dry", "classifications.jsonl")
        else:
            cls_out = os.path.join(BASE, "data", "breakout_classifications.jsonl")
        with open(cls_out, "a", encoding="utf-8") as f:
            f.write(json.dumps(cls_rec, ensure_ascii=False) + "\n")
        log("CLASSIFY 归类: " + ", ".join(f"{c2}={v2['driver_class_v0']}" for c2, v2 in cls_map.items()))
    except Exception as e:
        log(f"WARN 归类层失败({e}) → 本批无归类记录(判据一该项缺失, 不阻塞执行)")

    if dry:
        out_b = os.path.join(BASE, "logs", "_pipeline_dry", "batch_exec_breakout.json")
        out_m = os.path.join(BASE, "logs", "_pipeline_dry", "meta.json")
        out_ba = os.path.join(BASE, "logs", "_pipeline_dry", "batch_a.json")
        out_ma = os.path.join(BASE, "logs", "_pipeline_dry", "meta_a.json")
    else:
        out_b, out_m = BATCH, META
        out_ba, out_ma = BATCH_A, META_A
    os.makedirs(os.path.dirname(out_b), exist_ok=True)
    if entries_b:
        json.dump(entries_b, open(out_b, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(mk_meta("B", entries_b), open(out_m, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    if entries_a:
        os.makedirs(os.path.dirname(out_ba), exist_ok=True)
        json.dump(entries_a, open(out_ba, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        json.dump(mk_meta("A", entries_a), open(out_ma, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"{'DRY' if dry else 'BUILD'} asof={asof} B臂{len(entries_b)}={[e['code'] + e['name'] for e in entries_b]} "
        f"A臂{len(entries_a)}={[e['code'] + e['name'] for e in entries_a]} 持仓快照={snap_at}")
    return 0


def execute_guard(arm="b", dry=False):
    if arm == "a":
        batch_f, meta_f, tag = BATCH_A, META_A, "A"
    else:
        batch_f, meta_f, tag = BATCH, META, "B"
    try:
        meta = json.load(open(meta_f, encoding="utf-8"))
    except Exception as e:
        log(f"SKIP[{tag}] 无meta({e}) → 批次未由管线登记, 不执行")
        return 0
    if meta.get("executed"):
        log(f"SKIP[{tag}] meta.executed=true 该批次已处理")
        return 0
    gen_day = str(meta.get("generated_at", ""))[:10]
    today = datetime.now().strftime("%Y-%m-%d")
    if gen_day != today:
        log(f"SKIP[{tag}] 批次非当日生成({gen_day}) → 视为陈旧拒执行")
        return 0
    if int(meta.get("signal_date", 0)) != qfq_max_date():
        log(f"SKIP[{tag}] 信号日{meta.get('signal_date')} != qfq最新{qfq_max_date()} 数据已前移")
        return 0
    entries = json.load(open(batch_f, encoding="utf-8"))
    if not entries:
        log(f"SKIP[{tag}] 批次为空(无候选日)")
        return 0
    log(f"GUARD_PASS[{tag}] asof={meta.get('signal_date')} 席位={[e['code'] + e['name'] for e in entries]} → 提交executor")
    if dry:
        log(f"GUARD_DRY[{tag}] --execute-guard-dry: 守卫逻辑全过, 不提交不标记(契约测试/日检用)")
        return 0
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([sys.executable, os.path.join(BASE, "trade_executor.py"), "buy-batch",
                        "--file", batch_f, "--execute"], capture_output=True, text=True, cwd=BASE, env=env,
                       encoding="utf-8", errors="replace")
    tail = ((r.stdout or "") + (r.stderr or ""))[-500:].replace("\n", " | ")
    log(f"EXEC[{tag}] rc={r.returncode} tail={tail}")
    if r.returncode == 0:
        meta["executed"] = True
        meta["executed_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        json.dump(meta, open(meta_f, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return r.returncode


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    if mode == "--build":
        sys.exit(build(False))
    elif mode == "--dry":
        sys.exit(build(True))
    elif mode in ("--execute-guard", "--execute-guard-dry"):
        arm = sys.argv[sys.argv.index("--arm") + 1] if "--arm" in sys.argv else "b"
        sys.exit(execute_guard(arm, dry=(mode == "--execute-guard-dry")))
    else:
        print("用法: --build | --dry | --execute-guard")
        sys.exit(1)
