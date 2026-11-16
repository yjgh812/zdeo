# -*- coding: utf-8 -*-
"""backup_source_hitrate_20261018.py — 备源链命中率观测脚本 (V12, DeepSeek审计吸收项)

场景: 备源链(新浪→baostock→tushare)主源降级时静默切换无感知(10/18 DeepSeek审计B-4)。
本件为**独立观测脚本**(不改备源链代码), 通过试呼主源+备源端点并比对返回来量化命中率。

用法: python analysis/backup_source_hitrate_20261018.py [--runs 3]
输出: analysis/backup_source_hitrate_20261018_out.json (追加模式, 季度复跑累积)
"""
import json, os, sys, time
from datetime import datetime
sys.path.insert(0, r"D:\zcodesj")
sys.stdout.reconfigure(encoding="utf-8")
OUT = r"D:\zcodesj\analysis\backup_source_hitrate_20261018_out.json"
TEST_CODE = "000858"  # 五粮液(流动性好, 全源必有)

def try_sina():
    """主源: 新浪行情(实时/日线基线口径)"""
    try:
        import urllib.request
        url = f"https://hq.sinajs.cn/list=sz{TEST_CODE}"
        req = urllib.request.Request(url, headers={"Referer": "https://finance.sina.com.cn"})
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
        return "ok" if len(data) > 30 else "short_response"
    except Exception as e:
        return f"err:{e}"

def try_baostock():
    """备源1: baostock"""
    try:
        import baostock as bs
        lg = bs.login()
        if lg.error_code != "0":
            return f"login_err:{lg.error_msg}"
        rs = bs.query_history_k_data_plus(f"sz.{TEST_CODE}", "date,close", start_date="2026-10-01", end_date="2026-10-16", frequency="d", adjustflag="3")
        rows = []
        while rs.error_code == "0" and rs.next():
            rows.append(rs.get_row_data())
        bs.logout()
        return "ok" if rows else "empty"
    except Exception as e:
        return f"err:{e}"

def try_tushare():
    """备源2: tushare(token须配置)"""
    try:
        import tushare as ts
        pro = ts.pro_api()
        df = pro.daily(ts_code=f"{TEST_CODE}.SZ", start_date="20261001", end_date="20261016")
        return "ok" if len(df) > 0 else "empty"
    except Exception as e:
        return f"err:{str(e)[:60]}"

def main():
    runs = int(sys.argv[sys.argv.index("--runs") + 1]) if "--runs" in sys.argv else 3
    results = []
    for i in range(runs):
        sina = try_sina()
        bs_r = try_baostock()
        ts_r = try_tushare()
        results.append({"run": i + 1, "sina": sina, "baostock": bs_r, "tushare": ts_r})
        print(f"  run{i+1}: sina={sina} baostock={bs_r} tushare={ts_r}")
    # 汇总
    sina_ok = sum(1 for r in results if r["sina"] == "ok")
    bs_ok = sum(1 for r in results if r["baostock"] == "ok")
    ts_ok = sum(1 for r in results if r["tushare"] == "ok")
    summary = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "runs": runs,
        "sina_hitrate": f"{sina_ok}/{runs}",
        "baostock_hitrate": f"{bs_ok}/{runs}",
        "tushare_hitrate": f"{ts_ok}/{runs}",
        "detail": results,
    }
    # 追加模式
    existing = []
    if os.path.exists(OUT):
        existing = json.load(open(OUT, encoding="utf-8"))
    existing.append(summary)
    json.dump(existing, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[OK] 主源命中率 {sina_ok}/{runs} | 已追加 {OUT}")
    return 0

if __name__ == "__main__":
    sys.exit(main())
