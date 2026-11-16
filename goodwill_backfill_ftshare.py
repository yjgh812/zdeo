# -*- coding: utf-8 -*-
"""商誉 PIT 历史落库 (ftshare MCP HTTP 网关直调)
主人「现在就执行」授权 (2026-09-13, SOP(二十一)⑧ 路径B落地)。
数据源: market.ft.tech/gateway/mcp tools/call ft_v1_goodwill_stock_detail
  字段: security_code/security_name/goodwill_scale/goodwill_previous/
        goodwill_to_net_assets_ratio/net_profit_scale/notice_date(真实公告日=PIT锚)/trade_board
落库: event_calendar.db goodwill_stock_hist (PK code+report_date, 幂等 UPSERT)
  pit_avail_date = notice_date (真实公告日, 优于法定滞后推导)
用法:
  python goodwill_backfill_ftshare.py --test          # 单期(20251231)验证
  python goodwill_backfill_ftshare.py --full          # 2016Q1~2026Q2 全量(幂等续跑)
  python goodwill_backfill_ftshare.py --overlap       # 已落库数据×bp_ep池重叠率
限速: 调用间 0.6s; session 300s TTL 自动重握手 (照 ftshare_mcp_proxy 逻辑)
"""
import argparse
import http.client
import json
import sqlite3
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path

REMOTE_HOST = "market.ft.tech"
REMOTE_PATH = "/gateway/mcp"
HTTP_TIMEOUT = 120
SLEEP_BETWEEN = 0.6
DB = Path(r"D:\zcodesj\event_calendar.db")
TABLE = "goodwill_stock_hist"
TRADES_CSV = Path(r"D:\zcodesj\reports\drift_check_20260912e_0911end\bp_ep_core_trades_full.csv")

REPORT_DATES = [f"{y}{q}" for y in range(2016, 2027)
                for q in ("0331", "0630", "0930", "1231")
                if not (y == 2026 and q not in ("0331", "0630"))]


class McpHttp:
    """最小 MCP streamable-http 客户端 (300s TTL 自动重握手, SSE 解析)。"""

    def __init__(self):
        self.sid = None
        self.sid_ts = 0.0
        self.req_id = 0

    def _conn(self):
        ctx = ssl.create_default_context()
        return http.client.HTTPSConnection(REMOTE_HOST, 443, context=ctx, timeout=HTTP_TIMEOUT)

    def _post(self, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers = {"Content-Type": "application/json",
                   "Accept": "application/json, text/event-stream",
                   "Content-Length": str(len(body))}
        if self.sid:
            headers["Mcp-Session-Id"] = self.sid
        conn = self._conn()
        try:
            conn.request("POST", REMOTE_PATH, body=body, headers=headers)
            resp = conn.getresponse()
            new_sid = resp.getheader("Mcp-Session-Id")
            if new_sid:
                self.sid = new_sid
                self.sid_ts = time.time()
            raw = resp.read().decode("utf-8", errors="replace")
            status = resp.status
        finally:
            conn.close()
        if status != 200:
            raise RuntimeError(f"HTTP {status}: {raw[:200]}")
        # SSE 流解析: 取最后一个 data: 行的 JSON
        if "text/event-stream" in raw[:200] or raw.startswith("event:") or "\ndata:" in raw or raw.startswith("data:"):
            msgs = []
            for line in raw.splitlines():
                if line.startswith("data:"):
                    seg = line[5:].strip()
                    if seg:
                        try:
                            msgs.append(json.loads(seg))
                        except json.JSONDecodeError:
                            pass
            for m in msgs:
                if "result" in m or "error" in m:
                    return m
            return msgs[-1] if msgs else None
        return json.loads(raw)

    def _ensure_session(self):
        if self.sid and time.time() - self.sid_ts < 240:
            return
        self.req_id += 1
        r = self._post({"jsonrpc": "2.0", "id": self.req_id, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05",
                                   "capabilities": {},
                                   "clientInfo": {"name": "goodwill-backfill", "version": "1.0"}}})
        if r is None or "result" not in r:
            raise RuntimeError(f"initialize 失败: {str(r)[:200]}")
        try:
            self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})
        except Exception:
            pass
        print(f"  [session] 握手 OK sid={str(self.sid)[:16]}…", flush=True)

    def call(self, tool, args):
        for attempt in (1, 2):
            self._ensure_session()
            self.req_id += 1
            try:
                r = self._post({"jsonrpc": "2.0", "id": self.req_id,
                                "method": "tools/call",
                                "params": {"name": tool, "arguments": args}})
            except RuntimeError as e:
                if attempt == 1 and "Session not found" in str(e):
                    self.sid = None
                    continue
                raise
            if r and "error" in r:
                raise RuntimeError(f"tool error: {str(r['error'])[:200]}")
            result = r.get("result", {}) if r else {}
            sc = result.get("structuredContent")
            if sc and "data" in sc:
                return sc
            # 回退: text 内容里找 JSON
            for c in result.get("content", []):
                if c.get("type") == "text":
                    try:
                        j = json.loads(c["text"])
                        if "data" in j:
                            return j
                    except json.JSONDecodeError:
                        pass
            return result
        raise RuntimeError("unreachable")


def ensure_table(con):
    con.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            code TEXT NOT NULL,
            security_name TEXT,
            report_date TEXT NOT NULL,
            notice_date TEXT,
            goodwill_scale REAL,
            goodwill_previous REAL,
            gw_to_net_assets REAL,
            net_profit_scale REAL,
            trade_board TEXT,
            pit_avail_date TEXT,
            updated_at TEXT,
            PRIMARY KEY (code, report_date)
        )""")


def fetch_period(mcp, rd, con=None):
    """按 pages 元数据循环到上限; 每页即时落盘。
    服务端实测坑: 每页硬上限 150 行, pages 元数据按请求 page_size 计算——
    page_size=500→pages=6×150=900 拉不全 / page_size=250→1650<2675 拉不全 /
    page_size=150 与硬上限一致→pages=18 全量可达 (2675 实锚 20251231 期)。"""
    page, total_rows = 1, 0
    while True:
        sc = mcp.call("ft_v1_goodwill_stock_detail", {"date": rd, "page": page, "page_size": 150})
        data = sc.get("data", [])
        pages = sc.get("metadata", {}).get("pagination", {}).get("pages")
        if data and con is not None:
            upsert(con, rd, data)
            con.commit()
        total_rows += len(data)
        if not data or (pages is not None and page >= pages):
            break
        page += 1
        time.sleep(SLEEP_BETWEEN)
    return total_rows


def upsert(con, rd, rows):
    now = datetime.now().isoformat(timespec="seconds")
    for r in rows:
        code = (r.get("security_code") or "").strip()
        if not code:
            continue
        code6 = code[:6]
        notice = (r.get("notice_date") or "")[:10] or None
        con.execute(f"""INSERT INTO {TABLE}
            (code, security_name, report_date, notice_date, goodwill_scale, goodwill_previous,
             gw_to_net_assets, net_profit_scale, trade_board, pit_avail_date, updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(code, report_date) DO UPDATE SET
              security_name=excluded.security_name, notice_date=excluded.notice_date,
              goodwill_scale=excluded.goodwill_scale, goodwill_previous=excluded.goodwill_previous,
              gw_to_net_assets=excluded.gw_to_net_assets, net_profit_scale=excluded.net_profit_scale,
              trade_board=excluded.trade_board, pit_avail_date=excluded.pit_avail_date,
              updated_at=excluded.updated_at""",
            (code6, r.get("security_name"), rd, notice,
             float(r["goodwill_scale"]) if r.get("goodwill_scale") not in (None, "") else None,
             float(r["goodwill_previous"]) if r.get("goodwill_previous") not in (None, "") else None,
             float(r["goodwill_to_net_assets_ratio"]) if r.get("goodwill_to_net_assets_ratio") not in (None, "") else None,
             float(r["net_profit_scale"]) if r.get("net_profit_scale") not in (None, "") else None,
             r.get("trade_board"), notice, now))


def overlap(bp_codes_by_year=None):
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    # bp_ep 实际买入标的 (回测全史)
    import csv
    with open(TRADES_CSV, encoding="utf-8-sig") as f:
        codes = {r["股票代码"][:6] for r in csv.DictReader(f)}
    rows = con.execute(f"""SELECT code, report_date, gw_to_net_assets FROM {TABLE}
                            WHERE gw_to_net_assets IS NOT NULL AND gw_to_net_assets > 0
                            AND code IN ({','.join('?' * len(codes))})""",
                       tuple(codes)).fetchall()
    # 各报告期覆盖率 (连接关闭前取)
    cov = con.execute(f"SELECT report_date, COUNT(*) FROM {TABLE} GROUP BY report_date ORDER BY report_date").fetchall()
    con.close()
    print(f"bp_ep 交易池 unique 标的: {len(codes)} | 有商誉记录的池内标的: {len({r[0] for r in rows})}")
    for theta in (0.30, 0.50):
        hit = {r[0] for r in rows if r[2] >= theta}
        print(f"  商誉/净资产 >= {theta*100:.0f}%: 池内命中 {len(hit)} 只 = {len(hit)/len(codes)*100:.1f}% 重叠率")
        if hit:
            hit_recent = [(r[0], r[1], f"{r[2]*100:.0f}%") for r in rows if r[0] in hit and r[1] >= "20241231"]
            hit_recent.sort(key=lambda x: -float(x[2][:-1]))
            for c, rd_, g in hit_recent[:8]:
                print(f"    {c} @ {rd_}: {g}")
    print(f"落库覆盖率: {len(cov)} 期, 首期 {cov[0] if cov else 'N/A'} 末期 {cov[-1] if cov else 'N/A'}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--test", action="store_true")
    ap.add_argument("--full", action="store_true")
    ap.add_argument("--overlap", action="store_true")
    args = ap.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    if args.overlap:
        overlap()
        return
    periods = ["20251231"] if args.test else REPORT_DATES
    mcp = McpHttp()
    mcp._ensure_session()
    con = sqlite3.connect(DB)
    ensure_table(con)
    done = {r[0] for r in con.execute(f"SELECT DISTINCT report_date FROM {TABLE}")}
    t0 = time.time()
    ok_periods = 0
    for i, rd in enumerate(periods, 1):
        if rd in done and not args.test:
            print(f"[{i}/{len(periods)}] {rd} 已落库跳过 (幂等)")
            continue
        try:
            total = fetch_period(mcp, rd, con)
        except Exception as e:
            print(f"[{i}/{len(periods)}] {rd} 失败: {str(e)[:120]} (已落页保留, 可重跑幂等)", flush=True)
            time.sleep(2)
            continue
        con.commit()
        ok_periods += 1
        print(f"[{i}/{len(periods)}] {rd}: {total} 行落库 累计耗时 {time.time()-t0:.0f}s", flush=True)
        time.sleep(SLEEP_BETWEEN)
    con.close()
    print(f"完成: {ok_periods} 期新落库, 总耗时 {time.time()-t0:.0f}s")


if __name__ == "__main__":
    main()
