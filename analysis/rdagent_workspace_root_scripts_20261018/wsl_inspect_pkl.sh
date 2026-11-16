#!/usr/bin/env bash
# 解开 loop 2 running 步的 debug_tpl pkl, 看渲染后的 conf(feature_expressions等)
export PATH=/root/miniconda3/envs/rdagent4qlib/bin:$PATH
python - <<'PYEOF'
import pickle, glob, sys
files = sorted(glob.glob('/mnt/d/zcodesj/rdagent_workspace/log/2026-09-28_13-51-55-095648/Loop_2/running/debug_tpl/1262/*.pkl'))
print('n_pkls:', len(files))
for f in files[:2]:
    try:
        with open(f, 'rb') as fh:
            obj = pickle.load(fh)
        print('=== FILE:', f.split('/')[-1], 'TYPE:', type(obj))
        s = str(obj)
        print('LEN:', len(s))
        # 找特征表达式相关片段
        import re
        m = re.search(r'feature_expressions.{0,600}', s, re.S)
        if m:
            print('FEATURE_EXCERPT:', m.group(0)[:600])
        else:
            print('HEAD:', s[:300])
    except Exception as e:
        print(f, 'LOAD_FAIL', type(e).__name__, e)
PYEOF
echo UNPICKLE_DONE
