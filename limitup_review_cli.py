#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""每日复盘涨停范式入库 CLI limitup_review_cli v1 (2026-09-09)

【定位】复盘主线/次主线的**唯一写入通道**（主人 2026-09-09 裁决口径：白名单主线/
次主线由每日复盘人工判断（财联社收盘复盘页 cls.cn/depth?id=1003 验证）决定）。
替代"每日复制 limitup_review_write_2026xxxx.py 一次性脚本"的脏模式（9/8 实锚：
断更 9/5-9/7 无人发现=供给无 owner、脚本日日复制）。

【语义边界】"XX主线/XX支线"标签 = 复盘人工判断的结构化载体：内置主题词表源自
9/8 复盘（THEME_AGR 等 8 主题），rows.json 的 "themes" 段可按当日市场情况覆盖/
扩展词表（复盘会话验证结果后调用）。引擎口径行（engine_v2，backfill 写）不含
主题标签，本 CLI 只删本批次行（created_at=批次标识），永不触碰 engine_v2 行。

【用法】
  python limitup_review_cli.py append --date 20260909 --rows rows_20260909.json
    rows.json = {"rows":[{code,name,limit_count,first_hhmm,seal_money,turnover,industry,reason}],
                 "themes":{"主题名":["关键词",...]}(可选,覆盖内置词表),
                 "batch":"review_20260909"(可选,缺省按日期生成)}
  python limitup_review_cli.py check          # 覆盖/口径分布/滞后体检

schema B 合同（与 attribution_patterns_backfill / limitup_review_write_20260908 一致）：
  18 列；幂等键=created_at 批次常量（pattern_type 是结构化标签不可作幂等键）；
  seal_status 诚实边界=不写炸板次数（未采集不写"0炸"）；first_limit_time 秒位补 00。
铁律：纯复盘入库，零交易权；写前不校验主题对错（人工判断为真源），check 只看覆盖。
"""
import argparse
import datetime
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ATTR_DB = ROOT / "attribution" / "attribution.db"
QFQ_DB = Path(r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db")

# 内置主题词表（源自 2026-09-08 复盘；每日复盘可按市场情况在 rows.json.themes 覆盖/扩展）
DEFAULT_THEMES = {
    "农业农化主线": {"kw": ("种业", "玉米", "制种", "糖", "化肥", "尿素", "农", "粮", "磷", "油脂", "橡胶", "饲料", "棉", "种植"), "inds": ()},
    "出版AI应用主线": {"kw": ("出版", "影视", "传媒", "短剧", "IP", "文旅", "文创", "院线"), "inds": ("出版业", "影视音像", "广告包装")},
    "算力硬件支线": {"kw": ("PCB", "HDI", "光模块", "存储", "算力", "FPGA", "光纤", "CPO", "光通信", "封测", "芯片", "氦气"), "inds": ()},
    "机器人高位博弈": {"kw": ("机器人", "丝杠"), "inds": ()},
    "医疗CXO支线": {"kw": ("CRO", "药", "培养基"), "inds": ("医疗保健", "中成药", "化学制药")},
    "化工油气地缘支线": {"kw": ("煤化工", "甲醇", "焦炭", "油气", "石油", "天然气", "页岩气", "供气", "氦气", "炼化", "成品油", "有机硅", "炭黑", "铜"), "inds": ()},
    "地产零售低位补涨": {"kw": ("房产", "物业", "零售", "商业", "百货", "地产", "经纪"), "inds": ("房产服务", "全国地产", "百货", "超市连锁", "批发业")},
}
PATTERN_SCORE = {"主线梯队连板": 8, "主线首板": 6, "支线连板": 5, "支线首板": 5,
                 "事件催化首板": 5, "补涨连板": 4, "低位补涨首板": 4, "题材脉冲首板": 3}
PATTERN_SV = {"主线梯队连板": "主线梯队,续板样本", "主线首板": "主线新进,梯队补位观察",
              "支线连板": "支线接力,短线", "支线首板": "支线首板,短线",
              "事件催化首板": "事件驱动,一日游风险", "补涨连板": "补涨扩散,模式外",
              "低位补涨首板": "低位补涨扩散,模式外", "题材脉冲首板": "随机脉冲,模式外"}
REQ_FIELDS = ("code", "name", "limit_count", "first_hhmm", "seal_money", "turnover", "industry", "reason")


def board_of(code: str) -> str:
    if code.startswith("60"):
        return "沪市主板"
    if code.startswith("30"):
        return "创业板"
    if code.startswith("68"):
        return "科创板"
    return "深市主板"


def classify(row: dict, themes: dict):
    reason, ind, lc = row["reason"], row["industry"], int(row["limit_count"])
    theme = None
    for t, cfg in themes.items():
        if any(k in reason for k in cfg["kw"]) or ind in cfg["inds"]:
            theme = t
            break
    if theme is None:
        theme, main, sub = "散点脉冲", "资金驱动", "技术驱动"
    else:
        # 主线/支线归属与 attribution 主次由主题名语义决定（9/8 合同口径）
        main, sub = ("板块共振", "消息驱动") if theme.endswith("主线") else ("消息驱动", "板块共振")
    if lc >= 2:
        if theme and theme.endswith("主线"):
            pt = "主线梯队连板"
        elif theme == "地产零售低位补涨":
            pt = "补涨连板"
        else:
            pt = "支线连板"
    else:
        if theme and theme.endswith("主线"):
            pt = "主线首板"
        elif theme == "地产零售低位补涨":
            pt = "低位补涨首板"
        elif theme == "化工油气地缘支线":
            pt = "事件催化首板"
        elif theme == "散点脉冲":
            pt = "题材脉冲首板"
        else:
            pt = "支线首板"
    score = PATTERN_SCORE[pt]
    sv = PATTERN_SV[pt]
    return theme, main, sub, pt, score, sv


def cmd_append(a):
    if not a.rows or not Path(a.rows).exists():
        print(f"[review_cli][ERROR] rows 文件不存在: {a.rows}")
        return 2
    data = json.loads(Path(a.rows).read_text(encoding="utf-8"))
    rows = data.get("rows") or []
    if not rows:
        print("[review_cli][ERROR] rows 为空（复盘未产出清单），拒绝入库")
        return 2
    themes = dict(DEFAULT_THEMES)
    for t, cfg in (data.get("themes") or {}).items():  # 复盘会话按当日市场情况覆盖词表
        themes[t] = {"kw": tuple(cfg.get("kw") or ()), "inds": tuple(cfg.get("inds") or ())} if isinstance(cfg, dict) else {"kw": tuple(cfg), "inds": ()}
    date = int(a.date.replace("-", ""))
    # 2026-09-10 B-M6: 缺省 batch 改按日稳定(原含 HHMM → 跨分钟重跑生成新批次、复盘行
    # 同码重复, 与 firstboard_cluster_probe 双行计数失真同族); 重跑=替换当日批次
    batch = data.get("batch") or f"review_cli_{date}"
    bad = [r for r in rows if any(r.get(f) in (None, "") for f in ("code", "name", "limit_count", "first_hhmm", "seal_money", "turnover", "reason"))]
    if bad:
        print(f"[review_cli][ERROR] {len(bad)} 行缺必填字段(code/name/limit_count/first_hhmm/seal_money/turnover/reason)，首例={bad[0].get('code')}")
        return 2
    for r in rows:  # industry 允许空(9/8 实锚: 688293 行空行业=复盘真实状态), 缺省未分类
        if not r.get("industry"):
            r["industry"] = "未分类"
    dist = {}
    for row in rows:  # 分类全量预跑（dry-run 与实写同一代码路径）
        _, _, _, pt, _, _ = classify(row, themes)
        dist[pt] = dist.get(pt, 0) + 1
    if a.dry_run:
        print(f"[review_cli] dry-run {date}: {len(rows)}行 批次将={batch} 分布={sorted(dist.items(), key=lambda kv: -kv[1])}")
        return 0
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    con = sqlite3.connect(str(ATTR_DB), timeout=15)
    con.execute("PRAGMA busy_timeout=15000")
    try:
        pre = con.execute("SELECT COUNT(*) FROM limit_up_patterns WHERE trade_date=? AND created_at=?",
                          (date, batch)).fetchone()[0]
        con.execute("DELETE FROM limit_up_patterns WHERE trade_date=? AND created_at=?", (date, batch))
        n = 0
        for row in rows:
            theme, main, sub, pt, score, sv = classify(row, themes)
            code = str(row["code"]).zfill(6)
            hhmm = str(row["first_hhmm"])
            hh, mm = hhmm.split(":")
            seal, to = float(row["seal_money"]), float(row["turnover"])
            ratio = round(seal / to, 3) if to else None
            seal_status = f"首封{hhmm} 封板{seal/1e8:.2f}亿"  # 诚实边界: 不写炸板数(未采集)
            core = f"{row['reason']}｜{theme}｜封板/成交比{ratio}" if ratio is not None else f"{row['reason']}｜{theme}"
            tags = json.dumps({"main": main[:2], "sub": sub[:2], "main_zh": main, "sub_zh": sub,
                               "industry": row["industry"], "theme": theme, "seal_amt_ratio": ratio,
                               "limit_count": int(row["limit_count"]),
                               "afternoon_board": hhmm >= "13:00", "source": batch}, ensure_ascii=False)
            con.execute("""insert into limit_up_patterns(trade_date,code,name,industry,board,limit_count,seal_status,
                           first_limit_time,turnover,primary_attribution,secondary_attribution,core_driver,
                           predictability_score,feature_tags,pattern_type,info_available_pre_open,strategy_value,created_at)
                           values(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (date, code, row["name"], row["industry"], board_of(code), int(row["limit_count"]),
                         seal_status, f"{hh}{mm}00", to, main, sub, core, score, tags, pt, 0, sv, batch))
            n += 1
        con.commit()
        post = con.execute("SELECT COUNT(*) FROM limit_up_patterns WHERE trade_date=? AND created_at=?",
                           (date, batch)).fetchone()[0]
        dist = con.execute("SELECT pattern_type, COUNT(*) FROM limit_up_patterns WHERE trade_date=? AND created_at=? "
                           "GROUP BY pattern_type", (date, batch)).fetchall()
        eng = con.execute("SELECT COUNT(*) FROM limit_up_patterns WHERE trade_date=? AND pattern_type='engine_v2'",
                          (date,)).fetchone()[0]
        total = con.execute("SELECT COUNT(*) FROM limit_up_patterns WHERE trade_date=?", (date,)).fetchone()[0]
    finally:
        con.close()
    print(f"[review_cli] append {date}: 批次{batch} 删旧{pre} 插入{n} 验收{post} | engine_v2行(未触碰)={eng} 当日总行={total}")
    print(f"[review_cli] pattern_type分布: {dist}")
    assert post == n, f"验收异常 post={post} != n={n}"
    return 0


def cmd_check(a):
    con = sqlite3.connect(f"file:{ATTR_DB}?mode=ro", uri=True)
    try:
        mx = con.execute("SELECT MAX(trade_date) FROM limit_up_patterns").fetchone()[0]
        # 双口径分列（2026-09-09 补: 引擎行日更后全表 MAX 被引擎日占位, 复盘口径滞后
        # 需单列。复盘口径行=复盘会话人工主题标注样本(散点, 非日更合同); 其唯一消费方
        # 「白名单主线标注」已随 2026-09-24 名单制废除退役——本口径仅存研究样本观测价值)
        rv = con.execute("SELECT MAX(trade_date) FROM limit_up_patterns "
                         "WHERE pattern_type != 'engine_v2'").fetchone()[0]
        dist = con.execute("SELECT trade_date, pattern_type, COUNT(*) FROM limit_up_patterns "
                           "WHERE trade_date>=? GROUP BY trade_date, pattern_type ORDER BY trade_date DESC LIMIT 20",
                           (int(mx or 0) - 300,)).fetchall()
    finally:
        con.close()
    qmx = None
    if QFQ_DB.exists() and QFQ_DB.stat().st_size:
        con = sqlite3.connect(f"file:{QFQ_DB}?mode=ro", uri=True)
        qmx = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
        con.close()
    lag = rv_lag = None
    try:  # 日历差用 strptime（YYYYMMDD 整数差 //10000 同月内失真, 9/9 实锚: 差1显示0）
        if mx and qmx:
            lag = (datetime.datetime.strptime(str(qmx), "%Y%m%d")
                   - datetime.datetime.strptime(str(mx), "%Y%m%d")).days
        if rv and qmx:
            rv_lag = (datetime.datetime.strptime(str(qmx), "%Y%m%d")
                      - datetime.datetime.strptime(str(rv), "%Y%m%d")).days
    except ValueError:
        pass
    print(f"[review_cli] check: 表MAX={mx} | qfq MAX={qmx} | 表滞后={lag}天 | "
          f"复盘口径MAX={rv} 滞后={rv_lag}天")
    if rv_lag is not None and rv_lag >= 3:
        print("[review_cli] INFO 复盘口径样本止于%s(滞后%d天)=人工复盘标注停更; "
              "唯一消费方白名单主线标注已随 2026-09-24 名单制废除退役, 无功能降级"
              % (rv, rv_lag))
    by_day = {}
    for d, pt, c in dist:
        by_day.setdefault(d, {})[pt] = c
    for d in sorted(by_day, reverse=True):
        eng = by_day[d].get("engine_v2", 0)
        rev = sum(v for k, v in by_day[d].items() if k != "engine_v2")
        print(f"[review_cli]   {d}: engine_v2={eng} 复盘口径={rev}")
    return 0


def main():
    for _s in (sys.stdout, sys.stderr):
        if hasattr(_s, "reconfigure"):
            _s.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="每日复盘涨停范式入库 CLI（人工判断真源,本 CLI=写入通道）")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("append", help="复盘批次入库（幂等,只删本批次行）")
    p.add_argument("--date", required=True)
    p.add_argument("--rows", required=True, help="rows.json 路径")
    p.add_argument("--dry-run", action="store_true", help="只跑分类不落库（回放验证）")
    p.set_defaults(func=cmd_append)
    p = sub.add_parser("check", help="覆盖/口径分布/滞后体检")
    p.set_defaults(func=cmd_check)
    a = ap.parse_args()
    sys.exit(a.func(a))


if __name__ == "__main__":
    main()
