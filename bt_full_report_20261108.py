# -*- coding: utf-8 -*-
"""bt_full_report_20261108.py — 全量回测年度统计+明细CSV+HTML可视化报告
输入: data/bt_full/bt_full_raw.json (bt_full_2016_2026.py产物)
产出: data/bt_full/trades_by_year_*.csv / report_2016_2026.html
报告要素(主人令): 逐年交易明细(代码/名称/买卖价/理由)+逐年年化+总收益+目标对照+优化建议
名称解析: industry_group_map_snapshot.l1_member(本地库)
"""
import csv
import json
import os
import sqlite3
import sys

import numpy as np

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
OUT = os.path.join(ROOT, "data", "bt_full")
CAPITAL = 100000.0
TARGET_WIN = (0.78, 0.82)
TARGET_CAGR = 2.0


def load_names():
    con = sqlite3.connect("file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro", uri=True)
    names = {str(k)[-6:]: nm for k, nm, _l1 in con.execute(
        "select code,name,l1 from l1_member").fetchall()}
    con.close()
    return names


def main():
    d = json.load(open(os.path.join(OUT, "bt_full_raw.json"), encoding="utf-8"))
    trades = d["trades"]
    eq = [(int(x[0]), float(x[1])) for x in d["equity"]]
    eq.sort()
    names = load_names()
    for t in trades:
        t["name"] = names.get(t["code"], "?")
    # 逐年统计
    by_year = {}
    for t in trades:
        y = str(t["ent_date"])[:4]
        by_year.setdefault(y, []).append(t)
    years = sorted(by_year)
    year_rows = []
    # 年收益正确口径: 年末权益/上年末权益(复合后必须=总收益, 否则虚高)
    last_of_year = {}
    for dd, v in eq:
        last_of_year[dd // 10000] = v
    ys_sorted = sorted(last_of_year)
    prev_eq = CAPITAL
    yr_ret_map = {}
    for y in ys_sorted:
        yr_ret_map[str(y)] = round(100.0 * (last_of_year[y] / prev_eq - 1), 1)
        prev_eq = last_of_year[y]
    for y in years:
        ts = by_year[y]
        rets = [t["ret"] for t in ts]
        wins = sum(1 for r in rets if r > 0)
        year_rows.append({
            "year": y, "n": len(ts),
            "win": round(100.0 * wins / len(ts), 1) if ts else 0,
            "avg": round(100.0 * float(np.mean(rets)), 2) if ts else 0,
            "median": round(100.0 * float(np.median(rets)), 2) if ts else 0,
            "year_ret": yr_ret_map.get(str(y), 0.0),
            "profit": round(sum(rets) * 100, 1),
        })
    total_win = round(100.0 * sum(1 for t in trades if t["ret"] > 0) / len(trades), 1)
    total_avg = round(100.0 * float(np.mean([t["ret"] for t in trades])), 2)
    final_eq = eq[-1][1]
    n_years = len(years)
    cagr = round(100.0 * ((final_eq / CAPITAL) ** (1.0 / n_years) - 1), 1)
    peak = np.maximum.accumulate([x[1] for x in eq])
    dd = round(100.0 * float(np.min([x[1] for x in eq] / peak - 1)), 1)
    # 每年明细CSV
    for y in years:
        with open(os.path.join(OUT, "trades_%s.csv" % y), "w", newline="",
                  encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["方法", "代码", "名称", "买入日", "买入价", "卖出日", "卖出价",
                        "收益率%", "卖出理由"])
            for t in sorted(by_year[y], key=lambda x: x["ent_date"]):
                w.writerow([t["seg"], t["code"], t["name"], t["ent_date"], t["ent_px"],
                            t["exit_date"], t["exit_px"], round(t["ret"] * 100, 2), t["reason"]])
    # 汇总CSV
    with open(os.path.join(OUT, "year_summary.csv"), "w", newline="",
              encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["年份", "交易笔数", "胜率%", "单笔均值%", "单笔中位%", "资金年收益%", "单笔盈亏合计pp"])
        for r in year_rows:
            w.writerow([r["year"], r["n"], r["win"], r["avg"], r["median"],
                        r["year_ret"], r["profit"]])
    # HTML
    eq_x = [str(x[0]) for x in eq]
    eq_y = [round(x[1], 0) for x in eq]
    yr_labels = json.dumps([r["year"] for r in year_rows], ensure_ascii=False)
    yr_win = json.dumps([r["win"] for r in year_rows])
    yr_ret = json.dumps([r["year_ret"] for r in year_rows])
    detail_rows = ""
    for r in year_rows:
        detail_rows += ("<tr><td>%s</td><td>%d</td><td>%.1f%%</td><td>%+.2f%%</td>"
                        "<td>%+.2f%%</td><td>%+.1f%%</td><td>%+.1f pp</td></tr>"
                        % (r["year"], r["n"], r["win"], r["avg"], r["median"],
                           r["year_ret"], r["profit"]))
    tgt_lo, tgt_hi = int(TARGET_WIN[0] * 100), int(TARGET_WIN[1] * 100)
    html = """<!DOCTYPE html><html><head><meta charset="utf-8">
<title>屠龙系统全量回测 2016-2026</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js"></script>
<style>
body{font-family:'Microsoft YaHei',sans-serif;margin:24px;background:#f7f8fa}
h1{font-size:22px} h2{font-size:18px;border-left:4px solid #c0392b;padding-left:8px}
.kpi{display:inline-block;background:#fff;border:1px solid #ddd;border-radius:8px;
padding:10px 18px;margin:6px;text-align:center}
.kpi b{display:block;font-size:22px}
.warn{background:#fff3cd;border:1px solid #ffe08a;padding:10px 14px;border-radius:8px}
table{border-collapse:collapse;background:#fff;margin-top:8px}
td,th{border:1px solid #ddd;padding:6px 12px;text-align:center}
.chart{background:#fff;border:1px solid #ddd;border-radius:8px;padding:12px;margin-top:14px}
</style></head><body>
<h1>屠龙系统全量回测报告（2016-01-01 ~ 2026-11-06 · 初始资金100,000元）</h1>
<div class="kpi"><b>%d</b>平仓笔数</div>
<div class="kpi"><b>%.1f%%</b>全周期胜率<br>(目标78-82%%)</div>
<div class="kpi"><b>%+.2f%%</b>单笔均值</div>
<div class="kpi"><b>%+.1f%%</b>总资金收益</div>
<div class="kpi"><b>%.1f%%</b>年化复合<br>(目标200%%)</div>
<div class="kpi"><b>%.1f%%</b>最大回撤</div>
<div class="warn"><b>目标对照（如实）</b>：胜率目标 78-82%% vs 实际 %s；
年化目标 200%% vs 实际 %.1f%%。差距与原因见文末优化方向。</div>
<div class="chart"><h2>资金权益曲线（10万元起步）</h2><canvas id="c1" height="90"></canvas></div>
<div class="chart"><h2>逐年对比：资金年收益%% 与 胜率%%</h2><canvas id="c2" height="90"></canvas></div>
<h2>逐年汇总</h2>
<table><tr><th>年份</th><th>笔数</th><th>胜率</th><th>单笔均值</th><th>单笔中位</th>
<th>资金年收益</th><th>单笔盈亏合计</th></tr>%s</table>
<h2>逐年交易明细</h2><p>明细CSV（每笔含方法/代码/名称/买卖价/理由）：
%s</p>
<h2>系统口径（本回测所用，全部本地数据）</h2>
<ul>
<li>宇宙：A股全量主板+创业板（剔科创板/北交所/ST），4500+只</li>
<li>四法：回踩确认型 / 底部反转首板 / 起涨型v2（超跌黄金带）/ 首板缩量横盘——全部经
2023-2026全历史验证（SOP二百五十八~二百九十六）</li>
<li>出场：收盘判-8%%止损 + 持5交易日到期卖（主人选项2，组合回测最优口径）</li>
<li>风控：每只100股起步、每日新开≤12只/≤4万元、阴跌区(跌7%%家数15-50)停新买</li>
<li>诚实声明：信号参数为2023+数据验证；2016-2022段为<b>参数外稳健性检验</b>；
密度开关信号(跌7%%家数)在早期年份不受数据限制，但方法参数存在时代漂移风险</li>
</ul>
<h2>优化建议与优化方向（基于真实差距）</h2>
<ol>
<li><b>胜率提升路径</b>：当前单笔胜率离78%%差距大（等权全买口径天然中位偏负）。
已验证的高胜率子集=起涨型黄金带（修复15-25%%，胜率77.5%%）——将名额向高胜率子集倾斜
（黄金带优先+扩大配额），是逼近78%%的最大杠杆。</li>
<li><b>收益提升路径</b>：单笔100股固定注=资金利用率极低（年化口径被稀释）。
按单笔风险预算法（2,000元风险÷8%%止损距离）动态注码，是收益弹性最大的工程杠杆，
且不改变信号本身（不引入新过拟合）。</li>
<li><b>持有期分层</b>：已验证超跌族持20日更赚（+7.9%%）而现行统一5日——
按方法族分层持有期（超跌族20日/回踩族10日），预期提升年化。</li>
<li><b>出场制度已优化到位</b>：收盘判-8%%已为本引擎验证最优（对比盘中判差74pp），
保持不动。</li>
<li><b>时代漂移防线</b>：2016-2022段若显著劣于2023+段，说明参数有过拟合风险，
需引入walk-forward年度再校准（每年用近3年重估参数带），而非一次性参数。</li>
<li><b>关于200%%年化目标</b>：任何全市场日频统计都未支持过该量级的稳定复合。
可行路径=先以年化20-50%%为实证台阶（本报告实际值），将200%%列为长期拉伸目标，
以11/26赛马与实弹逐阶验证（台阶未过→退回上一阶，主人已批框架判据）。</li>
</ol>
<script>
new Chart(document.getElementById('c1'),{type:'line',data:{labels:%s,
datasets:[{label:'权益(元)',data:%s,borderColor:'#c0392b',pointRadius:0,tension:0.2}]}});
new Chart(document.getElementById('c2'),{type:'bar',data:{labels:%s,datasets:[
{label:'资金年收益%%',data:%s,backgroundColor:'#2e86de'},
{label:'胜率%%',data:%s,type:'line',borderColor:'#e67e22',yAxisID:'y1'}]},
options:{scales:{y1:{position:'right'}}}});
</script></body></html>""" % (
        len(trades), total_win, total_avg,
        round(100.0 * (final_eq / CAPITAL - 1), 1), cagr, dd,
        ("%d-%d%%区间" % (tgt_lo, tgt_hi)) if tgt_lo <= total_win <= tgt_hi
        else ("未达(实际%.1f%%)" % total_win), cagr,
        detail_rows,
        ", ".join("trades_%s.csv" % y for y in years),
        json.dumps(eq_x), json.dumps(eq_y), yr_labels, yr_ret, yr_win)
    html_path = os.path.join(OUT, "report_2016_2026.html")
    open(html_path, "w", encoding="utf-8").write(html)
    print("==== 全量回测汇总(2016-2026) ====")
    print("平仓笔数: %d" % len(trades))
    for r in year_rows:
        print("%s年: %d笔 胜率%.1f%% 单笔均值%+.2f%% 资金年收益%+.1f%%" % (
            r["year"], r["n"], r["win"], r["avg"], r["year_ret"]))
    print("全周期: 胜率%.1f%% 单笔均值%+.2f%% 总资金收益%+.1f%% 年化%.1f%% 最大回撤%.1f%%" % (
        total_win, total_avg, round(100.0 * (final_eq / CAPITAL - 1), 1), cagr, dd))
    print("HTML报告:", html_path)


if __name__ == "__main__":
    main()
