# -*- coding: utf-8 -*-
"""PortraitAgent — 上涨股票AI画像Agent（新项目）
按《屠龙系统·A股上涨股票AI画像Agent自主执行计划书》建设
（主人 2026-09-05 裁决：新项目，不受既有经验限制，按计划书走）.

模块:
  event_bus.py      轻量消息总线 C1_PORTRAIT_CMD / C2_PORTRAIT_RESULT / C3_PORTRAIT_ALERT
  feature_engine.py 五维特征引擎(沪深A股含科创板; 剔北交/ST/次新60日; ≤20元; 流动性过滤)
  model_v1.py       L1-LightGBM基线模型(双标签: 5日最高≥8% / 20日收盘≥15%)
  coldstart.py      冷启动自检+编排(计划书§2.1/§2.2)

版本: v1.0.0-A股 | 数据源: qfq真库+fundamental_data.db(复用本地), 北向停采标注
"""
__version__ = "1.0.0-A股"
