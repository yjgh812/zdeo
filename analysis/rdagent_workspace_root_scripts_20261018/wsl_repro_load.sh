#!/usr/bin/env bash
# 隔离复现: StaticDataLoader.load 直调
export PATH=/root/miniconda3/envs/rdagent4qlib/bin:$PATH
python - <<'PYEOF'
import pandas as pd, qlib
from qlib.constant import REG_CN
from qlib.data import D
from qlib.data.dataset.loader import StaticDataLoader
qlib.init(provider_uri='/root/.qlib/qlib_data/cn_data', region=REG_CN)
label_df = D.features(D.instruments('csi300'), ['Ref($close, -2)/Ref($close, -1) - 1'], start_time='2016-01-01', end_time='2026-09-24')
label_df.columns = ['LABEL0']; label_df.sort_index(inplace=True)
feat_df = pd.read_parquet('/mnt/d/zcodesj/rdagent_workspace/git_ignore_folder/RD-Agent_workspace/290b2833fec64768a4b39a9ee4778f79/combined_factors_df.parquet')
feat_df.columns = feat_df.columns.droplevel(0); feat_df.sort_index(inplace=True)
data = pd.concat({'feature': feat_df, 'label': label_df}, axis=1, join='outer')
data.sort_index(inplace=True)
print('data depth:', data.index._lexsort_depth)
inst_list = D.list_instruments(D.instruments('csi300'), as_list=True)
loader = StaticDataLoader(config=data)
out = loader.load(inst_list, '2017-01-01', '2024-06-30')
print('LOAD_OK', out.shape)
PYEOF
echo REPRO_DONE
