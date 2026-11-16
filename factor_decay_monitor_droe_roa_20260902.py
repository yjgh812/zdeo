# -*- coding: utf-8 -*-
"""droe/roa 因子衰减监控驱动 (2026-09-02, 9月评审触发链第3步: H1口径IC复测)

预注册语境: reports/SEPTEMBER_REVIEW_MATERIALS_20260901.md §1.4 决策树第2步——
「H1 口径清单就绪后，重跑 60d IC：转负或 < 基线 40% → 维持退役方向；显著回正 → B 组继续研究层观察」
基线锚(9/1 实测): droe 60d IC +0.0304(基线的54.8%, 即全史基线≈0.0555) / roa +0.0478。

实现注记: droe/roa 是 PIT 扩展列, 不在 FACTOR_REGISTRY(dragon/fund 表)——路由
load_factor_values → load_pit_factors(factor_pit.db v2, merge_asof 无前视, 生产同源)。
DECAY_DIR 重定向到独立研究目录(不覆盖 bp/ep 共享监控产物)。
只读研究脚本: 不改生产配置。
"""
import sys
from pathlib import Path

sys.path.insert(0, r"D:\zcodesj\multifactor")

import factor_loader
import factor_decay_monitor as fdm

OUT_DIR = Path(r"D:\zcodesj\reports\droeroa_decay_20260902")
OUT_DIR.mkdir(parents=True, exist_ok=True)
fdm.DECAY_DIR = OUT_DIR  # 重定向产物目录

_orig_lfv = factor_loader.load_factor_values


def _routed_load_factor_values(factor, start="20180101", end="20260901"):
    if factor["name"] in ("droe", "roa"):
        return factor_loader.load_pit_factors(factor["name"], start=start, end=end)
    return _orig_lfv(factor, start=start, end=end)


if __name__ == "__main__":
    factor_loader.load_factor_values = _routed_load_factor_values
    factors = [{"name": "droe"}, {"name": "roa"}]
    print("目标因子:", [f["name"] for f in factors], "(源=factor_pit.db v2 PIT口径, 含H1)")
    fdm.run_decay_monitor(factors, start="20180101", end="20260901")
