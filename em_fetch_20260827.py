# -*- coding: utf-8 -*-
"""em_fetch_20260827.py — 8/27 双口径 EM 段补漏: 一次性批查 westock data_fund_flow 落盘 em_results

背景: 8/27 22:35 双口径采集未落库(flow_snapshot 2026-08-27=0行), 00:2x 接管补漏。
THS 段已由 collect_dual_flow_daily.py --phase ths --date 20260827 完成。
本脚本: 读 data/em_todo_20260827.json(30只=scanner候选8+持仓22),
  WeStockClient.call('data_fund_flow') 分块查 2026-07-28~2026-08-27 日序列,
  累计当日/5/10/20 交易日 MainNetFlow(元), 写 data/em_results_20260827.json。
用法: python em_fetch_20260827.py
"""
import json
import sys
import time

sys.path.insert(0, r"D:\workbuddyspace")
from westock_client import WeStockClient  # noqa: E402

TODO = r"D:\zcodesj\data\em_todo_20260827.json"
OUT = r"D:\zcodesj\data\em_results_20260827.json"
START, END = "2026-07-28", "2026-08-27"
CHUNK = 5


def prefix(c: str) -> str:
    return ("sh" if c.startswith(("6", "9", "5")) else
            "bj" if c[0] in "48" else "sz") + c


def main() -> None:
    todo = json.load(open(TODO, encoding="utf-8"))
    codes, names = todo["codes"], todo.get("names", {})
    ws = WeStockClient()
    ws.ensure_fresh()

    series: dict[str, list[dict]] = {}
    for i in range(0, len(codes), CHUNK):
        chunk = codes[i:i + CHUNK]
        args = {"codes": ",".join(prefix(c) for c in chunk),
                "start": START, "end": END}
        r = ws.call("data_fund_flow", args)
        payload = r.get("data", r) if isinstance(r, dict) else r
        # 兼容 {"ok":true,"data":{code:{data:[...]}}} 与纯 {code:{data:[...]}}
        body = payload.get("data", payload) if isinstance(payload, dict) else {}
        for key, val in body.items():
            rows = val.get("data", []) if isinstance(val, dict) else []
            if rows:
                series[str(key)[-6:]] = sorted(rows, key=lambda x: x["date"])
        print(f"[{i + len(chunk)}/{len(codes)}] 累计取到 {len(series)} 只", flush=True)
        time.sleep(0.6)

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
