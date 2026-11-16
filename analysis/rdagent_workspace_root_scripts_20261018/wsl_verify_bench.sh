#!/usr/bin/env bash
# 验证 WSL qlib 能读 SH000300 基准(qlib表达式用文件避免转义)
export PATH=/root/miniconda3/envs/rdagent4qlib/bin:$PATH
python - <<'PYEOF'
import qlib
qlib.init(provider_uri='/root/.qlib/qlib_data/cn_data')
from qlib.data import D
df = D.features(['SH000300'], ['$close', '$factor'], start_time='2026-09-22', end_time='2026-09-24')
print(df)
print('BENCH_OK')
PYEOF
echo VERIFY_DONE
