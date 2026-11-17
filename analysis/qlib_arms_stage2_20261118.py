# -*- coding: utf-8 -*-
"""qlib 对照臂 stage-2 运行器 (研究层零接线)
预注册 reports/PREREG_QLIB_ARMS_STAGE2_20261118.md 指纹 24bf9c631be3cf4ef7c0e7478ff7d912 跑前冻结
授权链 2026-11-18 主人「继续, 一次性跑完」令。批次1: TCTS / Localformer
闸门 test 日频 RankIC mean>=0.0390 且 t>=2 (跑后禁改); trial_budget=1; 12h 熔断 exit 3
组装裁决: n_epochs=30/early_stop=10 统一(预算段口径, 两臂同款);
TCTS 适配 input_dim=158 + lowest_valid_performance=0(防无限重训循环)
TCTS 预测=GRU 单步退化形态; TCTS valid 推理=自实现镜像(predict 无 segment 参数)
E) TCTS 臂剔除登记(预注册§2 结构化处置条款): 冒烟实证 qlib TCTS 实现 np.squeeze(y_train.values)
   将 Alpha158 单列 label 压成 1D → train_epoch transpose(0,1) IndexError;
   实现内部假设 5 列 label(output_dim=5 论文多目标), 单列 label 结构性不兼容,
   构造器参数不可修复(需改 qlib 库源码或改 label 数据合同, 两者均被冻结条款禁止) → 臂剔除
"""
import hashlib
import json
import os
import sys
import time
from pathlib import Path

os.environ["MLFLOW_ALLOW_FILE_STORE"] = "true"

import numpy as np
import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.data.dataset import DatasetH
from qlib.contrib.data.handler import Alpha158

ROOT = Path(r"D:\zcodesj")
PREREG = ROOT / "reports" / "PREREG_QLIB_ARMS_STAGE2_20261118.md"
PREREG_MD5 = "24bf9c631be3cf4ef7c0e7478ff7d912"
RESULTS = ROOT / "analysis" / "qlib_arms_stage2_20261118.jsonl"
LOG = ROOT / "analysis" / "qlib_arms_stage2_20261118.log"
OUT_MD = ROOT / "reports" / "QLIB_ARMS_STAGE2_20261118.md"
TEST_CAP = "2026-11-16"
BASELINE_MEAN = 0.0390
GATE_T = 2.0
FUSE_H = 12.0
SEEDS5 = [42, 43, 44, 45, 46]
T_START = time.time()
SMOKE = "--smoke" in sys.argv

TCTS_BASE = dict(d_feat=158, input_dim=158, dropout=0.1, n_epochs=30, early_stop=10,
                 batch_size=2000, GPU=0, seed=42, lowest_valid_performance=0.0)
TCTS_GRID = [{"hidden_size": 256, "num_layers": 3}, {"hidden_size": 128, "num_layers": 3},
             {"hidden_size": 256, "num_layers": 2}, {"hidden_size": 128, "num_layers": 2}]
LF_BASE = dict(d_feat=158, n_epochs=30, early_stop=10, GPU=0, seed=42)
LF_GRID = [{"d_model": 64, "dropout": 0.0}, {"d_model": 128, "dropout": 0.1},
           {"d_model": 128, "dropout": 0.0}, {"d_model": 64, "dropout": 0.1}]


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def elapsed_h():
    return (time.time() - T_START) / 3600.0


def check_fuse(stage):
    if elapsed_h() > FUSE_H:
        log("[FUSE] %s 前 elapsed=%.2fh > %.0fh → 结构化中止" % (stage, elapsed_h(), FUSE_H))
        append({"stage": "fuse_abort", "at_stage": stage})
        sys.exit(3)


def append(rec):
    rec = dict(rec)
    rec["ts"] = time.strftime("%Y-%m-%d %H:%M:%S")
    rec["elapsed_h"] = round(elapsed_h(), 3)
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")


def check_prereg():
    got = hashlib.md5(PREREG.read_bytes()).hexdigest()
    if got != PREREG_MD5:
        log("预注册指纹不一致: got=%s expect=%s" % (got, PREREG_MD5))
        sys.exit(2)
    log("预注册指纹校验通过 %s" % got)


def build_dataset():
    sys.path.insert(0, r"D:\zcodesj\strategy_libs\MASTER")
    import tulong_dataset  # noqa: F401  (SPLITS 真源, import 即前视重叠断言)
    from tulong_dataset import SPLITS
    qlib.init(provider_uri=r"D:\workbuddyspace\qlib_data\qlib_bin", region=REG_CN, kernels=4)
    log("qlib 初始化完成")
    tr, va = SPLITS["train"], SPLITS["valid"]
    from qlib.data.dataset.processor import DropnaProcessor
    h = Alpha158(instruments="csi300",
                 start_time=tr[0], end_time=TEST_CAP,
                 fit_start_time=tr[0], fit_end_time=tr[1],
                 learn_processors=[DropnaProcessor()],
                 infer_processors=[])
    ds = DatasetH(h, {"train": tr, "valid": va, "test": (SPLITS["test"][0], TEST_CAP)})
    log("DatasetH 构建完成 train=%s valid选优2025/闸门2026零漂移=%s" % (tr, TEST_CAP))
    return ds, SPLITS


def eval_ic(pred, label_df):
    df = pred.to_frame("pred").join(label_df, how="inner").dropna()
    g = df.groupby(level="datetime")
    ic = g.apply(lambda x: x["pred"].corr(x["LABEL0"], method="spearman") if len(x) >= 5 else np.nan)
    return ic.dropna()


def fmt_stats(ic):
    n = len(ic)
    mean = float(ic.mean())
    sd = float(ic.std(ddof=1)) if n > 1 else 0.0
    t = mean / (sd / n ** 0.5) if (sd > 0 and n > 1) else 0.0
    icir = mean / sd if sd > 0 else 0.0
    return {"n": int(n), "mean": round(mean, 6), "sd": round(sd, 6), "t": round(t, 4), "icir": round(icir, 4)}


def fwd20_labels():
    from qlib.data import D
    df = D.features(D.instruments("csi300"), ["Ref($close,20)/$close-1"],
                    start_time="2026-01-01", end_time=TEST_CAP)
    df.columns = ["fwd20"]
    return df


def eval_fwd20(pred, fwd20_df):
    df = pred.to_frame("pred").join(fwd20_df, how="inner").dropna()
    if df.empty:
        return {"n": 0, "mean": None, "t": None}
    dt = df.index.get_level_values("datetime")
    df = df.assign(_m=dt.to_period("M"))
    last_day = df.reset_index().groupby("_m")["datetime"].max()
    sel = df[df.index.get_level_values("datetime").isin(set(last_day))]
    ic = sel.groupby(level="datetime").apply(
        lambda x: x["pred"].corr(x["fwd20"], method="spearman") if len(x) >= 5 else np.nan).dropna()
    return fmt_stats(ic)


def fit_predict_unit(ds, arm, tag, mod_path, cls_name, kw, segment):
    import importlib
    pth = ROOT / "analysis" / ("qlib_arms_stage2_%s_pred.pkl" % tag)
    if pth.exists():
        log("%s 已存在, 跳过(幂等)" % tag)
        return pd.read_pickle(str(pth))
    check_fuse("%s/%s" % (arm, tag))
    from qlib.workflow import R
    m = importlib.import_module(mod_path)
    cls = getattr(m, cls_name)
    log("%s 训练开始 kw=%s" % (tag, json.dumps(kw, default=str)))
    t0 = time.time()
    with R.start(experiment_name="qlib_arms_stage2_20261118", recorder_name=tag):
        model = cls(**kw)
        model.fit(ds)
        if arm == "tcts":
            pred = tcts_infer(model, ds, segment)
        else:
            pred = model.predict(ds, segment=segment)
    t1 = time.time()
    pred = pred.dropna()
    log("%s 完成 %.0f秒 pred=%d行" % (tag, t1 - t0, len(pred)))
    pred.to_pickle(str(pth))
    if not SMOKE:
        append({"stage": "unit", "arm": arm, "tag": tag, "segment": segment,
                "unit_seconds": round(t1 - t0, 1), "pred_rows": int(len(pred))})
    return pred


def tcts_infer(model, ds, segment):
    import torch
    x = ds.prepare(segment, col_set="feature")
    model.fore_model.eval()
    vals = x.values.astype("float32")
    out = []
    bs = int(model.batch_size)
    for begin in range(len(vals))[::bs]:
        end = min(begin + bs, len(vals))
        xb = torch.from_numpy(vals[begin:end]).to(model.device)
        with torch.no_grad():
            out.append(model.fore_model(xb).detach().cpu().numpy().ravel())
    return pd.Series(np.concatenate(out), index=x.index)


def canon_md5(preds_by_seed):
    h = hashlib.md5()
    for k in sorted(preds_by_seed):
        s = preds_by_seed[k].sort_index()
        h.update(str(k).encode())
        h.update(str(list(s.index)).encode())
        h.update(np.round(s.values.astype("float64"), 10).tobytes())
    return h.hexdigest()


def fiveseed(ds, arm, mod_path, cls_name, kw, gi, label_df, fwd20_df, run_tag):
    preds_by_seed = {}
    seed_stats = {}
    for s in SEEDS5:
        kw_s = dict(kw)
        kw_s["seed"] = s
        tag5 = "%s_best_g%d_seed%d%s" % (arm, gi, s, run_tag)
        p = fit_predict_unit(ds, arm, tag5, mod_path, cls_name, kw_s, "test")
        preds_by_seed[s] = p
        seed_stats[s] = fmt_stats(eval_ic(p, label_df))
        log("[5seed] %s seed%d test=%s" % (arm, s, json.dumps(seed_stats[s])))
    ens = pd.concat(preds_by_seed.values(), axis=1).mean(axis=1)
    st = fmt_stats(eval_ic(ens, label_df))
    f20 = eval_fwd20(ens, fwd20_df)
    gate = bool(st["mean"] >= BASELINE_MEAN and st["t"] >= GATE_T)
    log("[gate] %s r=%s ens=%s fwd20=%s gate=%s" % (arm, run_tag, json.dumps(st), json.dumps(f20), gate))
    append({"stage": "fiveseed", "arm": arm, "run": run_tag, "seed_stats": seed_stats,
            "ens_stats": st, "fwd20_stats": f20, "gate_pass": gate})
    return {"ens": ens, "stats": st, "fwd20": f20, "gate_pass": gate, "preds_by_seed": preds_by_seed}


def run_arm(ds, arm, mod_path, cls_name, grid, base_kw, label_df, valid_label_df, fwd20_df):
    best = None
    for gi, g in enumerate(grid):
        kw = dict(base_kw)
        kw.update(g)
        tag = "%s_g%d_v" % (arm, gi)
        p = fit_predict_unit(ds, arm, tag, mod_path, cls_name, kw, "valid")
        st_v = fmt_stats(eval_ic(p, valid_label_df))
        log("[grid] %s g%d valid=%s" % (arm, gi, json.dumps(st_v)))
        append({"stage": "grid", "arm": arm, "grid_idx": gi, "grid": g, "valid_stats": st_v})
        if best is None or st_v["mean"] > best[1]["mean"]:
            best = (gi, st_v, kw)
    gi, st_v, kw = best
    log("[select] %s 最优 g%d valid mean=%.4f" % (arm, gi, st_v["mean"]))
    append({"stage": "select", "arm": arm, "best_grid_idx": gi, "valid_stats": st_v})
    r1 = fiveseed(ds, arm, mod_path, cls_name, kw, gi, label_df, fwd20_df, "_r1")
    r2 = fiveseed(ds, arm, mod_path, cls_name, kw, gi, label_df, fwd20_df, "_r2")
    m1, m2 = canon_md5(r1["preds_by_seed"]), canon_md5(r2["preds_by_seed"])
    match = bool(m1 == m2)
    log("[double] %s md5 r1=%s r2=%s 一致=%s" % (arm, m1, m2, match))
    append({"stage": "double", "arm": arm, "md5_r1": m1, "md5_r2": m2, "match": match})
    return {"arm": arm, "best_grid_idx": gi, "best_kw": kw, "r1": {k: r1[k] for k in ("stats", "fwd20", "gate_pass")},
            "r2": {k: r2[k] for k in ("stats", "fwd20", "gate_pass")}, "md5_match": match}


def main():
    check_prereg()
    ds, SPLITS = build_dataset()
    label_df = ds.prepare("test", col_set="label")
    valid_label_df = ds.prepare("valid", col_set="label")
    log("label 就绪 test=%d行 valid=%d行" % (len(label_df), len(valid_label_df)))
    fwd20_df = fwd20_labels()
    log("fwd20 副口径 label 就绪 %d 行" % len(fwd20_df))

    arms = [
        # TCTS 臂按预注册§2 结构化处置条款剔除(冒烟实证: np.squeeze 单列 label→1D→transpose
        # IndexError; 5列label假设; 构造器参数不可修复)。证据: 冒烟栈+shape 实证+源码 130-155 行。
        ("localformer", "qlib.contrib.model.pytorch_localformer", "LocalformerModel", LF_GRID, LF_BASE),
    ]
    finals = []
    if not SMOKE:
        append({"stage": "arm_drop", "arm": "tcts",
                "reason": "qlib TCTS np.squeeze 单列label→1D→transpose IndexError; 5列label假设; "
                          "构造器参数不可修复(需改qlib源码或label合同, 冻结条款均禁); 预注册§2条款剔除"})
    for arm, mod_path, cls_name, grid, base_kw in arms:
        if SMOKE:
            kw = dict(base_kw)
            kw["n_epochs"] = 1
            kw.update(grid[0])
            t0 = time.time()
            _ = fit_predict_unit(ds, arm, "%s_smoke_v" % arm, mod_path, cls_name, kw, "valid")
            tv = time.time() - t0
            t1 = time.time()
            _ = fit_predict_unit(ds, arm, "%s_smoke_t" % arm, mod_path, cls_name, kw, "test")
            tt = time.time() - t1
            log("[smoke] %s 1epoch 单元耗时 valid=%.0fs test=%.0fs → 30ep 单格估 %.1f~%.1fh" %
                (arm, tv, tt, tv * 30 / 3600 * 0.8, tv * 30 / 3600 * 1.5))
            continue
        res = run_arm(ds, arm, mod_path, cls_name, grid, base_kw, label_df, valid_label_df, fwd20_df)
        finals.append(res)
    if SMOKE:
        log("[smoke] 管线验证完成, 产物不入正式 jsonl")
        return 0
    canon = json.dumps(finals, sort_keys=True, ensure_ascii=False)
    md5 = hashlib.md5(canon.encode("utf-8")).hexdigest()
    append({"stage": "final", "results": finals, "canon_md5": md5})
    log("final 落盘 canon_md5=%s" % md5)
    print(json.dumps(finals, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
