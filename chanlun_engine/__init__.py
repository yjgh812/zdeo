"""
标准化缠论分解引擎 (Chanlun Engine)
=====================================
基于前4轮缠论学习的标准化规则，实现唯一分解。

八层架构：
  L1 K线包含（方向锚点法）
  L2 分型
  L3 笔（5K线硬规则 + 幅度阈值）
  L4 线段（特征序列三条件）
  L5 中枢（六要素 + 九段升级）
  L6 走势类型（盘整/趋势）
  L7 背驰（MACD面积法）
  L8 三类买卖点

使用：
  from chanlun_engine.analyzer import ChanAnalyzer
  analyzer = ChanAnalyzer()
  result = analyzer.analyze(df)   # df 含 date/open/high/low/close/vol

数据铁律：所有DB以 mode=ro 只读访问，K线走 stock_daily_hsj（前复权）。
"""
__version__ = "1.0.0"
