#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""[SUPERSEDED 2026-09-28] 本脚本已退役，勿再调用。

退役原因（全维度自省 S1 定谳，reports/full_dim_self_review_20260928.md）：
1. 从未挂入任何调度：22:00 十三合一第六步只调 feed_feedback_tables.py all，
   本脚本无 cron/schtasks/链内调用者（grep 全仓仅 dashboard/morning_screen 的提示文案引用）；
2. 口径过时且必败：trade_date 解析用 '%Y-%m-%d' 而 verification_trades 实存格式为
   YYYYMMDD → strptime 抛异常 continue，fillback 段 0 行生效；
   且其收益回填用 candidates_latest.json「现价」冒充 T+1/T+3/T+5 三口径，属错误近似；
3. result 标注合同（T+1 ±0.5% → confirmed/refuted/inconclusive）已并入唯一 owner
   feed_feedback_tables.py do_update()（随 22:00 链每日执行，幂等）。

历史影响：result 列自建表以来 294/294 全空（无 owner 静默空转），已于 2026-09-28 补标。

用法：python D:/zcodesj/feed_feedback_tables.py update
"""
import sys

if __name__ == "__main__":
    print("[SUPERSEDED] afternoon_fillback.py 已退役(2026-09-28 S1)。"
          "验证反馈闭环唯一入口 = python D:/zcodesj/feed_feedback_tables.py "
          "{sync|snapshot|update|all}（随22:00链第六步日跑）。")
    sys.exit(2)
