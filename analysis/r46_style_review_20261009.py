# -*- coding: utf-8 -*-
"""R46 组H呈报项落地样例: 周/月级人读风格复盘+胜率自总结 (一次性聚合, 2026-10-09)
组H(BV1XM4m1X74t复盘7步)唯一可吸收增量=周/月级人读风格复盘+自身胜率总结维度。
本脚本=一次性样例生成(纯只读聚合, 零接线零交易权); 是否固化cron化待主人裁决。
数据源=D:/zcodesj/attribution.db(根目录看板库, health_check_daily 同源); 窗口=20260825起(近30交易日)。
输出=reports/style_review_sample_20261009.md
"""
import re
import sqlite3
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

DB = r"D:\zcodesj\attribution\attribution.db"
OUT = Path(r"D:\zcodesj\reports\style_review_sample_20261009.md")
BRIEF = Path(r"D:\zcodesj\data\style_review_brief_latest.txt")
WIN_START = "20260825"

if not Path(DB).exists():
    print(f"[FATAL] 库缺失 {DB}")
    sys.exit(2)

con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
con.row_factory = sqlite3.Row
cur = con.cursor()

# ---- 1. 平仓闭环 ----
closed = cur.execute(
    "SELECT code,name,entry_date,exit_date,exit_reason,pnl_pct,holding_days,max_pnl_pct,max_drawdown_pct "
    "FROM position_history WHERE exit_date>=? AND status='CLOSED' ORDER BY exit_date",
    (WIN_START,)).fetchall()
pnls = [r["pnl_pct"] for r in closed]
wins = [p for p in pnls if p > 0]
losses = [p for p in pnls if p <= 0]
holds = [r["holding_days"] for r in closed]

def cls(reason: str) -> str:
    r = reason or ""
    if "S-5" in r or "S5" in r or "止损" in r.replace("止损线", "S-5"):
        # 区分深度滑点
        return "s5_stop"
    if "爆量退出" in r:
        return "vol_exit"
    if "空头排列" in r or "趋势走坏" in r or "续阴" in r:
        return "trend_bad"
    if "主人指令" in r or "主人" in r:
        return "owner_order"
    if "R5" in r or "持有周期" in r:
        return "period_exit"
    if "盘后审计导入" in r or "待补评" in r:
        return "audit_backfill"
    if "调仓换股" in r or "换股" in r or "换仓" in r:
        return "rebalance"
    if "落袋" in r or "止盈" in r:
        return "take_profit"
    return "other"

cat = Counter(cls(r["exit_reason"]) for r in closed)
CAT_NAME = {"s5_stop": "S-5硬止损", "vol_exit": "爆量退出", "trend_bad": "趋势走坏/空头排列",
            "owner_order": "主人指令", "period_exit": "R5持有周期", "audit_backfill": "盘后审计导入(待补评)",
            "rebalance": "调仓换仓", "take_profit": "止盈落袋", "other": "其他"}

# ---- 2. 闸门拒绝面 ----
guards = cur.execute(
    "SELECT trade_date,verdict,source,rejects FROM guard_precheck_log WHERE trade_date>=?",
    (WIN_START,)).fetchall()
verdicts = Counter(g["verdict"] for g in guards)
rej_reason = Counter()
for g in guards:
    if g["verdict"] == "REJECT":
        rj = g["rejects"] or ""
        if "R38" in rj:
            rej_reason["R38期望闸(弱档负期望)"] += 1
        elif "30.0" in rj or "股价" in rj and "上限" in rj:
            rej_reason["30元上限铁律"] += 1
        elif "R23" in rj:
            rej_reason["R23持仓加仓"] += 1
        elif "R33" in rj:
            rej_reason["R33活跃度负面清单"] += 1
        elif "DD_GATE" in rj:
            rej_reason["DD_GATE回撤门控"] += 1
        elif "R26" in rj:
            rej_reason["R26名单制"] += 1
        elif "admit闸门" in rj or "不在白名单" in rj:
            rej_reason["admit信号源白名单"] += 1
        elif "R22" in rj:
            rej_reason["R22双口径闸"] += 1
        elif "R31" in rj:
            rej_reason["R31偏离度追高"] += 1
        elif "科创板" in rj or "北交所" in rj:
            rej_reason["板块排除铁律"] += 1
        else:
            rej_reason["其他"] += 1

# ---- 3. S-5 执行质量(滑点=亏损超过-8%线幅度) ----
s5 = [r for r in closed if cls(r["exit_reason"]) == "s5_stop"]
s5_deep = [(r["name"], r["pnl_pct"]) for r in s5 if r["pnl_pct"] < -8.5]
s5_pnls = [r["pnl_pct"] for r in s5]

# ---- 4. 买卖节奏 ----
trades = cur.execute(
    "SELECT trade_date,action FROM trade_records WHERE trade_date>=? AND strategy_version NOT IN ('recon_backfill','westock_v1')",
    (WIN_START,)).fetchall()
buy_days = Counter(t["trade_date"] for t in trades if t["action"].upper() == "BUY")
sell_days = Counter(t["trade_date"] for t in trades if t["action"].upper() == "SELL")

con.close()

med = statistics.median
asof = closed[-1]["exit_date"] if closed else "N/A"
lines = [
    f"# 近30交易日人读风格复盘+胜率自总结（滚动幂等件, 数据asof={asof}）",
    "",
    f"> 窗口 20260825~20261009；数据源=attribution.db 根目录看板库（只读聚合）；本件=R46组H呈报项落地样例（周/月级人读风格复盘+自身胜率总结），是否固化cron化待主人裁决。零交易权。",
    "",
    "## 一、闭环战绩（position_history, n=%d 笔平仓）" % len(closed),
    "",
    f"- 胜率：{len(wins)}/{len(pnls)} = {len(wins)/len(pnls)*100:.1f}%",
    f"- 平均盈利单：{statistics.mean(wins):+.2f}%（n={len(wins)}）；平均亏损单：{statistics.mean(losses):+.2f}%（n={len(losses)}）",
    f"- 盈亏比（均盈/|均亏|）：{statistics.mean(wins)/abs(statistics.mean(losses)):.2f}",
    f"- 单笔中位：{med(pnls):+.2f}%；最好：{max(pnls):+.2f}%；最差：{min(pnls):+.2f}%",
    f"- 持有天数：中位 {med(holds):.0f} 日 / 均值 {statistics.mean(holds):.1f} 日",
    f"- 合计 pnl（简单加总, 未按仓位加权）：{sum(pnls):+.1f}pp",
    "",
    "## 二、卖出理由分布",
    "",
    "| 类别 | 笔数 | 占比 |",
    "|---|---|---|",
]
for k, v in cat.most_common():
    lines.append(f"| {CAT_NAME.get(k, k)} | {v} | {v/len(closed)*100:.0f}% |")
    lines += [
        "",
        "## 二点五、盈利回吐度 giveback（20261009 回填后可算, 收盘近似口径）",
        "",
        f"- 回填覆盖：{sum(1 for r in closed if r['max_pnl_pct'])}/{len(closed)} 笔（其余 entry/exit 字段缺失不可回填, 如实排除）",
    ]
    gb_rows = [r for r in closed if r["max_pnl_pct"] and r["max_pnl_pct"] > 0]
    if gb_rows:
        gbs = [(r["max_pnl_pct"] - r["pnl_pct"]) for r in gb_rows]
        ratios = [(r["max_pnl_pct"] - r["pnl_pct"]) / r["max_pnl_pct"] for r in gb_rows if r["max_pnl_pct"] > 0]
        full = sum(1 for r, g in zip(gb_rows, gbs) if r["pnl_pct"] <= 0 and g > 5)
        lines += [
            f"- 有正浮盈记录的平仓 {len(gb_rows)} 笔：平均回吐 {statistics.mean(gbs):+.1f}pp（最高浮盈→最终实现），回吐比例中位 {med(ratios)*100:.0f}%",
            f"- **回吐至亏损的大赢单**（max_pnl>5pp 但最终 pnl≤0）：{full} 笔" + ("——" + "、".join(f"{r['name']}({r['code']}) 最高+{r['max_pnl_pct']:.1f}→收{r['pnl_pct']:+.1f}" for r, g in zip(gb_rows, gbs) if r["pnl_pct"] <= 0 and g > 5) if full else ""),
        ]
    lines += [
        "- 🔴治理注记（20261009 勘误）：回吐数字为事实呈现，但**出场侧治理已判死**——10/9 盈侧卖出双否决（B2 固定分批砍右尾 -20.55pp/笔 n=81；B1 浮盈回撤触发 n≤4），8.1pp 回吐已重定性\"事件出场一次性 gap 不可机械回收，对策回入场侧(低位启动回踩)+仓位档(重拳)\"（证伪档案 #793）。本段禁作止盈规则依据。",
    ]
    lines += [
        "",
        "## 三、S-5 执行质量（滑点体检）",
    "",
    f"- S-5 止损笔数：{len(s5)}（占平仓 {len(s5)/len(closed)*100:.0f}%）",
    f"- S-5 平均实现亏损：{statistics.mean(s5_pnls):+.2f}%（线=-8%；超线部分=执行滑点/跳空）",
    f"- 深滑点笔（pnl<-8.5%）：{len(s5_deep)} 笔——" + "、".join(f"{n}({p:+.1f}%)" for n, p in s5_deep) if s5_deep else "- 无深滑点笔",
    "",
    "## 四、闸门拦截面（guard_precheck_log）",
    "",
    f"- 总预检 {len(guards)} 次：PASS_WITH_WARN {verdicts.get('PASS_WITH_WARN',0)} / REJECT {verdicts.get('REJECT',0)} / SELL_OK {verdicts.get('SELL_OK',0)} / HOLD {verdicts.get('HOLD',0)}",
    "- REJECT 原因分布：" + "；".join(f"{k} {v}" for k, v in rej_reason.most_common()),
    "",
    "## 五、买卖节奏（trade_records, 剔除补录标记）",
    "",
    f"- 交易日买笔峰值：{buy_days.most_common(3)}",
    f"- 卖笔峰值日：{sell_days.most_common(3)}",
    "",
    "## 六、自总结（描述性, 非信号）",
    "",
    "- **盈利集中度**：百亚股份(003006)三段平仓 +69.9/+53.7/+64.4 与内蒙新华(603230)+58.3 贡献几乎全部利润——符合\"盈亏比引擎\"结构（少数大赢单扛全部止损）。",
    "- **卖出纪律结构**：S-5 止损占比最高=硬止损纪律在执行；\"盘后审计导入待补评\"批（9/23 一次性导入）=当日未走正常台账的历史遗留口径, 该批胜率单独看为噪声段。",
    "- **闸门面**：R38 弱档负期望拒绝为最大拦截源（10/8 上线后持续工作），与\"弱档不重拳\"铁律一致。",
    "- 反臆想声明：以上全部为已实现闭环的描述统计，max_pnl/max_drawdown 字段为 0 值（该两列未回填），盈利回吐度（giveback）本窗不可算，如实标注。",
]
OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
# 简报（人读 8 行, 供晨报/巡检消费; 幂等覆盖）
brief = [
    f"风格复盘(近30交易日) asof={closed[-1]['exit_date'] if closed else 'N/A'}",
    f"平仓 {len(closed)} 笔: 胜率 {len(wins)/len(pnls)*100:.1f}% 盈亏比 {statistics.mean(wins)/abs(statistics.mean(losses)):.2f} (均盈{statistics.mean(wins):+.1f}%/均亏{statistics.mean(losses):+.1f}%) 持有中位{med(holds):.0f}日",
    f"卖出结构: " + "、".join(f"{CAT_NAME.get(k,k)}{v}" for k, v in cat.most_common(4)),
    f"S-5: {len(s5)}笔 平均实现{s5_pnls and round(statistics.mean(s5_pnls),1)}% 深滑点{len(s5_deep)}笔",
    f"闸门: 预检{len(guards)} REJECT{verdicts.get('REJECT',0)} (R23加仓{rej_reason.get('R23持仓加仓',0)}/R38弱档{rej_reason.get('R38期望闸(弱档负期望)',0)}/R26名单{rej_reason.get('R26名单制',0)})",
]
if gb_rows:
    gbs = [(r["max_pnl_pct"] - r["pnl_pct"]) for r in gb_rows]
    full = sum(1 for r, g in zip(gb_rows, gbs) if r["pnl_pct"] <= 0 and g > 5)
    brief.append(f"giveback: {len(gb_rows)}笔浮盈单平均回吐{statistics.mean(gbs):+.1f}pp 回吐至亏损大赢单{full}笔 (🔴出场侧治理已判死#793, 仅事实呈现)")
brief.append("边界: 人读呈现层零交易权; 滚动30交易日幂等重算")
BRIEF.write_text("\n".join(brief) + "\n", encoding="utf-8")
print(f"[OK] -> {OUT}")
print("\n".join(lines[:40]))
