# -*- coding: utf-8 -*-
"""em_fetch_daily.py — 通用日度 EM 资金流拉取器（22:00 十三合一 第十三步③ 程序化通道）
用法: python em_fetch_daily.py --date 20260917 [--days 30]
读 data/em_todo_<date>.json 的 codes, WeStockClient.call('data_fund_flow') 分块查
  近 N 自然日日序列, 累计当日/5/10/20 交易日 MainNetFlow(元), 写 data/em_results_<date>.json。
(8/27 补漏器 em_fetch_20260827.py 的参数化版本, 免每日复制脚本)
"""
import argparse
import json
import sys
import time
from datetime import datetime, timedelta

sys.path.insert(0, r"D:\workbuddyspace")
from westock_client import WeStockClient  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--date", required=True, help="YYYYMMDD")
ap.add_argument("--days", type=int, default=30, help="回看自然日数(默认30≈20交易日)")
a = ap.parse_args()

D = a.date
TODO = rf"D:\zcodesj\data\em_todo_{D}.json"
OUT = rf"D:\zcodesj\data\em_results_{D}.json"
END = datetime.strptime(D, "%Y%m%d")
START = END - timedelta(days=a.days)
START_S, END_S = START.strftime("%Y-%m-%d"), END.strftime("%Y-%m-%d")
CHUNK = 5


def prefix(c: str) -> str:
    return ("sh" if c.startswith(("6", "9", "5")) else
            "bj" if c[0] in "48" else "sz") + c


def main() -> None:
    todo = json.load(open(TODO, encoding="utf-8"))
    codes, names = todo["codes"], todo.get("names", {})
    if isinstance(names, list):
        names = dict(zip(codes, names))
    ws = WeStockClient()
    ws.ensure_fresh()

    series: dict[str, list[dict]] = {}
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        args = {"codes": ",".join(prefix(c) for c in chunk),
                "start": START_S, "end": END_S}
        r = ws.call("data_fund_flow", args)
        payload = r.get("data", r) if isinstance(r, dict) else r
        body = payload.get("data", payload) if isinstance(payload, dict) else {}
        for key, val in body.items():
            rows = val.get("data", []) if isinstance(val, dict) else []
            if rows:
                series[str(key)[-6:]] = sorted(rows, key=lambda x: x["date"])
        print(f"[{i + len(chunk)}/{len(codes)}] 累计取到 {len(series)} 只", flush=True)
        time.sleep(3.0)  # 9/19 实测: 上游服务限频(空响应), 加大间隔防误判缺数据

    out = []
    for c in codes:
        rows = series.get(c)
        if not rows:
            print(f"  [缺] {c} {names.get(c, '')} 无日序列")
            continue
        mflows = [float(x["MainNetFlow"]) for x in rows]  # 已按日期升序
        out.append({
            "code": c,
            "name": names.get(c, ""),
            "em_today": mflows[-1],
            "em_5d": sum(mflows[-5:]),
            "em_10d": sum(mflows[-10:]),
            "em_20d": sum(mflows[-20:]),
        })
    json.dump(out, open(OUT, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[落盘] {OUT} 共 {len(out)}/{len(codes)} 只")


if __name__ == "__main__":
    main()
