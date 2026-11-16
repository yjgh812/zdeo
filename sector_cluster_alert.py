# -*- coding: utf-8 -*-
"""
sector_cluster_alert.py — 板块簇集中度提示器 (2026-09-18 立项, 胜率评估推荐的制度化落地件)
================================================================================================
实证依据:
  - 9/10 煤炭航运过闸批 7 笔同簇 T+5 胜率 0% (山西焦煤/冀中能源/华阳股份/招商南油/宁波远洋/兴通/中远海控)
  - 9/16 逐票归因: 跌因=板块退潮 60% + 换手≥80 占 47% (跌票画像=高换手追入+板块退潮)
语义: 当日买入尝试(含被拦截)按通达信行业聚簇, 同簇 n>=3 红色警报 / n=2 黄色观察。
定位 = 只读提示层 (照 pullback_alert/bp_ep_exec_brief 家族), 零交易权零闸门改动 (冻结期不动闸门语义)。
消费: 交互会话呈报主人时提示; 买入决策权仍在主人+executor 双层防线。

数据面 (全部只读):
  - attribution.db guard_precheck_log 当日行 (code/name/verdict)
  - dragon_quant.db stock_info.industry (通达信行业)
输出: reports/sector_cluster_alert_YYYYMMDD.md + toast(仅红色簇)
"""
import sys, os, sqlite3, subprocess
from collections import defaultdict
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.stderr.reconfigure(encoding="utf-8", errors="replace")

ROOT = r"D:\zcodesj"
ATT_DB = os.path.join(ROOT, "attribution", "attribution.db")
RAW_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
TODAY = datetime.now().strftime("%Y%m%d")
OUT_MD = os.path.join(ROOT, "reports", f"sector_cluster_alert_{TODAY}.md")


def main():
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    db = sqlite3.connect(f"file:{ATT_DB}?mode=ro", uri=True)
    rows = db.execute(
        "SELECT code, name, verdict FROM guard_precheck_log WHERE trade_date=? ORDER BY id",
        (TODAY,)).fetchall()
    db.close()
    # 语义修复(2026-09-18 实弹暴露): precheck 日志含卖出复核(SELL_OK)/持有复核(HOLD)——
    # 只统计买入尝试(PASS*/REJECT), 排除 SELL/HOLD 类 verdict, 否则持有复核会误报成簇
    rows = [r for r in rows
            if not any(k in (r[2] or "").upper() for k in ("SELL", "HOLD"))]
    ind = {}
    raw = sqlite3.connect(f"file:{RAW_DB}?mode=ro", uri=True)
    for c, i in raw.execute("SELECT code, industry FROM stock_info WHERE instrument_type='stock'"):
        ind[c] = i or "未知"
    raw.close()

    lines = [f"# 板块簇集中度提示 {TODAY}", "",
             f"- 当日买入尝试(precheck): {len(rows)} 笔 (含 REJECT=被闸门拦截的尝试, 簇信号同样有效)",
             "- 阈值: 同通达信行业 n>=3 红色(9/10 煤炭批 7 笔 0% 胜率实证) / n=2 黄色观察",
             "- 定位: 只读提示层, 决策权在主人+executor, 零闸门改动", ""]
    red_clusters = []
    if not rows:
        lines.append("## 今日无买入尝试记录")
    else:
        # 标的去重(同票多次 precheck 只计 1), 明细保留全部尝试
        by_ind = defaultdict(lambda: defaultdict(list))  # ind -> code -> [尝试]
        for code, name, verdict in rows:
            by_ind[ind.get(code, "未知")].setdefault(f"{code} {name}", []).append(verdict)
        lines.append("| 行业 | 标的数(尝试次数) | 标的 | 级别 |")
        lines.append("|---|---|---|---|")
        for ind_name, codes in sorted(by_ind.items(), key=lambda kv: -len(kv[1])):
            n = len(codes)
            items = [f"{c}({len(v)})" for c, v in codes.items()]
            lvl = "🔴 红" if n >= 3 else ("🟡 黄" if n == 2 else "🟢")
            if n >= 3:
                red_clusters.append(ind_name)
            lines.append(f"| {ind_name} | {n} | {'; '.join(items[:6])}{'…' if n>6 else ''} | {lvl} |")
        lines.append("")
        lines.append("## 呈报提示 (固定段)")
        lines.append("1. 红色簇=当日同行业已 3+ 笔买入尝试: 对照 9/10 煤炭批(0%胜率)与 9/16 归因(跌因=板块退潮 60%),")
        lines.append("   继续追加该簇前须主人显式确认板块未退潮 (22:45 行业量能 C1 状态可查);")
        lines.append("2. 黄色=2 笔观察, 无强制动作;")
        lines.append("3. 本提示不拦截不修改任何委托, 买入资格仍由 pre_trade R 闸实时判定;")
        lines.append("4. 生成: sector_cluster_alert.py @ schtasks SectorClusterAlert_1400, 只读零交易权.")
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print(f"[CLUSTER-ALERT] {OUT_MD} | 当日尝试{len(rows)}笔 红色簇{len(red_clusters)}个"
          + (f": {red_clusters}" if red_clusters else ""))
    if red_clusters:
        try:
            subprocess.run(["powershell", "-NoProfile", "-Command",
                            f"New-BurntToastNotification -Text '板块簇警报:{','.join(red_clusters)} 当日3+笔'"],
                           capture_output=True, timeout=15)
        except Exception:
            pass


if __name__ == "__main__":
    main()
