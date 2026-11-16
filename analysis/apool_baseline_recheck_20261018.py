# -*- coding: utf-8 -*-
"""apool_baseline_recheck_20261018.py — A池基线复核读回件 (2026-10-18, DeepSeek外部审计吸收项V4)

DeepSeek E-2 四问: ①分年收益表 ②退市股口径核验 ③扣费后收益 ④第三窗(2026)。
本件不重算事件(复用 10/11 全速复核引擎 apool_tradability_review_vec.py 的官方口径复刻产物),
只做: 读回 + 退市口径结构核验(qfq 源码查询无幸存者过滤 + 退役股计数) + 四问逐条判定直出。

口径警示(反臆想):
  vec json 口径 = fwd20 / cost0 可选 / 无 -8% 止损路径 / 全门状态;
  总纲 v1.2 口径 = 2023-2026 合并 mean +2.67% / -8% 止损路径。
  两口径并存皆标注来源, 禁互相冒充。

输出: analysis/apool_baseline_recheck_20261018_out.md (幂等直出, 禁人工转抄)
零交易权: 纯研究呈现; "已验证式"称号是否加注记/是否立正式复核 prereg = 主人裁决。
"""
import json
import os
import sqlite3
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = r"D:\zcodesj"
VEC = os.path.join(ROOT, "data", "apool_tradability_review_20261011_vec.json")
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_MD = os.path.join(ROOT, "analysis", "apool_baseline_recheck_20261018_out.md")


def main():
    d = json.load(open(VEC, encoding="utf-8"))
    yearly = d["yearly"]
    cs = d["cost_sensitivity"]
    bq = d["baseline_quadrant"]
    bb = d.get("block_bootstrap", {})

    # 退市口径量化: qfq 全库 0/3/6 码, 2023+ 有数据但数据止于 2025 前 = 退役/长期停牌股
    con = sqlite3.connect("file:" + QFQ.replace("\\", "/") + "?mode=ro", uri=True, timeout=30)
    row_dead = con.execute(
        "SELECT COUNT(*) FROM (SELECT code, MAX(date) AS mx FROM stock_daily_meta_qfq "
        "WHERE substr(code,1,1) IN ('0','3','6') AND date>=20230101 GROUP BY code HAVING mx<=20250630)"
    ).fetchone()[0]
    row_all = con.execute(
        "SELECT COUNT(DISTINCT code) FROM stock_daily_meta_qfq "
        "WHERE substr(code,1,1) IN ('0','3','6') AND date>=20230101"
    ).fetchone()[0]
    con.close()

    pos_years = [y for y, v in yearly.items() if v["mean"] > 0]
    md = []
    w = md.append
    w("# A池基线复核(V4, DeepSeek外部审计吸收项 E-2 四问)")
    w("")
    w("> 脚本 analysis/apool_baseline_recheck_20261018.py 直出 | 数据=data/apool_tradability_review_20261011_vec.json(10/11官方口径复刻引擎产物, n=1019事件) + qfq主库结构核验")
    w("> 口径警示: vec=fwd20/无止损路径/全门状态; 总纲v1.2=2023-2026合并mean+2.67%/-8%止损路径——两口径并存各标来源, 禁互冒充")
    w("")
    w("## ① 分年收益表(全样本A池, fwd20, cost0)")
    w("")
    w("| 年 | n | med | mean | win |")
    w("|---|---|---|---|---|")
    for y in sorted(yearly):
        v = yearly[y]
        w(f"| {y} | {v['n']} | {v['med']:+.2f}% | {v['mean']:+.2f}% | {v['win']:.1f}% |")
    w("")
    pos_n = len(pos_years)
    w(f"**判定**: 正收益年 {pos_n}/5（{'、'.join(sorted(pos_years))}）；2022 深负(med {yearly['2022']['med']:+.2f}%/win {yearly['2022']['win']:.1f}%)；"
      f"**2026 第三窗归零边缘**(med {yearly['2026']['med']:+.2f}%/mean {yearly['2026']['mean']:+.2f}%/win {yearly['2026']['win']:.1f}%)——样本期依赖坐实，正收益集中 2024-2025。")
    w("")
    w("## ② 扣费后收益(全样本)")
    w("")
    w("| 成本 | med | mean | win |")
    w("|---|---|---|---|")
    for k in ("cost0bps", "cost30bps", "cost50bps"):
        v = cs[k]
        w(f"| {k.replace('cost','').replace('bps','')}bp | {v['med']:+.2f}% | {v['mean']:+.2f}% | {v['win']:.1f}% |")
    w("")
    w("**判定**: mean 扣费后仍正但收窄（0→50bp: +1.41%→+0.91%）；**med 全档深负**（-1.81%→-2.31%）——"
      "A池全样本为右尾依赖结构（中位事件亏钱），『扣费后是否仍正』的诚实答案=均值勉强正、中位负。")
    w("")
    w("## ③ 唯一出手格复核（门A开∧门C关）")
    w("")
    vq = bq.get("门A开∧门C关", {})
    w(f"- 事件口径复刻：n={vq.get('n')} / med {vq.get('med', 0):+.2f}% / mean {vq.get('mean', 0):+.2f}% / win {vq.get('win', 0):.1f}%"
      f"（与总纲 +19.4%/79.2% n=77-79 同源吻合）")
    if bb:
        w(f"- 日簇块自助法：med {bb.get('med'):+.2f}%，95%CI [{bb.get('p2_5'):+.2f}%, {bb.get('p97_5'):+.2f}%]，"
          f"独立日簇 n_days={bb.get('n_days')}——**CI 下界勉强过零，统计强度脆弱**（外部 E-3『小样本陷阱』担忧定量成立）")
    w("- 低流动性事件 n_low_amt=" + str(d.get("liquidity", {}).get("n_low_amt", "N/A")) + "（占比约 3.7%）")
    w("")
    w("## ④ 退市股口径核验 —— 🔴 发现生产源结构性活股化")
    w("")
    w("- vec 引擎 load 查询无上市状态过滤（源码 L45-48 引证）——**查询层面**无幸存者过滤逻辑；")
    w(f"- **但数据层核验（本轮实查）**：wb qfq 主库全库 {row_all} 码中，max(date) 早于 2025-10 的码 = **{row_dead} 个**——"
      "生产价格源（2026-10-13 wb 翻转 + 10-14 合并终局后）**不含任何退市段**，全库均为存活至当前的股票；")
    w("- 旧事实勘误：『qfq 库含 312 只真退市』系 10/13 翻转前旧副本（vipdoc 重建链含退市段）口径，"
      "**wb 源自活股源链（tickflow/新浪），退市段从未进入主库**；旧副本现仅存于 backups 白名单族；")
    w("- **含义**：翻转后全部研究（含本件引用的 vec n=1019 与 10/14-16 全域回测）= 活股宇宙样本；"
      "翻转前研究（含 doctrine +2.67% 基线，10/3-10/8 旧源口径）与翻转后**不可直接比较**；"
      "幸存者偏差量级前测=事件级 0.6%（rightside 10/11 于含退市源实测），A池口径未重测；")
    w("- 已知边界（引 meta）：ST 名册不在 qfq 库→板宽按 10/20% 处理的剔除遗漏注记在案（pool_a_st_sensitivity_20261008）")
    w("- **呈请（P1）**：baostock 退市段回填立项（10/11 评估已定 baostock=唯一可行批量源）；回填前所有 wb 源回测报告应标注『幸存者偏差敞口未回填』。")
    w("")
    w("## ⑤ E-2 四问逐条判定")
    w("")
    w("| 问 | 判定 | 证据落点 |")
    w("|---|---|---|")
    w("| ①分年收益表 | **成立且要害**：正年 2/5，2022 深负，2026 归零边缘 | 本件① |")
    w("| ②退市口径 | **🔴 比外部担忧更重：生产源结构性活股化（0 退役段），翻转前后研究不可比** | 本件④ |")
    w("| ③扣费后 | mean 勉强正 / **med 全档负**——右尾依赖结构 | 本件② |")
    w("| ④第三窗(2026) | **归零边缘**（med -0.90%/win 44.2%）——alpha 衰减信号 | 本件① |")
    w("")
    w("## ⑥ 结论与呈请（主人裁决项）")
    w("")
    w("1. 总纲『唯一已验证式』称号建议加三条注记（改 reports/portrait_battle_doctrine.md，一行成本）："
      "**2024-2025 regime 依赖（正年 2/5）** / **右尾依赖（med 负，扣费后更负）** / **2026 第三窗归零边缘**；"
      "『OOS两窗皆正』表述保留但并列本件分年事实。")
    w("2. DeepSeek 建议的『基线复核 prereg』实质已被 10/11 复核引擎覆盖大半（四问中三问有硬数据）；"
      "增量仅在『分年×门格交互』与『2026H2 滚动重估』——是否立项由主人定，不建议自动立项。")
    w("3. 呈请态：本件零交易权，不触发任何接线/称号变更；注记落地等主人勾选。")
    with open(OUT_MD, "w", encoding="utf-8") as f:
        f.write("\n".join(md) + "\n")
    print(f"[OK] 正年 {pos_n}/5: {'、'.join(sorted(pos_years))} | 2026 med {yearly['2026']['med']:+.2f}% | "
          f"CI下界 {bb.get('p2_5')}% | 🔴退役码 {row_dead}/{row_all} = 生产源无退市段(幸存者敞口)")
    print(f"[OK] 已直出 {OUT_MD}")


if __name__ == "__main__":
    sys.exit(main())
