r"""stage_screener.py — 四阶段全市场分池选股器（零交易权, 只读 qfq+daily_basic）。
方法来源: 主人 2026-11-10 令「选阶段+选触发」五层漏斗第三份框架。
池定义(全部复用 stage_locator.locate, 不造第二实现):
  A_吸筹观察   = S1_吸筹观察(UNVERIFIED描述性, 不买)
  B1_洗盘严格  = S2 且在当晚 pullback_scan 观察档(缩量条件, 生产扫描器口径)
  B2_洗盘位置  = S2 位置口径(宽, 仅[锚,+3%]未查缩量, 与B1交集才是重点)
  C_震仓已收复 = S3a(569样本验证: 收复=假摔, 持有纪律; 场外不追=回补口径负)
  C2_震仓待判  = S3 当日急跌未破锚(次日收复判别)
  C3_震仓未收复= S3b(撤离判别组, 十年-4.08%)
  D_拉升初期   = S4a(5日<10%且锚上<10%, 新仓需主人特批)
  RISK_超热/破锚 = 撤离侧风险名单(只计数+TOP20)
买入侧过滤: 剔4/8/9+688/689前缀; close>30剔除(宪法铁律3); close<1.5剔除(面值退市风险区, 框架排除项)。
局限声明: ST/退市风险无标记数据源未过滤, 属已知未过滤维度。
用法: python attribution/stage_screener.py [可选:逗号分隔codes=抽样, 缺省=全市场]
"""
import glob
import json
import os
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, r"D:\zcodesj\attribution")
import stage_locator as SL

QFQ_DB = SL.QFQ_DB
OUT_DIR = r"D:\zcodesj\reports"


def latest_scan_codes():
    scans = sorted(glob.glob(os.path.join(OUT_DIR, "pullback_scan_*.json")))
    if not scans:
        return set()
    with open(scans[-1], encoding="utf-8") as f:
        d = json.load(f)
    return set(str(x.get("code")) for x in (d.get("candidates") or []) + (d.get("watchlist") or []))


def main():
    t0 = time.time()
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    conn = sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True)
    if arg:
        codes = [c.strip()[-6:] for c in arg.split(",") if c.strip()]
    else:
        codes = [r[0] for r in conn.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq")]
    latest_date = conn.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    conn.close()
    scan_set = latest_scan_codes()
    fconn = None
    try:
        fconn = sqlite3.connect("file:%s?mode=ro" % SL.FUND_DB, uri=True)
    except sqlite3.Error:
        fconn = None
    pools = {k: [] for k in ("A_吸筹观察", "B1_洗盘严格", "B2_洗盘位置", "C_震仓已收复",
                             "C2_震仓待判", "C3_震仓未收复", "D_拉升初期", "RISK_超热", "RISK_破锚")}
    n = err = over30 = 0
    for c in codes:
        s = str(c)
        if s.startswith(("4", "8", "9")) or s.startswith(("688", "689")):
            continue
        try:
            r = SL.locate(c, fconn=fconn)
        except Exception:
            err += 1
            continue
        st = r.get("stage", "")
        cl = r.get("close")
        if r.get("date") != latest_date:
            over30 += 1  # 停牌僵尸票: 最新有效bar不在最新交易日, 剔除
            continue
        if cl is None or cl > 30.0 or cl < 1.5:
            over30 += 1
            continue
        n += 1
        if st == "S1_吸筹观察":
            pools["A_吸筹观察"].append(r)
        elif st == "S2_洗盘入场区":
            pools["B1_洗盘严格" if s in scan_set else "B2_洗盘位置"].append(r)
        elif st == "S3a_震仓已收复":
            pools["C_震仓已收复"].append(r)
        elif st.startswith("S3_震仓当日"):
            pools["C2_震仓待判"].append(r)
        elif st == "S3b_震仓未收复":
            pools["C3_震仓未收复"].append(r)
        elif st == "S4a_拉升启动段":
            pools["D_拉升初期"].append(r)
        elif st.startswith("S4b"):
            pools["RISK_超热"].append(r)
        elif st == "S5x_破锚走坏":
            pools["RISK_破锚"].append(r)
        if n % 500 == 0:
            print("进度 %d/%d 耗时%.0fs" % (n, len(codes), time.time() - t0))
    if fconn is not None:
        fconn.close()
    date_tag = max((r.get("date", "NA") for p in pools.values() for r in p), default="NA")
    out = {"ts": time.strftime("%Y-%m-%d %H:%M:%S"), "date": date_tag,
           "universe_scanned": len(codes), "kept_le30": n, "skipped_over30": over30, "errors": err,
           "scan_strict_codes": sorted(scan_set), "pools": {k: v for k, v in pools.items()}}
    jp = os.path.join(OUT_DIR, "stage_screen_%s.json" % date_tag)
    with open(jp, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("== 四池筛选完成 %.0fs -> %s" % (time.time() - t0, jp))
    for k, v in pools.items():
        print("  %5d %s" % (len(v), k))
    for k in ("B1_洗盘严格", "B2_洗盘位置", "C_震仓已收复", "C2_震仓待判", "C3_震仓未收复", "D_拉升初期"):
        for r in pools[k][:30]:
            tv = (r.get("turnover") or {}).get("band", "")
            print("[池] %s %s close=%s %s %s" % (k[:2], r["code"], r.get("close"), tv, r.get("action", "")[:40]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
