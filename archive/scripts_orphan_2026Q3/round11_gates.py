# -*- coding: utf-8 -*-
"""round11 G1-G4 门禁裁决（预注册 d21e286b13852fa9a1a9 冻结判据的机器执行）
用法: python round11_gates.py [--r11-dir D:...round11...] [--r10-dir D:...round10...]"""
import argparse
import glob
import io
import json
import math
import os

R11_DEFAULT = r"D:\zcodesj\reports\tulong_backtest_round11_ml_v23_20260828"
R10_DEFAULT = r"D:\zcodesj\reports\tulong_backtest_round10_full_20260827"
MODEL_DIR = r"D:\zcodesj\models\round11"
PREREG_DIR = r"D:\zcodesj\reports\round11_ml_walkforward_20260828"


def yearly_returns(d):
    import pandas as pd
    df = pd.read_csv(os.path.join(d, "yearly_summary_all.csv"))
    ml = df[df["strategy"] == "ml_signal"].set_index("year")["annual_return"]
    ml.index = ml.index.astype(str)
    return ml


def final_equity(path):
    import pandas as pd
    ec = pd.read_csv(path)
    col = "value" if "value" in ec.columns else ("equity" if "equity" in ec.columns else ec.columns[-1])
    return float(ec[col].iloc[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--r11-dir", default=R11_DEFAULT)
    ap.add_argument("--r10-dir", default=R10_DEFAULT)
    args = ap.parse_args()

    gates = {}

    # ---- G1: PIT 断言 + purge 边界（模型元数据 + 训练日志 violations）
    g1_ok, g1_detail = True, []
    for j in sorted(glob.glob(os.path.join(MODEL_DIR, "M*.json"))):
        m = json.load(io.open(j, encoding="utf-8"))
        ok = m["train_max_date"] < m["boundary"]
        g1_ok = g1_ok and ok
        g1_detail.append({"year": os.path.basename(j)[1:-5], "train_max": m["train_max_date"],
                          "boundary": m["boundary"], "ok": ok,
                          "best_iter": m["best_iteration"], "val_auc": round(m["val_auc"], 4)})
    log = io.open(os.path.join(PREREG_DIR, "retrain_full.log"), encoding="utf-8").read()
    viol = [l for l in log.splitlines() if "violations" in l and "PASS" in l]
    g1_ok = g1_ok and bool(viol)
    gates["G1"] = {"pass": g1_ok, "detail": g1_detail, "violations_line": viol[-1] if viol else None}

    # ---- 年度收益
    ml11 = yearly_returns(args.r11_dir)
    ml10 = yearly_returns(args.r10_dir)

    # ---- G2: 2018+2019 年收益算术和 > 0
    g2 = sum(float(ml11.get(y, 0.0)) for y in ("2018", "2019"))
    gates["G2"] = {"pass": g2 > 0, "value": round(g2, 4),
                   "2018": float(ml11.get("2018", 0.0)), "2019": float(ml11.get("2019", 0.0))}

    # ---- G3: 2020-2026 全期 >= 0 且 >= v22 同期 * 50%
    tot11 = sum(float(ml11.get(str(y), 0.0)) for y in range(2020, 2027))
    tot10 = sum(float(ml10.get(str(y), 0.0)) for y in range(2020, 2027))
    gates["G3"] = {"pass": tot11 >= 0 and tot11 >= 0.5 * tot10,
                   "v23_2020_2026": round(tot11, 4), "v22_2020_2026": round(tot10, 4),
                   "half_v22": round(0.5 * tot10, 4)}

    # ---- G4: 组合(bpep+v23) 终值 >= 纯 bpep 腿终值
    combo = final_equity(os.path.join(args.r11_dir, "portfolio_combo_equity.csv"))
    bpep = final_equity(os.path.join(args.r11_dir, "bp_ep_core_equity.csv"))
    gates["G4"] = {"pass": combo >= bpep, "combo": round(combo, 2), "bpep": round(bpep, 2)}

    # ---- 裁决（预注册规则）
    all_pass = all(gates[k]["pass"] for k in ("G1", "G2", "G3", "G4"))
    verdict = {
        "fingerprint": "d21e286b13852fa9a1a9",
        "gates": gates,
        "all_pass": all_pass,
        "decision": ("建议 v23 替换生产 ml 表" if all_pass
                     else "生产不动; v23 入研究档案 + 前视量化上报主人裁决"),
        "yearly_ml_v23": {str(y): (round(float(ml11.get(str(y), 0.0)), 4) if str(y) in ml11.index else None)
                          for y in range(2018, 2027)},
        "yearly_ml_v22": {str(y): (round(float(ml10.get(str(y), 0.0)), 4) if str(y) in ml10.index else None)
                          for y in range(2018, 2027)},
    }
    out = os.path.join(PREREG_DIR, "GATES_VERDICT.json")
    io.open(out, "w", encoding="utf-8").write(json.dumps(verdict, ensure_ascii=False, indent=1))
    print(json.dumps(verdict, ensure_ascii=False, indent=1))
    print("saved:", out)


if __name__ == "__main__":
    main()
