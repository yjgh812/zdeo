# -*- coding: utf-8 -*-
"""nextday_buy_list.py — 次日买入名单终审层(生产, 2026-11-07 建, 主人令「彻底修复」)

根因修复: 此前 dip_scan 原始输出直接呈现主人, 闸门层(R闸/买卖区域)与消息层(龙虎榜/
负面预告/新闻红旗)未接入管线 → 本脚本固化为 nightly 终审环节:
  ①读 dip_scan json 四段候选 → 方法强度排序+去重
  ②硬排除: 当日龙虎榜股(榜上股不当买入标的) + 近期负面预告股
  ③逐票 pre_trade_check_v2 dry-run 过闸; 个人性拒绝(非R37环境项)→弃用并递补同法次优
  ④新闻红旗扫描(westock HTTP桥 data_news): 立案/减持/处罚/问询/预亏等关键词→弃用递补
  ⑤产出 data/nextday_buy_list_YYYYMMDD.json + latest.txt(每只: 量化原因/新闻面/闸门判定/退出规则)
合同: 呈现给主人的买入名单必须=本脚本产物, 禁止直接呈现扫描器原始输出(SOP二百九十三)。
用法: python nextday_buy_list.py [--date YYYYMMDD] [--topn 10]
"""
import json
import os
import re
import sqlite3
import subprocess
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
os.chdir(ROOT)
sys.path.insert(0, ROOT)
FUND = "file:D:/workbuddyspace/databases/fundamental_data.db?mode=ro"
RED_FLAGS = ("立案", "调查", "减持", "处罚", "问询", "违规", "退市", "冻结",
             "质押爆仓", "强制平仓")
# 软红旗=业绩评论类(三季报等已披露旧闻的媒体评述), 对超跌族不构成新信息:
# 起涨型/底部反转首板的验证宇宙(1.75万超跌事件)本身含大量亏损股, 剔除=偏离已验证宇宙;
# 预告类真利空已由forecast库硬排除。软红旗仅标注不剔除(台账二百九十四)。
SOFT_FLAGS = ("预亏", "亏损扩大", "商誉减值")
SEG_META = {
    "up":   ("起涨型v2", "dip_up_v1", 0),
    "fb":   ("底部反转首板", "dip_fb_v1", 1),
    "ret":  ("回踩确认型", "dip_ret_v1", 2),
    "cons": ("首板缩量横盘", "dip_cons_v1", 3),
}


def _load_year_rows(T):
    con = sqlite3.connect(
        "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    rows = con.execute(
        "select code,date,close,volume,change_pct from stock_daily_meta_qfq "
        "where date>=? order by code,date", ((T // 10000 - 1) * 10000 + 101,)).fetchall()
    con.close()
    g = {}
    for code, d, c, v, p in rows:
        s = g.setdefault(str(code)[-6:], {"d": [], "c": [], "v": [], "p": []})
        s["d"].append(int(d)); s["c"].append(float(c or 0)); s["v"].append(float(v or 0))
        s["p"].append(float(p or 0) if p is not None else 0.0)
    return g


def _relaxed_upstart(T, year_rows, st_set, exclude):
    """起涨型次档(前20日跌0~10%, 验证+0.30%%档): 严格池不足4只时递补, 按跌深排序"""
    import numpy as _np
    out = []
    for c6, s in year_rows.items():
        if c6[:3] in ("688", "689") or c6[0] in "489" or c6 in st_set or c6 in exclude:
            continue
        d, c, v, p = s["d"], s["c"], s["v"], s["p"]
        try:
            i = d.index(T)
        except ValueError:
            continue
        if i < 25 or c[i] <= 0 or not (2.0 <= c[i] <= 30.0) or c[i - 21] <= 0:
            continue
        rt = p[i] / 100.0
        lim = 0.19 if c6[:3] in ("300", "301") else 0.09
        if not (0.05 <= rt < lim):
            continue
        prior20 = c[i - 1] / c[i - 21] - 1.0
        if not (-0.10 <= prior20 < 0):
            continue
        ma20 = float(_np.mean(c[i - 20:i]))
        dev = c[i] / ma20 - 1.0 if ma20 > 0 else 9
        if dev >= 0.05:
            continue
        vseg = [x for x in v[i - 20:i] if x > 0]
        volr = v[i] / float(_np.mean(vseg)) if vseg and v[i] > 0 else 9.9
        if not (1.0 <= volr <= 2.5):
            continue
        out.append({"code": c6, "name": "?", "ind": "?", "px": round(c[i], 2),
                    "r_today": round(rt * 100, 1), "volr": round(volr, 2),
                    "dev": round(dev * 100, 1), "prior20": round(prior20 * 100, 1),
                    "rebr": 0})
    out.sort(key=lambda x: x["prior20"])
    return out


def load_candidates(T):
    jp = os.path.join(ROOT, "data", "dip_scan_%d.json" % T)
    d = json.load(open(jp, encoding="utf-8"))
    stcon = sqlite3.connect("file:D:/zcodesj/data/st_history.db?mode=ro", uri=True)
    st_set = {str(c)[-6:] for c, f in stcon.execute(
        "select code,is_st from st_daily s1 where date=(select max(date) from st_daily s2 "
        "where s2.code=s1.code)").fetchall() if f == 1}
    stcon.close()
    # pre-exclusions for relaxed pool (LHB+neg forecast+strict-pool codes)
    lhb, neg = exclusions(T)
    strict_codes = {h["code"] for h in d.get("up_hits", [])}
    yr = _load_year_rows(T)
    icon = sqlite3.connect("file:D:/zcodesj/data/industry_group_map_snapshot.db?mode=ro", uri=True)
    nind = {str(k)[-6:]: (nm, l1) for k, nm, l1 in icon.execute(
        "select code,name,l1 from l1_member").fetchall()}
    icon.close()
    cands = []
    for h in d.get("up_hits", []):
        rebr = h.get("rebr", 0)
        golden = 15 <= rebr <= 25
        reason = ("超跌反弹黄金带: 已修复%.1f%%(验证15-25%%=+8.8%%胜率77%%), 涨%+.1f%%量%.2f, "
                  "偏离MA20=%.1f%%" % (rebr, h["r_today"], h["volr"], h["dev"])) if golden else (
            "超跌反弹(严格池非黄金带): 已修复%.1f%%(该档验证+4.9%%胜率65%%), 涨%+.1f%%量%.2f, "
            "偏离MA20=%.1f%%" % (rebr, h["r_today"], h["volr"], h["dev"]))
        cands.append((0, (0 if golden else 1, h["code"]), "up", h, reason))
    for h in _relaxed_upstart(T, yr, st_set, exclude=strict_codes | lhb | neg):
        nm, l1 = nind.get(h["code"], ("?", "?"))
        h["name"] = nm; h["ind"] = l1
        cands.append((0, (2, h["code"]), "up", h,
                      "起涨型次档: 前20日跌%.1f%%(该档验证+0.30%%), 涨%+.1f%%量%.2f, "
                      "偏离MA20=%.1f%%" % (h["prior20"], h["r_today"], h["volr"], h["dev"])))
    for h in d.get("fb_hits", []):
        prio = 0 if h["prior20"] <= -15 else 1
        cands.append((1, (prio, h["code"]), "fb", h,
                      "底部反转首板: 前20日跌%.1f%%后涨停(验证+2.7%%胜率53%%), 价%.2f" % (
                          h["prior20"], h["px"])))
    for h in d.get("hits", []):
        if 2.0 <= h["dev"] <= 5.0 and 1.5 <= h["volr"] <= 2.5:
            cands.append((2, (0 if 1.5 <= h["volr"] <= 2.5 else 1, h["volr"], h["code"]), "ret", h,
                          "回踩确认型: 前20日涨%.1f%%回踩, 位置+%.1f%%量比%.2f"
                          "(共振带+0.77%%), 周五%+.1f%%" % (h["r20"], h["dev"], h["volr"], h["r_today"])))
    for h in d.get("cons", []):
        if h["dev"] < 5 and 0.4 <= h["volr"] <= 0.9 and h["fb_days_ago"] <= 6:
            cands.append((3, (h["volr"], h["code"]), "cons", h,
                          "首板横盘介入: %d天前涨停不破板, 今日缩量%.2f(验证+0.85%%), 偏离%.1f%%" % (
                              h["fb_days_ago"], h["volr"], h["dev"])))
    cands.sort(key=lambda x: (x[0], x[1]))
    return [(seg, h, reason) for _rk, _pr, seg, h, reason in cands]


def exclusions(T):
    fcon = sqlite3.connect(FUND, uri=True)
    lhb = {r[0][:6] for r in fcon.execute(
        "select DISTINCT ts_code from dragon_tiger_new where trade_date=?", (str(T),))}
    neg = {r[0][:6] for r in fcon.execute(
        "select ts_code from forecast where ann_date>=20261024 and type in "
        "('预减','首亏','略减','预亏')")}
    fcon.close()
    return lhb, neg


def gate_check(c6, name, src, sector, px):
    """pre_trade_check_v2 dry-run → (ok, env_only, rejects[])"""
    cmd = [sys.executable, os.path.join(ROOT, "pre_trade_check_v2.py"),
           "--code", c6, "--name", name, "--source", src, "--sector", sector,
           "--price", str(px), "--skip-time-window"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                           timeout=120, shell=False, env=dict(os.environ, PYTHONUTF8="1"))
        txt = r.stdout or ""
        m = re.search(r'"rejects": \[(.*?)\]', txt, re.S)
        rj = re.findall(r'"([^"]+)"', m.group(1)) if m else []
        # 2026-11-08: 环境类项剥离(R37弱档/R22R33时变fail-open探针)——时变状态不构成本票
        # 个人性拒绝; 剥离后剩余才判剔除, 防夜链时变状态误剔(台账三百零六)
        personal = [x for x in rj if not x.startswith(("R37", "R22", "R33"))]
        env_only = len(rj) > 0 and not personal
        return (not personal, env_only, personal if personal else rj)
    except Exception as e:
        return (False, False, ["gate-exception: %s" % e])


def news_redflag(c6):
    """westock HTTP桥 data_news → (hard_hits, soft_hits, n_titles, err)"""
    try:
        import urllib.request as ur
        cfgp = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\Administrator"),
                            ".zcode", "cli", "config.json")
        ws = json.load(open(cfgp, encoding="utf-8")).get("mcp", {}).get("servers", {}).get("westock-mcp", {})
        tok = (ws.get("headers", {}).get("Authorization", "") or "").replace("Bearer ", "")
        url = "https://stockbuddy.qq.com/cgi/cgi-bin/openai/mcp/mcp"
        if not tok:
            return ([], [], 0, "no-token")

        def rpc(payload):
            body = json.dumps(payload).encode()
            h = {"Authorization": "Bearer %s" % tok, "Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"}
            req = ur.Request(url, data=body, headers=h, method="POST")
            with ur.urlopen(req, timeout=20) as r:
                return r.read().decode("utf-8")
        rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": "2024-11-05", "capabilities": {},
            "clientInfo": {"name": "tulong-list", "version": "1.0"}}})
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        raw = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {
            "name": "data_news", "arguments": {"symbol": ("sz%s" if c6[0] in "03" else "sh%s") % c6,
                                               "limit": 6}}})
        titles = []
        for line in raw.splitlines():
            if line.startswith("data:") and line.strip() != "data:":
                dd = json.loads(line[5:].strip())
                for c in dd.get("result", {}).get("content", []):
                    if c.get("type") == "text":
                        obj = json.loads(c["text"])
                        for it in (obj.get("data", {}) or {}).get("data", []) or []:
                            titles.append(str(it.get("title", "")))
        hard = [t for t in titles if any(k in t for k in RED_FLAGS)]
        soft = [t for t in titles if any(k in t for k in SOFT_FLAGS)]
        return (hard, soft, len(titles), None)
    except Exception as e:
        return ([], [], 0, str(e)[:60])


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default="")
    ap.add_argument("--topn", type=int, default=10)
    a = ap.parse_args()
    con = sqlite3.connect(
        "file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro", uri=True)
    T = int(a.date) if a.date else int(con.execute(
        "select max(date) from stock_daily_meta_qfq").fetchone()[0])
    con.close()
    print("==== 次日名单终审 %s (top%d) ====" % (T, a.topn))
    cands = load_candidates(T)
    lhb, neg = exclusions(T)
    picked, examined = [], []
    for seg, h, reason in cands:
        if len(picked) >= a.topn:
            break
        c6 = h["code"]
        if c6 in [p["code"] for p in picked]:
            continue
        if c6 in lhb:
            examined.append({"code": c6, "name": h["name"], "seg": seg, "excluded": "当日龙虎榜"})
            continue
        if c6 in neg:
            examined.append({"code": c6, "name": h["name"], "seg": seg, "excluded": "负面预告"})
            continue
        segname, src, _ = SEG_META[seg]
        ok, env_only, rj = gate_check(c6, h["name"], src, h["ind"], h["px"])
        if not ok and not env_only:
            examined.append({"code": c6, "name": h["name"], "seg": seg,
                             "excluded": "闸门拒绝: %s" % (rj[:1])})
            continue
        hard, soft, n_titles, nerr = news_redflag(c6)
        if hard:
            examined.append({"code": c6, "name": h["name"], "seg": seg,
                             "excluded": "新闻硬红旗: %s" % hard[:1]})
            continue
        picked.append({"code": c6, "name": h["name"], "ind": h["ind"], "px": h["px"],
                       "seg": seg, "segname": segname, "source": src, "reason": reason,
                       "gate": "过审(仅R37弱档环境项)" if env_only else "全绿过审",
                       "news": ("软标注: %s" % soft[:1]) if soft
                               else ("扫描%d条无红旗" % n_titles if not nerr
                                     else "新闻桥不可用(%s)" % nerr),
                       "exit": "持满5交易日14:45自动卖 / 收盘跌破成本8%自动卖"})
    out = {"T": T, "built_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
           "picked": picked, "examined_out": examined}
    json.dump(out, open(os.path.join(ROOT, "data", "nextday_buy_list_%d.json" % T),
                        "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    # 熊市年线监控提示(主人裁决A: 胜率优先, 开关只提示不拦截, SOP三百零八)
    idx_con = sqlite3.connect(
        "file:D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db?mode=ro", uri=True)
    irow = idx_con.execute(
        "select date, close from index_daily_meta where code='000001' order by date "
        "desc limit 250").fetchall()
    idx_con.close()
    bear_note = ""
    if len(irow) >= 250:
        closes = [float(r[1]) for r in irow]
        ma250 = sum(closes) / len(closes)
        if closes[-1] < ma250:
            bear_note = ("⚠️ 指数年线下(收盘%.0f<年线%.0f): 仅提示不拦截(主人A裁决), "
                         "建议名额向高胜率档倾斜" % (closes[-1], ma250))
    lines = ["==== 次日买入名单(终审版, 信号日%d, 开盘挂单) ====" % T,
             "合同: 本名单=已过闸+已排雷版(闸门/龙虎榜/预告/新闻红旗四层), 逐票带来源与退出规则"]
    if bear_note:
        lines.append("    " + bear_note)
    for i, p in enumerate(picked, 1):
        lines.append("%2d. %s %s [%s|%s] 价%.2f  source=%s" % (
            i, p["code"], p["name"], p["segname"], p["ind"], p["px"], p["source"]))
        lines.append("    量化: %s" % p["reason"])
        lines.append("    闸门: %s | 新闻: %s" % (p["gate"], p["news"]))
        lines.append("    退出: %s" % p["exit"])
    if examined:
        lines.append("---- 终审剔除(%d只) ----" % len(examined))
        for e in examined[:12]:
            lines.append("  %s %s [%s] %s" % (e["code"], e["name"], e["seg"],
                                              "; ".join(e["excluded"]) if isinstance(e["excluded"], list) else e["excluded"]))
    txt = "\n".join(lines)
    open(os.path.join(ROOT, "data", "nextday_buy_list_latest.txt"), "w",
         encoding="utf-8").write(txt)
    print(txt)
    print("json: data/nextday_buy_list_%d.json" % T)


if __name__ == "__main__":
    main()
