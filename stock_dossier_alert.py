#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
stock_dossier_alert.py — 个股档案晨报toast告警(09:15 schtasks)
==============================================================
定位: 档案呈现的确定性规则层(晨会automation接线的fallback, 全局automation上限20满)。
  从最新 持仓_全息档案_YYYYMMDD.md 速览总表提取三类重点→本机toast(notify.py):
    a) 量能=爆量   b) S-5距<+3.0%(贴/破止损警戒)   c) 月线区=月顶区(附乖离值)
  另做新鲜度检查(档案日期落后qfq最新交易日=error)。
  假日静默(当日非qfq交易日→只写日志不响toast)。
  零交易权; 档案=事实呈现层, 判断归闸门层与主人。
用法: python stock_dossier_alert.py            (schtasks每日09:15)
      python stock_dossier_alert.py --test    (强制toast, 假日守卫旁路)
"""
import glob
import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
ROOT = Path(r"D:\zcodesj")
DOSSIER_DIR = ROOT / "analysis" / "持仓档案"
QFQ_DB = "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
sys.path.insert(0, str(ROOT))
from stock_dossier import load_kline, compute_trend, _load_json  # noqa: E402
from stock_name_lookup import get_name  # noqa: E402


def notify(title, content, level="info"):
    subprocess.run(
        [sys.executable, str(ROOT / "notify.py"), "send",
         "--title", title, "--content", content, "--level", level],
        capture_output=True, timeout=30,
    )


def parse_summary_table(md_text):
    """解析速览总表 → [{code,name,pnl,ma,pos,zone,vr,ret20,phase,s5}]"""
    rows = []
    for line in md_text.splitlines():
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 10 or cells[0] == "代码":
            continue
        if set(cells[0]) <= {"-", ""}:
            continue
        rows.append({
            "code": cells[0], "name": cells[1], "pnl": cells[2],
            "ma": cells[3], "pos": cells[4], "zone": cells[5],
            "vr": cells[6], "ret20": cells[7], "phase": cells[8], "s5": cells[9],
        })
    return rows


def bias_of(md_text, code):
    """明细段取该票乖离MA20数值(%)"""
    blocks = re.split(r"\n### ", md_text)
    for b in blocks:
        if b.startswith(code):
            m = re.search(r"乖离MA20\s+([+-]?[\d.]+)%", b)
            return float(m.group(1)) if m else None
    return None


def f_pct(s):
    try:
        return float(str(s).rstrip("%").replace("+", ""))
    except (ValueError, TypeError):
        return None


def main():
    force = "--test" in sys.argv
    today = datetime.now().strftime("%Y%m%d")
    files = sorted(DOSSIER_DIR.glob("持仓_全息档案_*.md"))
    if not files:
        notify("个股档案晨报", "无持仓档案产物(analysis/持仓档案/为空), TulongStockDossier_2330 可能未配置或未跑", "error")
        return 1
    path = files[-1]
    md = path.read_text(encoding="utf-8")
    rows = parse_summary_table(md)
    if not rows:
        notify("个股档案晨报", f"档案 {path.name} 无有效速览表, 如实报告不做脑补", "error")
        return 1

    c = sqlite3.connect(QFQ_DB, uri=True)
    latest = c.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    c.close()
    # 交易日判定复用系统双源守卫(trade_day_guard: tushare→baostock+24h缓存)
    # 🔴不得用"qfq今日行存在性"判定——09:15当日行情未入库(晚间更新)会永久误判假日哑火
    # 守卫语义: True交易日/False休市/None日历不可用 → 仅False静默, None=fail-open响铃宁误勿漏
    m = re.search(r"持仓_全息档案_(\d{8})\.md", path.name)
    fdate = m.group(1) if m else "0"
    try:
        from trade_day_guard import is_trading_day
        tflag = is_trading_day(today)
    except Exception:
        tflag = None
    if tflag is False and not force:
        print(f"假日静默(today={today} 非交易日[trade_day_guard], latest={latest}, 档案={fdate}) — 不响toast")
        return 0
    cal_note = "" if tflag is True else "⚠交易日历不可用(fail-open响铃)"
    stale = fdate < latest
    # 对称缺口: 档案日期超前行情=当日22:00数据链未跑成, 23:30档案用旧数据但原逻辑无告警
    ahead = fdate > latest

    lines = []
    baoliang = [f"{r['code']} {r['name']} vr={r['vr']} ret20={r['ret20']}" for r in rows if r["vr"] == "爆量"]
    if baoliang:
        lines.append("【爆量】" + "; ".join(baoliang))
    tie_s5 = [f"{r['code']} {r['name']} 距S-5线{r['s5']}" for r in rows
              if f_pct(r["s5"]) is not None and f_pct(r["s5"]) < 3.0]
    if tie_s5:
        lines.append("【贴/破S-5线】" + "; ".join(tie_s5))
    yueding = []
    for r in rows:
        if "月顶区" in r["zone"]:
            b = bias_of(md, r["code"])
            yueding.append(f"{r['code']} {r['name']}(乖离{b:+.1f}%)" if b is not None else f"{r['code']} {r['name']}")
    if yueding:
        lines.append("【月顶区】" + "; ".join(yueding))
    if not lines:
        lines.append("无爆量/贴止损/月顶区持仓")
    if stale:
        lines.append(f"⚠档案{fdate}落后行情{latest}, TulongStockDossier_2330未跑或数据链未更新")
    if ahead:
        lines.append(f"⚠档案{fdate}超前行情{latest}: 当日数据链可能未跑成(22:00链), 档案为旧数据口径")
    if cal_note:
        lines.append(cal_note)

    # 跟踪池右侧触发器检测(data/watchlist_codes.json; 触发器1=量能vol20/vol60回升>=1.0,
    # 触发器2=站上MA20; 触发器3=主力20日净流转正需westock MCP人工复核, 离线不测)
    try:
        wl = _load_json(ROOT / "data" / "watchlist_codes.json")
        hits = []
        for wcode, wmeta in wl.items():
            wrows = load_kline(wcode)
            if not wrows:
                continue
            wname = wmeta.get("name") or get_name(wcode) or wcode
            wt = compute_trend(wrows)
            if not wt:
                continue
            fired = []
            if wt["vr"] is not None and wt["vr"] >= 1.0:
                fired.append(f"量能回升{wt['vr']:.2f}x")
            if wt["mas"].get(20) and wt["last"] > wt["mas"][20]:
                fired.append("站上MA20")
            if fired:
                hits.append(f"{wcode} {wname}: {'+'.join(fired)}")
        if hits:
            lines.append("【跟踪池触发】" + "; ".join(hits))
        else:
            print(f"watchlist triggers: none ({len(wl)}只未命中)")
    except Exception as e:
        print(f"watchlist check skipped: {type(e).__name__}: {e}")

    title = f"个股档案晨报{datetime.now().strftime('%m%d')}"
    level = "error" if (stale or ahead) else "info"
    notify(title, "\n".join(lines), level)
    print(f"alert sent: {title} | rows={len(rows)} 爆量{len(baoliang)} 贴S-5 {len(tie_s5)} 月顶区{len(yueding)} stale={stale} (档案={path.name}, 行情={latest})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
