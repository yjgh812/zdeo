# -*- coding: utf-8 -*-
"""腾讯行情快照字段位次探针 (2026-10-14, daily_basic 批量提速前置验证, 只读零写库)

目的: 实测 qt.gtimg.cn 返回字段与 fundamental_data.db daily_basic (20261013, 现合同真值)
逐列对拍, 确定位次映射后再接入 daily_chain_update.run_daily_basic 兜底路径。
禁止凭记忆定字段位次 —— 全部位次以本探针实测为准。

用法: python analysis/probe_tencent_daily_basic_fields_20261014.py
"""
import sqlite3
import sys
import urllib.request

FUND_DB = r"D:\workbuddyspace\databases\fundamental_data.db"
TARGET = "20261013"
SAMPLES = ["sh600519", "sz000001", "sh601398", "sz300750", "sh601127", "sz002591"]


def fetch(symbols):
    url = "http://qt.gtimg.cn/q=" + ",".join(symbols)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = r.read().decode("gbk", errors="ignore")
    out = {}
    for line in data.strip().split(";"):
        line = line.strip()
        if "~" not in line:
            continue
        key = line.split("=", 1)[0].replace("v_", "").strip()
        out[key] = line.split("=", 1)[1].strip('"').split("~")
    return out


def main():
    quotes = fetch(SAMPLES)
    con = sqlite3.connect(f"file:{FUND_DB}?mode=ro", uri=True)
    for sym in SAMPLES:
        vals = quotes.get(sym)
        if not vals:
            print(f"[MISS] {sym} 无快照")
            continue
        ts_code = sym[2:] + (".SH" if sym.startswith("sh") else ".SZ")
        row = con.execute(
            "SELECT close,turnover_rate,volume_ratio,pe_ttm,pb,dv_ratio,dv_ttm,total_share,"
            "float_share,total_mv,circ_mv FROM daily_basic WHERE ts_code=? AND trade_date=?",
            (ts_code, TARGET)).fetchone()
        print(f"\n===== {sym} ({ts_code}) 字段数={len(vals)} =====")
        if row is None:
            print("  库内无 20261013 行, 仅打印原始字段")
        else:
            print(f"  库行: close={row[0]} turn={row[1]} vr={row[2]} pe_ttm={row[3]} pb={row[4]} "
                  f"dv_ratio={row[5]} dv_ttm={row[6]} tot_sh={row[7]} flt_sh={row[8]} "
                  f"tot_mv={row[9]} circ_mv={row[10]}")
        # 打印疑似估值相关位次(3, 38-49, 64-73)的原始值
        for i in sorted(set([3, 5, 38, 39, 43, 44, 45, 46, 47, 48, 49, 64, 65, 66, 72, 73])):
            if i < len(vals):
                print(f"  idx[{i:2d}] = {vals[i]}")
    con.close()


if __name__ == "__main__":
    sys.exit(main())
