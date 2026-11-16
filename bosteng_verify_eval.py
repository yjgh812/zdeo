# -*- coding: utf-8 -*-
"""bosteng_verify_eval.py — 博腾股份(300363)验证单 T+20 到期判定 (2026-09-19 立, 主人9/16验证单契约收口件)

契约回顾 (9/16 记忆): 验证单买入博腾 1000 股, 判定锚=成交日起 20 个交易日收盘;
  预判依据 = fwd20 中位 -4.58% / 胜率≤39% (弱势预期);
  判定 = 20日收益 > +5% → 证伪(实际强势, 验证单预判错);
         ≤ +5% → 验证单预判成立(弱势兑现)。
执行件 = data/_bosteng_exec_20260921.py @ schtasks BostengVerify_Exec_0921, 结果落
  data/_bosteng_300363_exec_result.json (未执行/被拒 → 本判定输出"不适用"终态)。
止损锚 19.52 由 S-5 止损链自动负责, 本件零交易权纯只读判定。
调度 = 一次性 automation 10/21 09:20 (T+20 未到则如实输出顺延)。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
RESULT = os.path.join(ROOT, "data", "_bosteng_300363_exec_result.json")
QFQ = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_MD = os.path.join(ROOT, "reports", f"bosteng_verify_eval_{datetime.now():%Y%m%d}.md")
WIN = 20  # 交易日


def main():
    lines, verdict = [], "N/A"
    A = lines.append
    A("# 博腾股份(300363) 验证单 T+20 到期判定")
    A("")
    if not os.path.exists(RESULT):
        A("- 执行结果文件缺失: 验证单未执行(9/21 编排未跑或产物丢失), 判定不适用")
        verdict = "不适用(未执行)"
        _finish(lines, verdict)
        return
    r = json.load(open(RESULT, encoding="utf-8"))
    if not r.get("executed"):
        A(f"- 执行结果 executed=false (拒绝/守卫跳过): {json.dumps(r, ensure_ascii=False)[:300]}")
        verdict = "不适用(未成交)"
        _finish(lines, verdict)
        return
    fill_date = str(r.get("fill_date") or r.get("date") or "")[:8].replace("-", "")
    fill_px = r.get("fill_price") or r.get("price")
    if not (fill_date.isdigit() and fill_px):
        A(f"- 结果字段缺成交日/价: {json.dumps(r, ensure_ascii=False)[:300]}")
        verdict = "不适用(字段缺失)"
        _finish(lines, verdict)
        return
    db = sqlite3.connect(f"file:{QFQ}?mode=ro", uri=True)
    dates = [d for (d,) in db.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>=? ORDER BY date LIMIT ?",
        (fill_date, WIN + 1))]
    pxs = dict(db.execute(
        "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND date>=? ORDER BY date LIMIT ?",
        ("300363", fill_date, WIN + 1)).fetchall())
    db.close()
    if len(dates) < WIN + 1 or len(pxs) < WIN + 1:
        A(f"- T+20 窗口未到 (已有 {max(len(dates), len(pxs)) - 1}/20 交易日), 顺延重跑")
        verdict = "顺延(窗口未到)"
        _finish(lines, verdict)
        return
    d20, px20 = dates[WIN], pxs.get(dates[WIN])
    ret = (px20 / fill_px - 1) * 100 if px20 and fill_px else None
    if ret is None:
        verdict = "不适用(价格缺失)"
        A(f"- T+20({d20}) 价格缺失")
    elif ret > 5.0:
        verdict = "证伪(20日>+5%: 实际强势, 验证单弱势预判错)"
    else:
        verdict = "预判成立(20日≤+5%: 弱势兑现)"
    A(f"- 成交: {fill_date} @ {fill_px} (验证单 1000 股)")
    A(f"- T+20: {d20} 收盘 {px20} → 20日收益 {ret:+.2f}%")
    A(f"- 判定锚: >+5% 证伪 / ≤+5% 预判成立 (预判依据 fwd20 中位 -4.58%/胜率≤39%)")
    A(f"- **判定: {verdict}**")
    A("- 止损锚 19.52 由 S-5 链负责; 本件只读零交易权")
    _finish(lines, verdict)


def _finish(lines, verdict):
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    open(OUT_MD, "w", encoding="utf-8").write("\n".join(lines))
    print(f"[BOSTENG-EVAL] {verdict} | {OUT_MD}")


if __name__ == "__main__":
    main()
