# -*- coding: utf-8 -*-
"""M-3 回退条款判读件 (2026-11-30 复核点前置, 只读零交易权)
=====================================================================
背景: config v1.4 回退条款 = 「2026-11-30 复评若增量转负则 trailing 回 0」
(PROJECT_BOUNDARY §3 ml_signal 腿·主人显式复活, 2026-11-30 复核点在册)。
本脚本为该复核点的判读件: 汇总三条证据线, 输出判读报告供主人裁决。

证据线:
  E1 trailing 监控史: reports/trailing_monitor_log.txt 逐次扫描/触发计数
     (在役监控器 trailing_monitor_ml.py, 参数指纹 a41fbe252a3af5b7641a)
  E2 模拟盘 forward 样本: tulong_production_paper.db trades/positions WHERE
     source='ml_signal' —— 实际成交与 trailing 触发的 realized 增量
  E3 回测延伸对照: 9/3 净值级 A/B 基线锚
     (tulong_ab_m3_trail_equity_20260903/CONCLUSION: trail10/hold20 +258.95%/
      MDD24.46% vs 基线 +220.56%/33.56%, +38.39pp); 复核日应以同法把数据
      延伸至最新复跑对照 —— 本脚本只登记锚与复跑命令, 不自动跑重回测

判读口径 (跑前冻结, 源=config v1.4 note 原文):
  增量转负 → 建议 trailing 回 0 (bp60/ml40 权重不动, 回退仅 trailing 参数)
  增量为正/样本不足 → 维持 + 列下一复核窗口
  诚实层: E2 样本 <10 笔时 forward 证据力不足, 判读降级为 E3 单线并如实标注

用法: python analysis/m3_trail_review.py [--asof YYYYMMDD]
输出: reports/m3_trail_review_<asof|today>.md; exit 0 正常 / 2 守卫失败
合规: 只读; 不触 LOCKED prereg; 不下单; 判读权在主人
"""
import argparse
import datetime
import json
import os
import re
import sqlite3
import sys

BASE = r"D:\zcodesj"
SHADOW_DB = os.path.join(BASE, "tulong_production_paper.db")
MON_LOG = os.path.join(BASE, "reports", "trailing_monitor_log.txt")
REPORT_DIR = os.path.join(BASE, "reports")
AB_ANCHOR = {
    "source": "reports/tulong_ab_m3_trail_equity_20260903/CONCLUSION.md (git 8a560ef)",
    "trail_arm": "+258.95% / MDD 24.46% (trail10/hold20)",
    "baseline_arm": "+220.56% / MDD 33.56% (hold20)",
    "delta": "+38.39pp / MDD 优 9.10pp",
    "rerun_cmd": "python tulong_ab_m3_trail_equity_20260903.py  # 复核日同法延伸数据复跑",
}


def guard_params():
    """参数单一真源守卫: M-3 登记不可得则拒绝判读(不猜参数)。"""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "pcm", os.path.join(BASE, "production_combo_monthly.py"))
    pcm = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(pcm)
    except SystemExit:
        pass
    ov = pcm.get_ml_leg_overrides() if hasattr(pcm, "get_ml_leg_overrides") else None
    if not ov or "trailing_stop_pct" not in ov:
        print("[m3-review] M-3 登记参数不可得(守卫fail-closed), 拒绝判读")
        sys.exit(2)
    return ov


def read_monitor_log():
    if not os.path.exists(MON_LOG):
        return {"scans": 0, "triggers": 0, "last_line": "(日志不存在)"}
    scans = triggers = 0
    last = ""
    # 监控日志由 schtasks cmd 写入, 实测为 GBK 编码(UTF-8 读取丢中文致计数恒0)
    with open(MON_LOG, encoding="gbk", errors="ignore") as f:
        for line in f:
            last = line.rstrip()
            if "报告:" in line:
                scans += 1
            m = re.search(r"触发 (\d+) 只", line)
            if m:
                triggers += int(m.group(1))
    return {"scans": scans, "triggers": triggers, "last_line": last}


def read_forward_sample():
    if not os.path.exists(SHADOW_DB):
        return {"trades": 0, "positions": 0, "db": "(影子库不存在)"}
    c = sqlite3.connect(f"file:{SHADOW_DB}?mode=ro", uri=True)
    t = c.execute("SELECT COUNT(*) FROM trades WHERE source LIKE '%ml%'").fetchone()[0]
    p = 0
    try:
        p = c.execute(
            "SELECT COUNT(*) FROM positions WHERE source LIKE '%ml%'").fetchone()[0]
    except sqlite3.OperationalError:
        pass
    c.close()
    return {"trades": t, "positions": p, "db": "tulong_production_paper.db"}


def read_latest_extend():
    """读最新延伸跑次 compare.json(m3_trail_equity_extend_*/compare.json, 若存在)。
    延伸跑次=同9/3法数据延伸重放(口径②E3), 11-30 复核日复跑后本件自动汇总读数。"""
    import glob as _g
    hits = sorted(_g.glob(os.path.join(
        REPORT_DIR, "m3_trail_equity_extend_*", "compare.json")))
    if not hits:
        return None
    try:
        d = json.load(open(hits[-1], encoding="utf-8"))
        a, b = d.get("arm_a_baseline", {}), d.get("arm_b_m3_trail", {})
        ck = d.get("checks", {})
        return {
            "path": os.path.relpath(hits[-1], BASE),
            "data_end": (d.get("meta", {}) or {}).get("data_end", "-"),
            "a_ret": a.get("total_return_pct"), "a_mdd": a.get("max_drawdown_pct"),
            "b_ret": b.get("total_return_pct"), "b_mdd": b.get("max_drawdown_pct"),
            "gain": ck.get("c1_return_gain"),
            "mdd_ok": ck.get("c2_mdd_better_or_equal"),
        }
    except (json.JSONDecodeError, OSError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asof", default="")
    args = ap.parse_args()
    ov = guard_params()
    asof = args.asof or datetime.date.today().isoformat()
    e1, e2 = read_monitor_log(), read_forward_sample()
    ext = read_latest_extend()

    lines = [
        "# M-3 回退条款判读 (asof %s)" % asof,
        "",
        "> 判据(跑前冻结, config v1.4 原文): 2026-11-30 复评若增量转负则 trailing 回 0。",
        "> 本件只汇总证据线与样本现实, 判读权在主人。零交易权。",
        "",
        "## 参数(单一真源)",
        "- trailing_stop_pct=%s, trail_activation_pct=%s, 指纹 a41fbe252a3af5b7641a" % (
            ov.get("trailing_stop_pct"), ov.get("trail_activation_pct")),
        "",
        "## E1 trailing 监控史(在役 trailing_monitor_ml.py)",
        "- 累计扫描 %d 次, 累计触发 %d 只" % (e1["scans"], e1["triggers"]),
        "- 末行: %s" % e1["last_line"],
        "",
        "## E2 模拟盘 forward 样本",
        "- ml 腿成交 %d 笔 / 在仓 %d 只 (%s)" % (
            e2["trades"], e2["positions"], e2["db"]),
        "",
        "## E3 回测 A/B 锚(9/3 基线, 复核日同法延伸复跑)",
        "- 锚: %s" % AB_ANCHOR["source"],
        "- trail 臂 %s vs 基线 %s (%s)" % (
            AB_ANCHOR["trail_arm"], AB_ANCHOR["baseline_arm"], AB_ANCHOR["delta"]),
        "- 复跑: `%s`" % AB_ANCHOR["rerun_cmd"],
        "",
        "## 样本现实与判读提示",
    ]
    if ext:
        lines += [
            "## E3+ 最新延伸跑次(同9/3法, 数据延伸重放)",
            "- 产物: %s (data_end=%s)" % (ext["path"], ext["data_end"]),
            "- A 基线: +%.2f%% / MDD %.2f%% | B M-3: +%.2f%% / MDD %.2f%%" % (
                ext["a_ret"], ext["a_mdd"], ext["b_ret"], ext["b_mdd"]),
            "- 增益 +%.2fpp | MDD 改善: %s" % (
                ext["gain"], "是" if ext["mdd_ok"] else "否"),
            "- (诚实层: 历史重放非真 forward; meta.date 为 9/3 模板字面量,",
            "  跑次身份以目录名+data_end 为准)",
            "",
        ]
    if e2["trades"] == 0 and e2["positions"] == 0:
        lines += [
            "- 🔴 **forward 样本为零**: ml 腿模拟盘建仓未发生(自 M-3 复活 9/4 裁决、",
            "  10/1 调仓窗口至今), E2 线不可用; trailing 触发数为 0 属输入恒空非闸失灵。",
            "- ✅ **口径裁决(主人 2026-10-12「继续」令按推荐)**: 复核口径 = **②E3 回测延伸",
            "  单线**(不补模拟盘建仓, 不引入新交易动作)。复核日动作 = 复跑",
            "  `tulong_ab_m3_trail_equity_20260903.py` 同法把数据延伸至最新, 对照 9/3 锚",
            "  (+38.39pp) 判增量正负; 【预判,存在不确定性】10-11 月新增段仅 ~2 个月,",
            "  对 +38.39pp 量级检验功效有限, 倾向『样本不足→维持+延长』而非双向可信结论;",
            "  反向风险 = 回测延伸仍非真 OOS, 结论权重须降档并如实注记。",
        ]
    else:
        lines += [
            "- forward 样本 %d 笔%s, %s" % (
                e2["trades"],
                "(<10 笔证据力不足, 判读降级 E3 单线)" if e2["trades"] < 10 else "(≥10 笔可做 realized 增量对比)",
                "建议按 E2 实际成交做 trailing 触发笔反事实对比后汇总"),
        ]
    lines += ["", "---", "判读权: 主人 | 本件零交易权零写库"]
    out = os.path.join(REPORT_DIR, "m3_trail_review_%s.md" % asof.replace("-", ""))
    with open(out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    # 读回核验(计数纪律)
    with open(out, encoding="utf-8") as f:
        back = f.read()
    assert "M-3 回退条款判读" in back and str(e2["trades"]) in back, "读回核验失败"
    print("[m3-review] 报告: %s" % out)
    print("[m3-review] E1 扫描%d/触发%d | E2 成交%d/在仓%d | 守卫PASS" % (
        e1["scans"], e1["triggers"], e2["trades"], e2["positions"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
