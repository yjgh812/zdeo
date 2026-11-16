# -*- coding: utf-8 -*-
"""WIRE-1 门禁：C1 回归目标 vs 在产二分类基线，同 M-3 交易层双臂裁决（2026-09-04）
=====================================================================
预注册: reports/rankloss_wire1_20260904/PREREG.json（指纹 a81c24fafcc3f11f, trial_budget=1, 跑前冻结）
双臂(各自独立子进程, 因 base._ML_TABLE 在 import 时读取):
  W1_binary_m3 = v25_round12wf 打分 + trailing10%/hold20（在产基线臂, round12 裁决臂同配置）
  W1_c1reg_m3  = v26_c1reg 打分 + 同交易层（候选臂）
判据(LOCKED, 统计窗 2019-01-01..数据末):
  D1 优势: ann(c1reg) >= ann(binary)
  D2 席位线: ann(c1reg) >= +8%（round12 同线）
  D3 回撤: mdd(c1reg) >= -25%（round12 硬顶）
  D4 稳定: 正年占比 >= 50%（2018-2026 打分年）
PASS = D1∧D2∧D3∧D4 → 向主人提交生产切换方案(切换仍须主人执行)
FAIL → C1 维持研究归档, v375 重训链保持 binary, 本条款关闭
一票制: data/wfa_trials.jsonl 已有本指纹裁决时拒绝重跑(--force 记 violation)
用法: python tulong_wfa_gate_wire1_c1_20260904.py [--force]
"""
import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, r"D:\zcodesj")

PREREG_PATH = r"D:\zcodesj\reports\rankloss_wire1_20260904\PREREG.json"
PREREG_FP = "a81c24fafcc3f11f"
TRIALS_LOG = r"D:\zcodesj\data\wfa_trials.jsonl"
GATE_DIR = r"D:\zcodesj\reports\rankloss_wire1_20260904\gate"

ARMS = {
    "w1_binary_m3": "stock_ml_scores_v25_round12wf",
    "w1_c1reg_m3": "stock_ml_scores_v26_c1reg",
}


def verify_prereg():
    p = json.load(open(PREREG_PATH, encoding="utf-8"))
    claimed = p.pop("fingerprint", None)
    canonical = json.dumps(p, ensure_ascii=False, sort_keys=True, indent=1)
    fp = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    assert fp == PREREG_FP == claimed, f"预注册指纹漂移: 计算{fp} != 冻结{PREREG_FP} != 文件{claimed}"
    assert p["status"] in ("preregistered", "completed") and p["trial_budget"] == 1
    print(f"预注册校验 OK: {PREREG_FP} | criterion={list(p['criterion_LOCKED'].keys())}", flush=True)
    return p


def one_vote_check(force: bool):
    if not os.path.exists(TRIALS_LOG):
        return
    for line in open(TRIALS_LOG, encoding="utf-8"):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if r.get("fingerprint") == f"rankloss-wire1-{PREREG_FP}" and not force:
            raise SystemExit(f"一票制拦截: 本协议已裁决({r.get('ts')}, verdict={r.get('verdict')})。"
                             f"重跑须 --force 并记 violation(宪法 B4)。")
    if force:
        with open(TRIALS_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": datetime.now().isoformat(), "fingerprint": f"rankloss-wire1-{PREREG_FP}",
                                "type": "force_rerun_violation"}, ensure_ascii=False) + "\n")


def verify_tables():
    """打分表年度完整性 fail-closed: 按各表实际血统口径查(v25_round12wf=8年2019起, v26_c1reg=9年2018起)"""
    import sqlite3
    expect = {"stock_ml_scores_v25_round12wf": range(2019, 2027),
              "stock_ml_scores_v26_c1reg": range(2018, 2027)}
    dq = sqlite3.connect(r"file:D:\workbuddyspace\factor_engine\data\dragon_quant.db?mode=ro", uri=True)
    for name, table in ARMS.items():
        for y in expect[table]:
            n = dq.execute(f"SELECT COUNT(*) FROM {table} WHERE date >= ? AND date <= ?",
                           (y * 10000 + 101, y * 10000 + 1231)).fetchone()[0]
            assert n > 0, f"打分表不完整: {table} {y} 年 0 行 (WF 重训未完成?) fail-closed"
        print(f"表完整性 OK: {table} ({expect[table][0]}-{expect[table][-1]})", flush=True)
    dq.close()


def run_arm(name: str, table: str) -> dict:
    """子进程跑单臂（TULONG_ML_TABLE import 时读取, 必须进程隔离）"""
    env = dict(os.environ)
    env["TULONG_ML_TABLE"] = table
    env["TULONG_BT_OUT"] = GATE_DIR
    code = (
        "import sys, os, json; sys.path.insert(0, r'D:\\zcodesj'); "
        "sys.path.insert(0, r'D:\\workbuddyspace\\factor_engine'); "
        "import tulong_backtest_full_report as base; "
        "loader = base.TulongDataLoader(base.DB_PATH, base.QFQ_PATH); "
        f"r = base.run_single_backtest('{name}', base.ml_signal, loader, 'monthly', "
        "{'max_holding_bars': 20, 'trailing_stop_pct': 0.10}); "
        # 只存 metrics(equity_curve 是 DataFrame 不可 JSON 化; equity 统一由父进程读引擎落盘的 {name}_equity.csv)
        f"json.dump({{'metrics': r.get('metrics', {{}})}}, "
        f"open(os.path.join(r'{GATE_DIR}', '{name}_result.json'), 'w', encoding='utf-8'), "
        "ensure_ascii=False, default=str); print('ARM_DONE')"
    )
    print(f"[arm] {name} (table={table}) 启动...", flush=True)
    r = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    tail = (r.stdout or "")[-500:] + (r.stderr or "")[-800:]
    assert r.returncode == 0 and "ARM_DONE" in (r.stdout or ""), f"臂 {name} 失败:\n{tail}"
    print(f"[arm] {name} 完成", flush=True)
    return json.load(open(os.path.join(GATE_DIR, f"{name}_result.json"), encoding="utf-8"))["metrics"]


def yearly_from_equity(eq) -> dict:
    import pandas as pd
    df = eq.copy() if hasattr(eq, "copy") else pd.DataFrame(eq)
    if "date" in df.columns:
        dcol = "date"
    else:
        df = df.reset_index()
        dcol = df.columns[0]
    vcol = [c for c in df.columns if c != dcol and pd.api.types.is_numeric_dtype(df[c])][0]
    df[dcol] = df[dcol].astype(str)
    df = df.sort_values(dcol)
    df["year"] = df[dcol].str[:4]
    ye = df.groupby("year")[vcol].last()
    out, prev = {}, None
    for y in sorted(ye.index):
        out[y] = (float(ye[y]) / prev - 1.0) if prev else None
        prev = float(ye[y])
    return out


def stats_from_equity(eq, d1: str, d2: str = "2099-12-31") -> dict:
    import pandas as pd
    df = eq.copy() if hasattr(eq, "copy") else pd.DataFrame(eq)
    if "date" in df.columns:
        dcol = "date"
    else:
        df = df.reset_index()
        dcol = df.columns[0]
    vcol = [c for c in df.columns if c != dcol and pd.api.types.is_numeric_dtype(df[c])][0]
    df[dcol] = df[dcol].astype(str)
    df = df.sort_values(dcol)
    w = df[(df[dcol] >= d1) & (df[dcol] <= d2)]
    vals = w[vcol].astype(float).tolist()
    start, end = vals[0], vals[-1]
    years = len(vals) / 244.0
    ann = (end / start) ** (1.0 / years) - 1.0
    peak, mdd = start, 0.0
    for v in vals:
        peak = max(peak, v)
        mdd = min(mdd, v / peak - 1.0)
    return {"window": [d1, w[dcol].iloc[-1]], "bars": len(vals), "total_return": end / start - 1.0,
            "ann_return": ann, "mdd": mdd, "years": years}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true", help="一票制拦截时强制重跑（记 violation）")
    ap.add_argument("--from-disk", action="store_true",
                    help="从 gate 目录已落盘 equity CSV 再生裁决（装配修复用, 不重跑回测）")
    args = ap.parse_args()
    prereg = verify_prereg()
    one_vote_check(args.force)
    import pandas as pd
    if not args.from_disk:
        verify_tables()
        for name, table in ARMS.items():
            run_arm(name, table)
    # equity 统一从引擎落盘的 CSV 装载(两条路径同源; run_single_backtest 每臂必写 {name}_equity.csv)
    arms = {name: {"equity": pd.read_csv(os.path.join(GATE_DIR, f"{name}_equity.csv"),
                                         encoding="utf-8-sig")}
            for name in ARMS}

    st_c1 = stats_from_equity(arms["w1_c1reg_m3"]["equity"], "2019-01-01")
    st_b = stats_from_equity(arms["w1_binary_m3"]["equity"], "2019-01-01")
    yr_c1 = yearly_from_equity(arms["w1_c1reg_m3"]["equity"])
    score_years = [str(y) for y in prereg["trainer"]["score_years"]]
    year_rets = {y: (float(yr_c1.get(y)) if yr_c1.get(y) is not None else 0.0) for y in score_years}
    pos_years = sum(1 for v in year_rets.values() if v > 0)

    d1 = bool(st_c1["ann_return"] >= st_b["ann_return"])
    d2 = bool(st_c1["ann_return"] >= 0.08)
    d3 = bool(st_c1["mdd"] >= -0.25)
    d4 = bool(pos_years >= len(score_years) * 0.5)
    verdict = "PASS" if (d1 and d2 and d3 and d4) else "FAIL"

    result = {
        "ts": datetime.now().isoformat(),
        "prereg_fingerprint": PREREG_FP,
        "ml_tables": {"binary": ARMS["w1_binary_m3"], "c1reg": ARMS["w1_c1reg_m3"]},
        "oos_window": st_c1["window"],
        "arm_W1_c1reg_m3": {
            "stats": {k: (round(float(v), 4) if isinstance(v, float) else v) for k, v in st_c1.items()},
            "yearly": {y: (round(float(v), 4) if v is not None else None) for y, v in year_rets.items()},
        },
        "arm_W1_binary_m3": {
            "stats": {k: (round(float(v), 4) if isinstance(v, float) else v) for k, v in st_b.items()},
        },
        "criteria": {
            "D1_superiority_ann(c1reg>=binary)": {"c1reg": round(st_c1["ann_return"], 4),
                                                   "binary": round(st_b["ann_return"], 4), "pass": d1},
            "D2_seat_bar_ann>=8%": {"value": round(st_c1["ann_return"], 4), "pass": d2},
            "D3_mdd>=-25%": {"value": round(st_c1["mdd"], 4), "pass": d3},
            "D4_pos_years>=50%": {"value": f"{pos_years}/{len(score_years)}", "pass": d4},
        },
        "verdict": verdict,
    }
    out_path = os.path.join(GATE_DIR, "gate_result.json")
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=1)

    entry = {
        "ts": result["ts"],
        "fingerprint": f"rankloss-wire1-{PREREG_FP}",
        "engine": "tulong_wfa_gate.v1(round12-ml-channel,wire1-dual-table)",
        "combo": ["m3_trail_ml_wf_c1reg"],
        "pool": "ml_signal_c1reg_vs_v25_round12wf_monthly",
        "params": {"arms": list(ARMS.keys()), "oos": st_c1["window"],
                   "overlay": {"max_holding_bars": 20, "trailing_stop_pct": 0.10}},
        "n_eff_trials": 1,
        "n_folds": len(score_years),
        "verdict": verdict,
        "oos": {"ann_c1reg": round(st_c1["ann_return"], 4), "ann_binary": round(st_b["ann_return"], 4),
                "mdd_c1reg": round(st_c1["mdd"], 4), "pos_years": pos_years},
        "note": "RANKLOSS stage-2 PASS_CANDIDATE(ce81f9f)兑现的正式接线评估; 判据 PREREG a81c24fafcc3f11f; "
                "PASS→向主人提交生产切换方案(切换由主人执行), FAIL→C1维持研究归档v375保持binary",
        "force": bool(args.force),
        "one_vote_status": "forced_violation" if args.force else "ok",
        "prereg": {"id": prereg["id"], "status": "completed"},
    }
    with open(TRIALS_LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    print(json.dumps(result["criteria"], ensure_ascii=False, indent=1), flush=True)
    print(f"裁决: {verdict} | 结果 {out_path} | 台账已记", flush=True)
    return 0 if verdict == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
