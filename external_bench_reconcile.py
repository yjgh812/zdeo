# -*- coding: utf-8 -*-
"""外部AI选股基准组 30日对账（主人 2026-09-21 反思令落地件）

数据源: data/external_benchmark_20260921.json（基期 9/11 收盘 5 只，经日K精确验证）
对账: 对方组区间收益 vs 上证 beta vs 我方 v5.2 建仓组同期收益 → toast + 报告落盘
触发: schtasks ExternalBenchReconcile_1016 (2026-10-16 09:20 一次性)
用途: 用数据裁决「底部反转左侧」vs「右侧趋势确认」两范式，替代立场性争论。
"""
from __future__ import annotations
import json, os, sys, urllib.request
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
TRACK = os.path.join(ROOT, "data", "external_benchmark_20260921.json")


def quote(code: str) -> float | None:
    try:
        url = f"http://qt.gtimg.cn/q={code}"
        req = urllib.request.Request(url)
        req.add_header("Referer", "http://gtimg.cn")
        data = urllib.request.urlopen(req, timeout=10).read().decode("gbk", errors="replace")
        f = data.split("=")[1].strip('"\n').split("~")
        return float(f[3]) if len(f) > 3 else None
    except Exception:
        return None


def index_ret(start: str, end: str | None = None) -> float | None:
    """上证区间收益(基期日收盘→最新收盘)。"""
    try:
        url = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get?param=sh000001,day,,,40,"
        req = urllib.request.Request(url)
        req.add_header("Referer", "https://gu.qq.com")
        d = json.loads(urllib.request.urlopen(req, timeout=10).read().decode("utf-8"))
        k = d["data"]["sh000001"]["day"]
        closes = {r[0]: float(r[2]) for r in k}
        base = closes.get(start)
        # end=None 取最后一根收盘(可能是今日实时bar)
        last = closes.get(end) if end else list(closes.values())[-1]
        if base and last:
            return last / base - 1
    except Exception:
        pass
    return None


def main() -> None:
    d = json.load(open(TRACK, encoding="utf-8"))
    base_date = d["external"]["base_date"]
    lines = [f"===== 外部基准组对账 {datetime.now():%Y-%m-%d %H:%M} (基期{base_date}) ====="]
    beta = index_ret(base_date)
    if beta is not None:
        lines.append(f"上证同期: {beta*100:+.2f}%")

    def group_ret(stocks, key):
        rs = []
        for s in stocks:
            px = quote(s["code"])
            if px and s.get(key):
                rs.append((s["name"], px / s[key] - 1))
        return rs

    ext = group_ret(d["external"]["stocks"], "base_close")
    ours = group_ret(d["ours_v52"]["stocks"], "base_price")
    for tag, rs in (("外部(底部反转)", ext), ("我方v5.2(右侧确认)", ours)):
        if rs:
            m = sum(r for _, r in rs) / len(rs)
            win = sum(1 for _, r in rs if r > 0)
            detail = " ".join(f"{n}{r*100:+.1f}" for n, r in rs)
            lines.append(f"{tag}: 均值{m*100:+.2f}% 胜率{win}/{len(rs)} | {detail}")
            if beta is not None:
                lines.append(f"  超额(vs上证): {(m-beta)*100:+.2f}pp")
    text = "\n".join(lines)
    print(text)
    out = os.path.join(ROOT, "reports", f"external_bench_reconcile_{datetime.now():%Y%m%d}.md")
    with open(out, "w", encoding="utf-8") as f:
        f.write("# 外部基准组对账\n\n```\n" + text + "\n```\n")
    try:
        from notify import send_toast
        send_toast("外部基准组对账就绪", f"详见 {out}")
    except Exception:
        pass


if __name__ == "__main__":
    main()
