# -*- coding: utf-8 -*-
"""baostock 5分钟 vs 本地 stock_minute_5 重叠窗对拍(呈请②前置件, 只读零交易风险)
窗口=2026-10-14~16(本地已完整入库的最近3个交易日), 样本=600000浦发银行/000001平安银行
比对: (date,time)对齐→close容差0.005(float32噪声)一致率+最大偏差; volume比值中位数(单位因子探测)
产出: analysis/baostock_5min_overlap_probe_20261020.json
"""
import json
import sqlite3
import statistics

import baostock as bs

META_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_meta.db"
WIN = ("2026-10-14", "2026-10-16")
SAMPLES = [("600000", "sh.600000", "浦发银行"), ("000001", "sz.000001", "平安银行")]
TOL = 0.005


def main():
    import time
    db = sqlite3.connect(f"file:{META_DB}?mode=ro", uri=True)
    lg = None
    # 2026-10-20晚: MCP同源通道实测健康(48bar秒回)→病根=登录握手间歇抖动(六跑两成);
    # 修法=8次递进退避(5/10/15/20/30/40/50/60s), 累计成功率≈99%+
    waits = [5, 10, 15, 20, 30, 40, 50, 60]
    lg = None
    for i, w in enumerate([0] + waits, start=1):
        if w:
            time.sleep(w)
        lg = bs.login()
        if lg.error_code == "0":
            print(f"[login] 第{i}次尝试成功", flush=True)
            break
        print(f"[warn] baostock登录失败({i}/{len(waits)+1}): {lg.error_msg}", flush=True)
    assert lg.error_code == "0", f"baostock登录失败: {lg.error_msg}"
    out = {"window": WIN, "samples": {}, "conventions": {
        "local_time": "bar末分钟int(935)", "local_volume": "股(待验证)",
        "bs_time": "bar末'YYYY-MM-DD HH:MM:SS'", "tol": TOL}}
    for code6, bscode, name in SAMPLES:
        rows = db.execute(
            "select date,time,close,volume from stock_minute_5 "
            "where code=? and date>=? and date<=? order by date,time",
            (code6, int(WIN[0].replace("-", "")), int(WIN[1].replace("-", "")))).fetchall()
        local = {(str(d), str(t).zfill(4)): (c, v) for d, t, c, v in rows}
        rs = bs.query_history_k_data_plus(code=bscode, start_date=WIN[0], end_date=WIN[1],
                                          frequency="5", adjustflag="3",
                                          fields="date,time,close,volume")
        bsrows = []
        while rs.error_code == "0" and rs.next():
            f = rs.get_row_data()
            bsrows.append(f)  # [time,date,time,open,high,low,close,volume,amount,adjustflag]
        bs_map = {}
        for f in bsrows:
            d = f[0].replace("-", "")
            t = f[1][8:12]  # baostock 5分钟time字段='YYYYMMDDHHMMSS000', 分钟=第8-11位
            bs_map[(d, t)] = (float(f[2]), float(f[3]))
        common = sorted(set(local) & set(bs_map))
        close_ok, max_diff, vols = 0, 0.0, []
        diffs = []
        for k in common:
            lc, lv = local[k]
            bc, bv = bs_map[k]
            diff = abs(lc - bc)
            diffs.append(lc - bc)
            max_diff = max(max_diff, diff)
            if diff <= TOL:
                close_ok += 1
            if bv > 0:
                vols.append(lv / bv)
        res = {
            "name": name, "local_bars": len(local), "bs_bars": len(bs_map),
            "aligned": len(common),
            "close_match_rate": round(close_ok / len(common), 4) if common else None,
            "close_max_abs_diff": round(max_diff, 4),
            "close_med_diff": round(statistics.median(diffs), 4) if diffs else None,
            "vol_ratio_med": round(statistics.median(vols), 4) if vols else None,
        }
        out["samples"][bscode] = res
        print(bscode, name, res, flush=True)
    bs.logout()
    out["verdict"] = {
        "both_aligned_full": all(v["aligned"] == v["local_bars"] == v["bs_bars"]
                                 for v in out["samples"].values()),
        "close_rates": [v["close_match_rate"] for v in out["samples"].values()],
        "vol_ratios": [v["vol_ratio_med"] for v in out["samples"].values()],
    }
    with open(r"D:\zcodesj\analysis\baostock_5min_overlap_probe_20261020.json", "w",
              encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)
    print("[done] → baostock_5min_overlap_probe_20261020.json", flush=True)


if __name__ == "__main__":
    main()
