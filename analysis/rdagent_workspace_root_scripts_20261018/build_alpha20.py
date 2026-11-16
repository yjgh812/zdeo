# -*- coding: utf-8 -*-
"""从 rdagent trace pkl 提取 Loop 2 渲染特征配置, 并用 qlib 计算 Alpha158 式 20 特征缓存。"""
import ast
import re
from pathlib import Path

import qlib
from qlib.constant import REG_CN
from qlib.data import D

PKL = "/mnt/d/zcodesj/rdagent_workspace/log/2026-09-28_13-51-55-095648/Loop_2/running/debug_tpl/1262/2026-09-29_04-52-39-869429.pkl"
OUT = "/mnt/d/zcodesj/rdagent_workspace/git_ignore_folder/alpha20_features.parquet"


def main():
    raw = Path(PKL).read_bytes()
    text = raw.decode("utf-8", errors="ignore")
    m_e = re.search(r"feature_expressions': \"(\[.*?\])\"", text)
    m_n = re.search(r"feature_names': \"(\[.*?\])\"", text)
    assert m_e and m_n, "feature lists not found in pkl"
    exprs = ast.literal_eval(m_e.group(1))
    names = ast.literal_eval(m_n.group(1))
    assert len(exprs) == len(names), f"len mismatch {len(exprs)} vs {len(names)}"
    print(f"extracted {len(exprs)} features: {names[:5]}...")

    qlib.init(provider_uri="/root/.qlib/qlib_data/cn_data", region=REG_CN)
    df = D.features(D.instruments("csi300"), exprs, start_time="2016-01-01", end_time="2026-09-24")
    df.columns = names
    df.sort_index(inplace=True)
    df.to_parquet(OUT)
    print(f"ALPHA20_SAVED rows={len(df)} cols={list(df.columns)[:5]}...")
    print(df.tail(2))


if __name__ == "__main__":
    main()
