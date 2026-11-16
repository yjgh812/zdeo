#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
昨日涨停板块资金流先行指标（2026-08-18 落地，复盘线索②）

========== 用途 ==========
8/17 实证：昨日涨停板块主力净流出 -33.76 亿 → 昨日 63 只涨停 33% 收跌，
一进二失败率高。本模块每天盘后计算"昨日涨停板块今日主力净流入"，
作为次日追涨强度的先行指标，写入 emotion 面板供 daily_trade_plan 消费。

========== 信号解读 ==========
  主力净流入 > 0        → 打板资金锁仓，次日溢价可期（强）
  主力净流入 0 ~ -20亿  → 中性，正常兑现
  主力净流入 < -30亿    → 打板资金集体离场，次日谨慎追涨

========== 用法 ==========
    python zt_flow_leading.py               # 对昨日涨停板块取今日资金（自动日期）
    python zt_flow_leading.py --date 20260818
输出: D:/zcodesj/reports/zt_flow_leading_YYYYMMDD.json
"""
import json
import os
import time
import urllib.parse
import urllib.request
from datetime import datetime

OUT_DIR = r"D:/zcodesj/reports"
HDRS = {"User-Agent": "Mozilla/5.0", "Referer": "https://quote.eastmoney.com/"}
HOST = "https://push2delay.eastmoney.com"


def fetch(url):
    req = urllib.request.Request(url, headers=HDRS)
    for _ in range(3):
        try:
            with urllib.request.urlopen(req, timeout=25) as r:
                return json.loads(r.read().decode("utf-8"))
        except Exception:
            time.sleep(2)
    return None


def last_trade_date_before(date: str) -> str:
    """取 date 的前一交易日（用东财涨停池接口探测，可用性已验证）"""
    from datetime import timedelta
    dt = datetime.strptime(date, "%Y%m%d")
    for i in range(1, 8):
        d2 = dt - timedelta(days=i)
        d4 = d2.strftime("%Y%m%d")
        url = (f"https://push2ex.eastmoney.com/getTopicZTPool?ut=7eea3edcaed734bea9cbfc24409ed989"
               f"&dpt=wz.ztzt&Pageindex=0&pagesize=1&sort=fbt%3Aasc&date={d4}")
        d = fetch(url)
        if d and d.get("data") and d["data"].get("tc", 0) > 0:
            return d4
    return None


def fetch_concepts_all():
    """全量分页拉概念板块（约500个）"""
    all_rows, pn = [], 1
    while True:
        url = (f"{HOST}/api/qt/clist/get?pn={pn}&pz=100&po=1&np=1&fltt=2&invt=2"
               f"&fid=f62&fs=m:90+t:3+f:!50&fields=f3,f12,f14,f62")
        d = fetch(url)
        if not d or not d.get("data") or not d["data"].get("diff"):
            break
        arr = d["data"]["diff"]
        all_rows.extend(arr)
        if len(arr) < 100:
            break
        pn += 1
        time.sleep(0.25)
    return all_rows


# 2026-09-23 凌晨补: push2delay/push2 的 clist 接口对本机持续 WAF(9/22 晚步4即缺,
# 非凌晨特有), 涨停池 push2ex 不受影响。本地 eastmoney 库 sector_basic 表含同名
# 字段(sector_name/change_pct/main_net_inflow, 当日快照由 22:00 链 eastmoney 环节
# 刷新), 作为第二源兜底, 产物加 source 标注。
LOCAL_SECTOR_DB = r"D:/zcodesj/eastmoney_quant/sector/sector_data.db"


def fetch_concepts_from_local():
    """本地 sector_basic 表兜底: 返回与 clist 接口同构的 [{f12,f14,f3,f62}]"""
    try:
        import sqlite3
        con = sqlite3.connect("file:%s?mode=ro" % LOCAL_SECTOR_DB, uri=True)
        rows = con.execute(
            "SELECT sector_code, sector_name, change_pct, main_net_inflow "
            "FROM sector_basic WHERE sector_type='concept'").fetchall()
        con.close()
        return [{"f12": c, "f14": n, "f3": pct, "f62": flow}
                for c, n, pct, flow in rows if flow is not None]
    except Exception:
        return []


# 2026-09-23 凌晨补(B1,知微): ftshare 网关第三层兜底——同 EM 板块资金流,但**经
# 9/21 对表验证为不同口径**(ftshare 修订值 vs clist 当晚初值: 昨日涨停 -11.24 vs
# 真值 -1.32 等全行系统性偏差), 非"口径不变"的纯传输迁移!仅作末层兜底,命中时
# main() 必须附带 caliber_warning,信号阈值(0/-30)按 clist 夜间口径标定,对修订
# 值误用会翻转解读(9/22: 本地+2.58 vs ftshare -20.32)。裁决前不得据其改判。
FTSHARE_HOST = "market.ft.tech"
FTSHARE_PATH = "/gateway/mcp"
FTSHARE_CALIBER_WARNING = ("ftshare_gateway口径与clist夜间初值系统性偏差(9/21对表:涨停-11.24 vs -1.32"
                           "等全行DIFF,疑似EM T+1修订值), 阈值0/-30按clist口径标定, 本次信号仅供"
                           "参考, 严禁直接改判")


def fetch_concepts_from_ftshare(date: str):
    """ftshare 网关兜底: 返回与 clist 接口同构的 [{f12,f14,f3,f62}], 失败返回 []"""
    try:
        import http.client
        import ssl
        try:
            import certifi
            ctx = ssl.create_default_context(cafile=certifi.where())
        except ImportError:
            ctx = ssl.create_default_context()

        def _post(payload, sid=None):
            conn = http.client.HTTPSConnection(FTSHARE_HOST, 443, context=ctx, timeout=60)
            hdrs = {"Content-Type": "application/json",
                    "Accept": "application/json, text/event-stream"}
            if sid:
                hdrs["Mcp-Session-Id"] = sid
            conn.request("POST", FTSHARE_PATH, json.dumps(payload), hdrs)
            r = conn.getresponse()
            body = r.read().decode("utf-8", errors="replace")
            conn.close()
            return r, body

        r0, body = _post({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2025-03-26", "capabilities": {},
            "clientInfo": {"name": "zt_flow_leading", "version": "1.0"}}})
        sid = r0.getheader("Mcp-Session-Id")
        if not sid:
            return []
        _post({"jsonrpc": "2.0", "method": "notifications/initialized"}, sid)

        rows_out = []
        pn = 1
        while pn <= 5:  # 500/页, 504 板块两页即尽; 5 页硬上限防御
            _, body = _post({"jsonrpc": "2.0", "id": 100 + pn, "method": "tools/call",
                             "params": {"name": "ft_v1_eastmoney_sector_flow",
                                        "arguments": {"board_type": "concept",
                                                      "trade_date": date,
                                                      "page": pn, "page_size": 500}}}, sid)
            data = None
            for line in body.splitlines():
                if line.startswith("data:"):
                    try:
                        j = json.loads(line[5:].strip())
                        if j.get("id") == 100 + pn:
                            data = j
                            break
                    except Exception:
                        pass
            if not data:
                break
            text = ((data.get("result") or {}).get("content") or [{}])[0].get("text", "")
            try:
                payload = json.loads(text)
            except Exception:
                break
            arr = payload.get("data") or []
            for x in arr:
                mn = x.get("main_net")
                if mn is None:
                    continue
                try:
                    f62 = float(str(mn).replace(",", "")) * 1e8  # 亿 -> 元, 对齐 clist 口径
                except (TypeError, ValueError):
                    continue
                rows_out.append({"f12": x.get("board_code"), "f14": x.get("board_name"),
                                 "f3": None, "f62": f62})
            if len(arr) < 500:
                break
            pn += 1
            time.sleep(0.3)
        return rows_out
    except Exception:
        return []


def main(date_str: str | None = None):
    date = date_str or datetime.now().strftime("%Y%m%d")
    prev = last_trade_date_before(date)
    rows = fetch_concepts_all()
    source = "push2"
    if not rows:
        rows = fetch_concepts_from_local()
        source = "local_sector_db"
    if not rows:
        rows = fetch_concepts_from_ftshare(date)
        source = "ftshare_gateway"
    out = {"date": date, "prev_trade_date": prev, "source": source,
           "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}
    if source == "ftshare_gateway":
        out["caliber_warning"] = FTSHARE_CALIBER_WARNING
    if source == "local_sector_db":
        # 2026-09-23 凌晨(知微, 承 9/23 02:14 fe947d0 口径分歧呈报+全量自检定谳):
        # 本地初值与 ftshare EM 修订口径存在未裁决分歧(9/22: +1.39 STRONG vs -21.13
        # DANGER, 与尾盘封板结构恶化盘感相符者为修订口径), 按 R22 拆逐日核实前,
        # 下游消费方不得采信本信号单边结论。
        out["caliber_dispute"] = (
            "本地初值口径, 与ftshare EM修订口径存在未裁决分歧(9/22: 初值+1.39亿STRONG vs "
            "修订-21.13亿DANGER, EM T+1修订值系统性偏差已证于9/21对表), R22待拆逐日核实——"
            "下游(盘前作战清单等)不得采信本STRONG单边结论, 须并列修订口径人工研判")
    if not rows:
        out["error"] = "concept board fetch failed"
        print(json.dumps(out, ensure_ascii=False))
        return
    targets = {}
    # 盘中东财 f62 可能返回带千分位逗号的字符串, 盘后为数字; 统一安全转 float(9/22 盘中实测)
    def _f(v):
        try:
            return float(str(v).replace(",", ""))
        except (TypeError, ValueError):
            return 0.0
    for x in rows:
        name = x.get("f14", "")
        if "昨日涨停" in name or "昨日连板" in name or "昨日首板" in name or "昨日打板" in name:
            targets[name] = {
                "main_flow_yi": round(_f(x.get("f62")) / 1e8, 2),
                "chg_pct": x.get("f3"),
            }
    # 综合：昨日涨停板块 = 昨日涨停(+含一字) 的主力净流入
    zt_flow = None
    for k in ("昨日涨停", "昨日涨停_含一字"):
        if k in targets:
            zt_flow = targets[k]["main_flow_yi"]
            break
    out["yesterday_zt_board"] = targets
    out["signal"] = {
        "zt_board_main_flow_yi": zt_flow,
        "strength": ("STRONG" if (zt_flow or 0) > 0 else
                     "NEUTRAL" if (zt_flow or 0) >= -30 else
                     "DANGER"),
        "interpretation": ("打板资金锁仓,次日溢价可期"
                           if (zt_flow or 0) > 0 else
                           "中性兑现" if (zt_flow or 0) >= -30 else
                           "打板资金集体离场,次日追涨谨慎"),
    }
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, f"zt_flow_leading_{date}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)
    print(json.dumps({"date": date, "prev": prev, "yesterday_zt_board": targets,
                      "signal": out["signal"]}, ensure_ascii=False, indent=1))
    print("saved:", path)


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=None)
    a = ap.parse_args()
    main(a.date)