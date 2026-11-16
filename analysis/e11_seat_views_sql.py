#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""E11 席位共现三维视图（已验证 SQL 集，2026-10-07，只读）。

背景：PD-037 候选 E11（源自雪球席位重合度分析帖的外部印证）——在 PD-032
lhb_seat_cooccur（同股同日共现）之外增补三个读法维度。本文件固化三条
**已在 a-share-local MCP（database=fund, 表 lhb_seat_detail, 2,393,524 行
2010-01-04~2026-09-30）实测通过** 的 SQL，供后续固化为定时视图/报表时取用。

口径要点（实测发现的坑，务必遵守）：
  1) operate_dept_name 为混合口径：既有个股营业部明细（LIKE '%证券%'/'%营业部%'，
     2026-9 月 10,309 行/1,665 席位），也有投资者类别聚合行（自然人/机构投资者/
     中小投资者等 11 类，4,587 行）——身份级追踪**必须过滤 broker_branch**，
     否则会把类别间对峙误当席位对峙（两类行同表混存）；
  2) trade_date 为 'YYYY-MM-DD' 带横线格式；
  3) side 取值 'BUY'/'SELL'（大写）；
  4) 111xxx/123xxx 等可转债代码也在表内，股票口径须另加代码过滤（屠龙不用可转债）；
  5) 2026-09 窗口实测：V1 对峙峰值=华宝东大名路 vs 华鑫陆家嘴 15.45 亿（9/16，
     111024 可转债）；V2 高盛世纪大道 9/28 卖山东黄金 2.29 亿→1~2 日内铺 5 只
     （铺赛道型切换实时样本）；V3 华鑫陆家嘴 9/16 买 7.1 亿=自身 2024-09 以来
     99.8 分位（罕见体量=配置型信号机械化）。

三视图 SQL（占位 :thr:=3e7 门槛, :big:=1e8, 窗口按需替换）：
"""

SQL_V1_DUEL = """
SELECT a.trade_date, a.ts_code, a.security_name,
       a.operate_dept_name AS buy_seat, ROUND(a.buy/1e8,2) AS buy_yi,
       b.operate_dept_name AS sell_seat, ROUND(b.sell/1e8,2) AS sell_yi,
       ROUND((a.buy+b.sell)/1e8,2) AS total_yi
FROM lhb_seat_detail a
JOIN lhb_seat_detail b
  ON a.trade_date=b.trade_date AND a.ts_code=b.ts_code
 AND a.side='BUY' AND b.side='SELL'
 AND a.operate_dept_name<>b.operate_dept_name
WHERE a.trade_date>='2026-09-01'
  AND (a.operate_dept_name LIKE '%证券%' OR a.operate_dept_name LIKE '%营业部%')
  AND (b.operate_dept_name LIKE '%证券%' OR b.operate_dept_name LIKE '%营业部%')
  AND a.buy>=:thr AND b.sell>=:thr
ORDER BY total_yi DESC
"""

SQL_V2_SWITCH = """
SELECT a.operate_dept_name AS seat,
       a.trade_date AS sell_day, a.ts_code AS sold_code, ROUND(a.sell/1e8,2) AS sold_yi,
       b.trade_date AS buy_day,  b.ts_code AS bought_code, ROUND(b.buy/1e8,2) AS bought_yi,
       CAST(julianday(b.trade_date)-julianday(a.trade_date) AS INT) AS gap_days
FROM lhb_seat_detail a
JOIN lhb_seat_detail b
  ON a.operate_dept_name=b.operate_dept_name AND b.side='BUY' AND a.side='SELL'
 AND b.ts_code<>a.ts_code
 AND julianday(b.trade_date)>julianday(a.trade_date)
 AND julianday(b.trade_date)-julianday(a.trade_date) BETWEEN 1 AND 4
WHERE a.trade_date>=:win_start AND a.trade_date<=:win_end
  AND b.trade_date>=:win_start
  AND a.sell>=:thr AND b.buy>=:thr
  AND (a.operate_dept_name LIKE '%证券%' OR a.operate_dept_name LIKE '%营业部%')
ORDER BY a.sell DESC
"""

SQL_V3_PERCENTILE = """
SELECT trade_date, ts_code, operate_dept_name AS seat,
       ROUND(buy/1e8,2) AS buy_yi,
       ROUND(prk*100,1) AS pct_in_own_hist
FROM (SELECT trade_date, ts_code, operate_dept_name, buy,
             PERCENT_RANK() OVER (PARTITION BY operate_dept_name ORDER BY buy) AS prk
      FROM lhb_seat_detail
      WHERE side='BUY' AND trade_date>=:hist_start AND buy>0)
WHERE trade_date>=:win_start AND buy>=:big
  AND (operate_dept_name LIKE '%证券%' OR operate_dept_name LIKE '%营业部%')
ORDER BY buy DESC
"""

if __name__ == "__main__":
    print(__doc__)
    for name in ("SQL_V1_DUEL", "SQL_V2_SWITCH", "SQL_V3_PERCENTILE"):
        print(f"\n===== {name} =====")
        print(globals()[name])
