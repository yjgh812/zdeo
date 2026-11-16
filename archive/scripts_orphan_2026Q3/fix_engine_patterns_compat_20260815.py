#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fix_engine_patterns_compat_20260815.py — 一次性修正：engine_v2范式行对齐下游消费方口径

【背景】8/15批量回灌的216条engine_v2行与mainline_candidate_screener/mainline_radar的
手工行口径不兼容（本轮自查发现，属数据写入方责任）：
  1. predictability_score 存了引擎1-5口径，screener阈值7.0（手工5.5-9.0口径）→候选全灭
  2. seal_status="封板0.78亿/炸板0次"无空格，screener解析不到炸板次数→封板过滤失效
  3. board列NULL（应为沪市主板类）、industry存申万口径（手工为东财口径）
【修正】predictability_score=3+引擎predictability（news/fund主归因→7-8达标，tech→6）；
        其余三字段从涨停池重拉。幂等可重跑。
"""
import sqlite3
import sys

import akshare as ak

DB = r"D:\zcodesj\attribution\attribution.db"
DATES = ("20260812", "20260813", "20260814")


def main():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    total = 0
    for dt in DATES:
        try:
            df = ak.stock_zt_pool_em(date=dt)
        except Exception as e:
            print(f"[{dt}] 涨停池拉取失败: {e}")
            continue
        fixed = 0
        for _, zt in df.iterrows():
            code6 = str(zt["代码"]).zfill(6)
            board_name = ("创业板" if code6.startswith("30") else
                          "科创板" if code6.startswith("68") else
                          "北交所" if code6.startswith(("8", "4", "92")) else
                          "沪市主板" if code6.startswith("6") else "深市主板")
            broken = int(zt.get("炸板次数") or 0)
            ft = str(zt.get("首次封板时间") or "")
            seal_txt = (f"首封{ft[:2]}:{ft[2:4]} "
                        f"封板{(float(zt.get('封板资金') or 0) / 1e8):.2f}亿 {broken}炸")
            cur.execute(
                """UPDATE limit_up_patterns SET industry=?, board=?, limit_count=?, seal_status=?
                   WHERE trade_date=? AND code=? AND pattern_type='engine_v2'""",
                (str(zt.get("所属行业") or ""), board_name,
                 int(zt.get("连板数") or 1), seal_txt, dt, code6))
            fixed += cur.rowcount if cur.rowcount > 0 else 0
        # predictability口径换算：join引擎日志 3+predictability
        cur.execute("""
            UPDATE limit_up_patterns SET predictability_score = COALESCE(
                (SELECT 3 + l.predictability FROM engine_attribution_log l
                 WHERE l.event_date = limit_up_patterns.trade_date
                   AND (l.code = 'sz'||limit_up_patterns.code
                        OR l.code = 'sh'||limit_up_patterns.code
                        OR l.code = 'bj'||limit_up_patterns.code)), 6)
            WHERE trade_date=? AND pattern_type='engine_v2'""", (dt,))
        con.commit()
        total += fixed
        print(f"[{dt}] 修正{fixed}条")
    # 归因字段翻中文 + feature_tags注入中文主题词（radar按中文关键词匹配召回）
    zh = {"tech": "技术驱动", "fund": "基本面驱动", "cap": "资金驱动",
          "sector": "板块共振", "news": "消息驱动", "mkt": "市场情绪"}
    rows = cur.execute(
        "SELECT p.id, p.trade_date, p.code, p.industry, p.primary_attribution, "
        "p.secondary_attribution, p.feature_tags, l.main_attribution, l.sub_attribution "
        "FROM limit_up_patterns p LEFT JOIN engine_attribution_log l "
        "ON l.event_date=p.trade_date AND (l.code='sz'||p.code OR l.code='sh'||p.code OR l.code='bj'||p.code) "
        "WHERE p.pattern_type='engine_v2'").fetchall()
    import json as _json
    for r in rows:
        pid, td, code6, ind, pa, sa, tags, lmain, lsub = r
        m = lmain or pa
        s = lsub or sa
        try:
            tg = _json.loads(tags) if tags else {}
            if isinstance(tg, dict):
                tg.setdefault("main", m)
                tg.setdefault("sub", s)
                tg["main_zh"] = zh.get(m, m)
                tg["sub_zh"] = zh.get(s, s)
                tg["industry"] = ind
                tags_new = _json.dumps(tg, ensure_ascii=False)
            else:
                tags_new = tags
        except Exception:
            tags_new = tags
        cur.execute("UPDATE limit_up_patterns SET primary_attribution=?, secondary_attribution=?, "
                    "feature_tags=? WHERE id=?",
                    (zh.get(m, m), zh.get(s, s), tags_new, pid))
    con.commit()
    # 大跌库avoidability口径同步（可避度1-10手工口径,引擎controllability同为1-10 ✓ 无需改）
    n = cur.execute("SELECT COUNT(*) FROM limit_up_patterns WHERE pattern_type='engine_v2'").fetchone()[0]
    scored = cur.execute("SELECT COUNT(*) FROM limit_up_patterns WHERE pattern_type='engine_v2' AND predictability_score>=7").fetchone()[0]
    con.close()
    print(f"完成: engine_v2共{n}条，达标(>=7) {scored} 条")
    return 0


if __name__ == "__main__":
    sys.exit(main())
