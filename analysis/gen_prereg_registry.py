# -*- coding: utf-8 -*-
"""prereg 登记索引生成器 (2026-10-12 净增量项6, 主人「继续」令)
=====================================================================
目的: 在役 prereg 一张表 —— 每个影子/预注册的 id、指纹(前16)、状态、判据
文件、台账路径、调度载体。此前分散在 data/PREREG_*.json + 记忆 + SOP, 查询
成本高; 本件只读汇总直出索引, **不是**自动拦截器(V2 审计已驳回机械 matching)。

纪律: 只读扫描, 禁改任何 PREREG 文件; 台账行数直出并读回核验。

用法: python analysis/gen_prereg_registry.py
输出: reports/prereg_registry_latest.md
"""
import datetime
import glob
import hashlib
import json
import os
import re
import sys

BASE = r"D:\zcodesj"
DATA = os.path.join(BASE, "data")
OUT = os.path.join(BASE, "reports", "prereg_registry_latest.md")

# 在役调度载体注记(直出手册台账, 载体名禁止凭空编造——由 CronList/schtasks 实况维护)
KNOWN_CARRIERS = {
    "PREREG_TWOGATE_ZONE_20260930": "cron 08:10 九步机(universe_zone_scanner)",
    "PREREG_TWOGATE_V2_20261001": "cron 08:10 九步机(twogate_shadow_engine_v2)",
    "PREREG_Z2S_SHADOW_20260929": "cron 07:45 观测层十合一(z2s_shadow_track)",
    "PREREG_VOLSPIKE_TOP_WATCH": "schtasks/sell-watch 链",
    "PREREG_QIHANG_SELL_WATCH": "sell-watch 链(strong_sell_watch)",
    "PREREG_SENTIMENT_FACTOR_P3": "cron 21:30 新闻PIT存档(仅存储)",
    "PREREG_PORTRAIT_Z2S_20261002": "一次性 2027-02-01 读出检查点",
    "PREREG_VOLTARGET_SHADOW_20261002": "一次性 2027-02-01 读出检查点",
    "PREREG_CONCEPT_STAGE": "cron 07:45(concept_stage_daily)",
    "PREREG_TIMING_STATE": "cron 09:25 晨报(timing_state_daily)",
    "PREREG_NEWS_SENTIMENT_SHADOW": "cron 15:35 候选癸(news_sentiment_ledger)",
    "PREREG_E10_TURNOVER_SIZE_20261007": "cron 07:00 E10 影子",
    "PREREG_EMOTION_PHASE_POSITION": "cron 09:25 晨报第7步(叙述轴)",
    "PREREG_FTD_FOLLOWTHROUGH_20261007": "cron 09:25 晨报第10步",
    "PREREG_SC_SPRING_DEEP_LOUD_20261007": "cron 09:25 晨报第12步",
    "PREREG_CZ1_FUND_STRUCTURE_STATE_SHADOW_20261008": "cron 09:25 晨报第13步+daily_chain[cz1]",
    "PREREG_DIPFLUSH_BREADTH_BASKET_20261011": "cron 09:25 晨报第14步",
    "PREREG_DEEPFIRST_SHADOW_20261004": "未接线(PD-031 呈请态)",
    "PREREG_M3_TRAIL_20260902": "schtasks TrailingMonitor(ml腿)",
    "PREREG_TOP5_VALIDATION": "一次性 automation 2026-10-12 09:27",
}


def fp16(path):
    try:
        h = hashlib.sha256(open(path, "rb").read()).hexdigest()
        return h[:16]
    except OSError:
        return "read-fail"


def ledger_rows(ledgers):
    """从 prereg 内容里发现的台账路径统计行数(存在才统计, 禁造数)。"""
    out = {}
    for p in ledgers:
        if not p:
            continue
        full = p if os.path.isabs(p) else os.path.join(BASE, p)
        if os.path.exists(full):
            try:
                with open(full, encoding="utf-8", errors="ignore") as f:
                    out[os.path.basename(p)] = sum(1 for _ in f)
            except OSError:
                out[os.path.basename(p)] = -1
    return out


def main():
    files = sorted(glob.glob(os.path.join(DATA, "PREREG_*.json")))
    rows = []
    for fp in files:
        name = os.path.basename(fp)[:-5]
        try:
            d = json.load(open(fp, encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            rows.append((name, fp16(fp), "PARSE-FAIL(%r)" % e, "", ""))
            continue
        status = d.get("status") or d.get("state") or "-"
        if not isinstance(status, str):
            status = json.dumps(status, ensure_ascii=False)[:40]
        ledgers = []
        blob = json.dumps(d, ensure_ascii=False)
        ledgers += re.findall(r'data/[\w/]*ledger[\w]*\.jsonl', blob)
        ledgers += re.findall(r'data/[\w]*\.jsonl', blob)
        lr = ledger_rows(sorted(set(ledgers)))
        ledger_txt = "; ".join("%s=%s行" % (k, v) for k, v in lr.items()) if lr else "-"
        carrier = next((v for k, v in KNOWN_CARRIERS.items() if name.startswith(k)
                        or k in name), "-")
        rows.append((name, fp16(fp), status, carrier, ledger_txt))

    lines = [
        "# 在役 prereg 登记索引 (asof %s)" % datetime.date.today().isoformat(),
        "",
        "> 生成: analysis/gen_prereg_registry.py 只读直出, 共 **%d** 个 PREREG 文件。" % len(rows),
        "> 本件是登记视图非拦截器; 判据真源 = 各 PREREG json 原文(指纹列=文件 sha256 前 16 位,",
        "> 与 cron prompt/schtasks 任务内注记的指纹比对可发现篡改)。载体列来自调度台账注记,",
        "> 冲突时以 CronList/schtasks 实况为准。",
        "",
        "| prereg | 指纹(sha256:16) | status | 调度载体 | 台账行数 |",
        "|---|---|---|---|---|",
    ]
    for name, fp, status, carrier, ledger in rows:
        lines.append("| %s | %s | %s | %s | %s |" % (name, fp, status, carrier, ledger))
    lines += [
        "",
        "## 纪律注记",
        "",
        "- 指纹不一致 = prereg 被篡改异常: 按 cron prompt 同款处置(告警+禁止继续)。",
        "- 影子期读数只记录不行动; 判读权在主人(各 cron prompt 明文)。",
        "- 本索引不含 falsified_rules(99条证伪)——那在 data/falsified_rules.json。",
    ]
    with open(OUT, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    with open(OUT, encoding="utf-8") as f:
        back = f.read()
    # Windows glob 大小写不敏感: PREREG_*(正式指纹件)与 prereg_*(历史登记件)都会命中,
    # 读回核验按通用表行计数(≥登记数), 大小写如实保留
    n = back.count("\n| ")
    assert n >= len(rows), "读回核验失败: 表行 %d < %d" % (n, len(rows))
    n_upper = sum(1 for r in rows if os.path.basename(r[0]).startswith("PREREG_"))
    print("[prereg-registry] 直出 %d 件(大写指纹件 %d + 小写登记件 %d) -> %s (读回 PASS)" % (
        len(rows), n_upper, len(rows) - n_upper, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
