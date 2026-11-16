# -*- coding: utf-8 -*-
"""pyfolio 冒烟测试：标准用法 import pandas as pd / matplotlib.pyplot as plt / pyfolio
合成 3 年日收益率 -> 完整 returns tear sheet + 关键指标，验证 Python 3.13 / pandas 2.3 / matplotlib 3.10 兼容性
"""
import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")  # 无窗口环境强制 Agg 后端
import matplotlib.pyplot as plt
import pyfolio as pf
import empyrical

# 1) 基础导入信息
print("pyfolio version :", pf.__version__)
print("pandas version  :", pd.__version__)
print("matplotlib ver  :", matplotlib.__version__)

# 2) 合成日收益率序列（252*3 交易日，工作日索引）
rng = np.random.default_rng(42)
dates = pd.bdate_range("2023-01-03", periods=756)
returns = pd.Series(rng.normal(0.0005, 0.015, len(dates)), index=dates)
returns.index.name = "date"

# 3) 核心指标（empyrical 引擎，pyfolio.timeseries 旧接口已弃用）
stats = {
    "annual_return": empyrical.annual_return(returns),
    "annual_volatility": empyrical.annual_volatility(returns),
    "sharpe_ratio": empyrical.sharpe_ratio(returns),
    "max_drawdown": empyrical.max_drawdown(returns),
    "calmar_ratio": empyrical.calmar_ratio(returns),
}
for k, v in stats.items():
    print(f"{k:>18}: {v:+.4f}")

# 4) 完整 tear sheet 落盘（14 张子图拼一张）
fig = pf.create_returns_tear_sheet(returns, return_fig=True)
out = r"D:\zcodesj\reports\pyfolio_smoke_tearsheet.png"
fig.savefig(out, dpi=110, bbox_inches="tight")
plt.close(fig)

# 5) 单图 API 抽查：累计收益 + 回撤
fig2, (ax1, ax2) = plt.subplots(2, 1, figsize=(10, 6), sharex=True)
pf.plotting.plot_rolling_returns(returns, ax=ax1)
pf.plotting.plot_drawdown_underwater(returns, ax=ax2)
fig2.tight_layout()
out2 = r"D:\zcodesj\reports\pyfolio_smoke_cum_dd.png"
fig2.savefig(out2, dpi=110, bbox_inches="tight")
plt.close(fig2)

print("tear sheet saved :", out)
print("cum/dd saved     :", out2)
print("SMOKE TEST PASS")
