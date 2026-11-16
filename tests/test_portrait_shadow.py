#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tests/test_portrait_shadow.py — portrait_shadow_v1 契约测试（2026-09-05 全量测试轮+修复迭代轮）
================================================================================================
T1  --selfcheck 隔离断言 exit 0
T2  默认模式 exit 0 且 asof == qfq MAX(date)（数据新鲜度闸）
T3  确定性：同日重跑，json(去 generated_at) 与 csv 逐位一致（幂等契约）
T4  非交易日 --date → exit 2 结构化失败（fail-closed 契约）
T5  未来日期 --date → exit 2
T6  历史日期全链：exit 0 + 白名单回退文件正确 + asof 正确 + 报告过 verify_stock_names
T7  产物合同：观察池 Top30 / 前缀闸 60-00-30 / 收盘≤30 / json 名称全一致 /
    excluded_dimensions 四项在位
T8  清理 T6 产生的非默认日产物（测试零残渣）
T9  白名单重复代码 → exit 2 fail-closed（名单生成器违约防御，2026-09-05 修复轮新增）
T10 备份后缀文件（.csv.v1）不入名单合同，只认 buy_whitelist_YYYYMMDD.csv（同上）
T11 PIT 单趟读取与 multifactor.factor_loader.load_pit_factors 逐因子等价
    （代码集一致 + 数值 allclose 1e-9；守护 2026-09-05 性能重构无语义漂移）
T12 报告表格显示无 "nan" 泄漏（空行业/空名 → "—"；2026-09-05 修复轮新增）
T13 早期日期（历史不足）→ exit 2 结构化失败（不伪造可评分结果；第二轮新增）
T14 白名单缺 code 列（schema 漂移）→ exit 2；T14b 空白码 strip 后仍判重 fail-closed
T15 stock_info 主键重复 → exit 2（merge 膨胀防御）
T16 独立重算对账：从 qfq 原表重算池内/名单内个股 8 项特征值与 JSON 逐位比对
    （ret20/ret60/ma_align/bias20/vol_ratio/vola20/amt20/close，容差 1e-9；
     防特征逻辑静默漂移，第二轮新增）

不写 guard_precheck_log（纯 stdout 断言，无日志面）。
用法：python tests/test_portrait_shadow.py   → 全 PASS exit 0；任一 FAIL exit 1。
"""
import hashlib
import json
import sqlite3
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "portrait_shadow_v1.py"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
DATA = ROOT / "data"
REPORTS = ROOT / "reports"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "multifactor"))
import portrait_shadow_v1 as ps  # noqa: E402

results: list[tuple[str, bool, str]] = []


def run(args, timeout=420):
    return subprocess.run([sys.executable, str(SCRIPT)] + args,
                          capture_output=True, text=True, timeout=timeout, cwd=str(ROOT))


def check(name, ok, detail=""):
    results.append((name, bool(ok), detail))
    print(("PASS " if ok else "FAIL ") + name + (f" | {detail}" if (detail and not ok) else ""))


def json_canonical(p: Path) -> str:
    d = json.loads(p.read_text(encoding="utf-8"))
    d.pop("generated_at", None)  # 时间戳字段不参与确定性比对
    return json.dumps(d, sort_keys=True, ensure_ascii=False, default=float)


# T1 隔离自检
r = run(["--selfcheck"])
check("T1 selfcheck_exit0", r.returncode == 0 and "SELFCheck-PASS" in r.stdout,
      (r.stdout + r.stderr)[-300:])

# T2 默认模式 + asof 数据新鲜度闸
r = run([])
ok = r.returncode == 0
asof = r.stdout.split("asof=")[1].split(" ")[0] if ("asof=" in r.stdout) else ""
check("T2 default_exit0", ok, (r.stdout + r.stderr)[-400:])
with sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True) as c:
    qmax = str(c.execute("select max(date) from stock_daily_meta_qfq").fetchone()[0])
check("T2b asof==qfq_max", asof == qmax, f"asof={asof} vs qfq_max={qmax}")
snap_json = json_canonical(DATA / f"portrait_shadow_{qmax}.json")
snap_csv = hashlib.sha256((DATA / f"portrait_shadow_pool_{qmax}.csv").read_bytes()).hexdigest()

# T3 确定性（幂等契约）
r = run([])
check("T3 rerun_exit0", r.returncode == 0, r.stderr[-300:])
check("T3b json_deterministic", json_canonical(DATA / f"portrait_shadow_{qmax}.json") == snap_json)
check("T3c csv_deterministic",
      hashlib.sha256((DATA / f"portrait_shadow_pool_{qmax}.csv").read_bytes()).hexdigest() == snap_csv)

# T4 非交易日 fail-closed
r = run(["--date", "20260905"])
check("T4 non_trading_exit2", r.returncode == 2 and "缺少原始数据" in (r.stdout + r.stderr),
      (r.stdout + r.stderr)[-300:])

# T5 未来日期 fail-closed
r = run(["--date", "20991231"])
check("T5 future_exit2", r.returncode == 2, (r.stdout + r.stderr)[-300:])

# T6 历史日期全链 + 名单回退 + 名称门禁
wl_files = sorted(f for f in REPORTS.glob("buy_whitelist_*.csv")
                  if len(f.name) == len("buy_whitelist_YYYYMMDD.csv"))
wl_prev = wl_files[-2].stem.split("_")[-1]
r = run(["--date", wl_prev])
check("T6 older_exit0", r.returncode == 0, (r.stdout + r.stderr)[-400:])
j = json.loads((DATA / f"portrait_shadow_{wl_prev}.json").read_text(encoding="utf-8"))
check("T6b whitelist_fallback", j["whitelist_file"] == f"buy_whitelist_{wl_prev}.csv",
      j["whitelist_file"])
check("T6c asof_correct", j["asof"] == wl_prev, j["asof"])
vr = subprocess.run([sys.executable, str(ROOT / "verify_stock_names.py"),
                     str(REPORTS / f"PORTRAIT_SHADOW_{wl_prev}.md")],
                    capture_output=True, text=True)
check("T6d names_gate_PASS", vr.returncode == 0, vr.stdout[-200:])

# T7 产物合同（默认日）
d = json.loads((DATA / f"portrait_shadow_{qmax}.json").read_text(encoding="utf-8"))
pool = d["observation_pool"]
check("T7a pool_top30", len(pool) == 30, f"pool={len(pool)}")
check("T7b prefix_gate_60_00_30", all(str(x["code"])[:2] in ("60", "00", "30") for x in pool))
check("T7c price_le_30", all(float(x["close"]) <= 30.0 for x in pool))
from stock_name_lookup import verify_pair  # noqa: E402
bad = []
for x in pool + d["whitelist_portrait"]:
    ok, actual = verify_pair(str(x["code"]), str(x["name"]))
    if not ok:
        bad.append(f"{x['code']}:{x['name']}!={actual}")
check("T7d json_names_consistent", not bad, ";".join(bad[:5]))
check("T7e excluded_dims_complete",
      set(d["excluded_dimensions"]) == {"chip", "sentiment", "northbound", "model"})

# T9/T10 名单合同（进程内 fixture，monkeypatch WHITELIST_DIR）
with tempfile.TemporaryDirectory() as td:
    td_path = Path(td)
    orig_dir = ps.WHITELIST_DIR
    try:
        ps.WHITELIST_DIR = td_path
        dup_rows = "code,name,source,holding_horizon,price,limit_price,industry,tier,note\n" \
                   "000001,A,src,h,10.0,9.9,行业,1.0x,\n" \
                   "000001,A2,src,h,10.0,9.9,行业,1.0x,\n" \
                   "600000,B,src,h,5.0,4.9,行业,1.0x,\n"
        (td_path / "buy_whitelist_20260903.csv").write_text(dup_rows, encoding="utf-8")
        try:
            ps.load_whitelist("20260903")
            check("T9 whitelist_dup_failclosed", False, "未触发 SystemExit")
        except SystemExit as e:
            check("T9 whitelist_dup_failclosed", e.code == 2, f"code={e.code}")
        ok_rows = "code,name,source,holding_horizon,price,limit_price,industry,tier,note\n" \
                  "000001,A,src,h,10.0,9.9,行业,1.0x,\n600000,B,src,h,5.0,4.9,行业,1.0x,\n"
        (td_path / "buy_whitelist_20260903.csv").write_text(ok_rows, encoding="utf-8")
        (td_path / "buy_whitelist_20260903.csv.v1").write_text(dup_rows, encoding="utf-8")
        wl2, name2 = ps.load_whitelist("20260903")
        check("T10 backup_suffix_excluded", name2 == "buy_whitelist_20260903.csv"
              and len(wl2) == 2, f"picked={name2}")
    finally:
        ps.WHITELIST_DIR = orig_dir

# T11 PIT 单趟读取与 load_pit_factors 逐因子等价
from multifactor.factor_loader import load_pit_factors  # noqa: E402
start = (datetime.strptime(qmax, "%Y%m%d") - timedelta(days=ps.PIT_LOOKBACK_DAYS)).strftime("%Y%m%d")
fast = ps._load_pit_asof(qmax)
for f in ps.FUND_FEATURES:
    slow = load_pit_factors(f, start=start, end=qmax)
    slow = slow[slow["date"] == qmax][["code", "factor_value"]]
    cmp = fast[["code", f]].merge(slow, on="code", how="inner")
    same_set = (len(cmp) == len(slow) == int(fast[f].notna().sum()))
    both = cmp.dropna()
    max_diff = float((both[f] - both["factor_value"]).abs().max()) if len(both) else 0.0
    check(f"T11 pit_equiv_{f}", same_set and max_diff < 1e-9,
          f"fast={int(fast[f].notna().sum())} slow={len(slow)} inner={len(cmp)} "
          f"max_diff={max_diff:.2e}")

# T12 显示无 nan 泄漏
row = {c: 1.0 for c in ps._REPORT_COLS}
row.update({"rank": 1, "code": "000001", "name": np.nan, "industry": np.nan,
            "close": 10.0, "ma_align": 3})
tbl = ps._md_table(pd.DataFrame([row]))
check("T12 no_nan_leak", ("nan" not in tbl) and ("—" in tbl))

# T13 早期日期：历史不足须诚实失败
r = run(["--date", "20160110"])
check("T13 early_date_exit2", r.returncode == 2, (r.stdout + r.stderr)[-300:])

# T14/T14b 名单 schema 漂移与空白码（进程内 fixture）
with tempfile.TemporaryDirectory() as td:
    td_path = Path(td)
    orig_dir = ps.WHITELIST_DIR
    try:
        ps.WHITELIST_DIR = td_path
        (td_path / "buy_whitelist_20260903.csv").write_text(
            "name,price\nA,10.0\n", encoding="utf-8")
        try:
            ps.load_whitelist("20260903")
            check("T14 whitelist_schema_drift_failclosed", False, "未触发 SystemExit")
        except SystemExit as e:
            check("T14 whitelist_schema_drift_failclosed", e.code == 2, f"code={e.code}")
        (td_path / "buy_whitelist_20260903.csv").write_text(
            "code,name,price\n 600000 ,B,5.0\n600000,B2,5.0\n", encoding="utf-8")
        try:
            ps.load_whitelist("20260903")
            check("T14b whitespace_dup_failclosed", False, "未触发 SystemExit")
        except SystemExit as e:
            check("T14b whitespace_dup_failclosed", e.code == 2, f"code={e.code}")
    finally:
        ps.WHITELIST_DIR = orig_dir

# T15 宇宙主键重复防御（进程内 fixture）
_orig_meta = ps.get_stock_meta
try:
    ps.get_stock_meta = lambda: pd.DataFrame([
        {"code": "000001", "name": "A", "industry": "x", "list_date": "20200101",
         "is_st": "0", "status": "normal"},
        {"code": "000001", "name": "A-dup", "industry": "x", "list_date": "20200101",
         "is_st": "0", "status": "normal"},
    ])
    try:
        ps.load_universe("20260904")
        check("T15 universe_dup_failclosed", False, "未触发 SystemExit")
    except SystemExit as e:
        check("T15 universe_dup_failclosed", e.code == 2, f"code={e.code}")
finally:
    ps.get_stock_meta = _orig_meta

# T16 独立重算对账（特征值与原始表逐位核对，防逻辑静默漂移）
con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
spot_codes = [str(pool[0]["code"]), str(d["whitelist_portrait"][0]["code"])]
for code in spot_codes:
    rows = con.execute(
        "SELECT close, volume, amount FROM stock_daily_meta_qfq "
        "WHERE code=? AND date<=? ORDER BY date", (code, int(qmax))).fetchall()
    rows = rows[-ps.KLINE_DAYS:]
    closes = [float(x[0]) for x in rows]
    vols = [float(x[1]) for x in rows]
    amts = [float(x[2]) for x in rows]
    ma = {n: sum(closes[-n:]) / n for n in (5, 10, 20, 60)}
    feat = {
        "close": closes[-1],
        "ret20": closes[-1] / closes[-21] - 1.0,
        "ret60": closes[-1] / closes[-61] - 1.0,
        "bias20": closes[-1] / ma[20] - 1.0,
        "vol_ratio": vols[-1] / max(sum(vols[-6:-1]) / 5.0, 1.0),
        "vola20": float(pd.Series(closes).pct_change().tail(20).std()),
        "amt20": sum(amts[-20:]) / 20.0,
    }
    feat["ma_align"] = (int(ma[5] > ma[10]) + int(ma[10] > ma[20]) + int(ma[20] > ma[60]))
    src = next(x for x in list(pool) + list(d["whitelist_portrait"])
               if str(x["code"]) == code)
    diffs = {k: abs(feat[k] - float(src[k]))
             for k in ("close", "ret20", "ret60", "bias20", "vol_ratio", "vola20", "amt20")}
    ok = max(diffs.values()) < 1e-9 and feat["ma_align"] == int(src["ma_align"])
    check(f"T16 recompute_{code}", ok,
          f"max_diff={max(diffs.values()):.2e} align {feat['ma_align']} vs {src['ma_align']}")

# T8 清理测试产生的非默认日产物（每日再生产物零残渣）
for p in (DATA / f"portrait_shadow_{wl_prev}.json",
          DATA / f"portrait_shadow_pool_{wl_prev}.csv",
          REPORTS / f"PORTRAIT_SHADOW_{wl_prev}.md"):
    p.unlink(missing_ok=True)

fails = [n for n, ok, _ in results if not ok]
print(f"\n== {len(results) - len(fails)}/{len(results)} PASS ==")
for n, ok, det in results:
    if not ok:
        print(f"  FAIL {n} | {det}")
raise SystemExit(0 if not fails else 1)
