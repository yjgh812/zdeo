#!/usr/bin/env bash
# WSL 侧 sh000300 bin 构建(参数内置, 防MSYS路径劫持)
cd /root
/root/miniconda3/envs/rdagent4qlib/bin/python /mnt/d/zcodesj/rdagent_workspace/build_index_bin.py /root/.qlib/qlib_data/cn_data /mnt/d/anzhuang/tdx/vipdoc/sh/lday/sh000300.day
echo "BUILD_RC=$?"
