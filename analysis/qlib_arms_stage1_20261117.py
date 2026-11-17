# -*- coding: utf-8 -*-
"""qlib_arms_stage1_20261117.py — qlib 对照臂 stage-1 运行器 (研究层零接线)
预注册: reports/PREREG_QLIB_ARMS_20261117.md 指纹 2efcd483a19f12d73f6b2798044f335f 跑前冻结
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
PREREG = ROOT / "reports" / "PREREG_QLIB_ARMS_20261117.md"
PREREG_MD5 = "2efcd483a19f12d73f6b2798044f335f"
RESULTS = ROOT / "analysis" / "qlib_arms_stage1_20261117.jsonl"
LOG = ROOT / "analysis" / "qlib_arms_stage1_20261117.log"
OUT_MD = ROOT / "reports" / "QLIB_ARMS_STAGE1_20261117.md"
TEST_CAP = "2026-11-16"
MASTER_PKLS = [str(ROOT / "strategy_libs" / "MASTER" / "data" / "tulong" / ("pred_test_20260929_seed%d.pkl" % i)) for i in range(5)]


def log(msg):
    line = "[%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg)
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def check_prereg():
    got = hashlib.md5(PREREG.read_bytes()).hexdigest()
    if got != PREREG_MD5:
        log("预注册指纹不一致: got=%s expect=%s" % (got, PREREG_MD5))
        sys.exit(2)
    log("预注册指纹校验通过 %s" % got)


def build_dataset():
    sys.path.insert(0, r"D:\zcodesj\strategy_libs\MASTER")
    import tulong_dataset  # noqa: F401  (SPLITS 真源)
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
    log("DatasetH 构建完成 train=%s valid=%s test=%s~%s" % (tr, va, SPLITS["test"][0], TEST_CAP))
    return ds, SPLITS


def eval_ic(pred, label_df):
    df = pred.to_frame("pred").join(label_df, how="inner").dropna()
    g = df.groupby(level="datetime")
    ic = g.apply(lambda x: x["pred"].corr(x["LABEL0"], method="spearman") if len(x) >= 5 else np.nan)
    return ic.dropna()


def fmt_stats(ic):
    n = len(ic)
    mean = float(ic.mean())
    sd = float(ic.std(ddof=1))
    if sd > 0 and n > 1:
        t = mean / (sd / n ** 0.5)
    else:
        t = 0.0
    icir = mean / sd if sd > 0 else 0.0
    return {"n": n, "mean": mean, "sd": sd, "t": t, "icir": icir}


def run_arm(ds, name, mod_path, cls_name, kw):
    import importlib
    import pickle
    pth = ROOT / "analysis" / ("qlib_arms_pred_%s_20261117.pkl" % name)
    if pth.exists():
        log("%s 预测已存在, 跳过训练(幂等续跑)" % name)
        pred = pd.read_pickle(str(pth))
        return pred
    m = importlib.import_module(mod_path)
    cls = getattr(m, cls_name)
    from qlib.workflow import R
    log("%s 训练开始 %s" % (name, kw))
    t0 = time.time()
    with R.start(experiment_name="qlib_arms_20261117", recorder_name=name):
        model = cls(**kw)
        model.fit(ds)
        pred = model.predict(ds, segment="test")
    pred = pred[pred.index.get_level_values("datetime") <= pd.Timestamp(TEST_CAP)]
    pred.name = None
    pred.to_pickle(str(pth))
    log("%s 完成 %.0f秒 n=%d" % (name, time.time() - t0, len(pred)))
    log("%s 预测落盘 %s" % (name, pth.name))
    return pred


GATE_T = 2.0

ALSTM_KW = dict(d_feat=158, hidden_size=64, num_layers=2, dropout=0.0, n_epochs=30, lr=0.001, batch_size=2000, early_stop=10, GPU=0, seed=42)

GRU_KW = dict(d_feat=158, hidden_size=64, num_layers=2, dropout=0.0, n_epochs=30, lr=0.001, batch_size=2000, early_stop=10, GPU=0, seed=42)

TCN_KW = dict(d_feat=158, n_chans=128, kernel_size=5, num_layers=5, dropout=0.5, n_epochs=30, lr=0.0001, batch_size=2000, early_stop=10, GPU=0, seed=42)

ARMS = [("alstm", "qlib.contrib.model.pytorch_alstm", "ALSTM", ALSTM_KW), ("gru", "qlib.contrib.model.pytorch_gru", "GRU", GRU_KW), ("tcn", "qlib.contrib.model.pytorch_tcn", "TCN", TCN_KW)]



def master_baseline(label_df):
    import pickle
    out = []
    for p in MASTER_PKLS:
        if not Path(p).exists():
            log("MASTER归档缺失: " + p)
            sys.exit(2)
        s = pickle.load(open(p, "rb"))
        st = fmt_stats(eval_ic(s, label_df))
        out.append(st)
        log("MASTER seed RankIC: " + json.dumps(st))
    means = [x["mean"] for x in out]
    m_mean = float(np.mean(means))
    log("MASTER 5-seed 同协议基线 mean=%.4f" % m_mean)
    return m_mean


def main():
    check_prereg()
    ds, SPLITS = build_dataset()
    label_df = ds.prepare("test", col_set="label")
    log("label 就绪 %d 行" % len(label_df))
    m_mean = master_baseline(label_df)
    results = []
    for name, mod_path, cls_name, kw in ARMS:
        pred = run_arm(ds, name, mod_path, cls_name, kw)
        st = fmt_stats(eval_ic(pred, label_df))
        gate = bool(st["mean"] >= m_mean and st["t"] >= GATE_T)
        log("臂 %s: %s" % (name, json.dumps(st)))
        results.append({"arm": name, "stats": st, "gate_pass": gate})
    canon = json.dumps(results, sort_keys=True, ensure_ascii=False)
    md5 = hashlib.md5(canon.encode("utf-8")).hexdigest()
    with open(RESULTS, "a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "results": results}) + "\n")
    log("results 已落盘 %s" % RESULTS.name)
    print(json.dumps(results, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
