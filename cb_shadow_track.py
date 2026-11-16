#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cb_shadow_track.py — 「可转债双低轮动」prereg影子跟踪器（2026-09-25 主人"继续"批准立项，路径A前向影子模式）

⛔⛔⛔ TERMINATED 2026-09-25: 主人令「我们不做可转债」——本脚本及全链已终止, 禁止运行/调度/接线。
裁决标注=data/CB_DOUBLELOW_TERMINATED_20260925.json。保留仅为git历史与研究报告引用完整性。⛔⛔⛔

prereg = data/prereg_cb_doublelow_shadow_v1.json (指纹 26792bae163b3e44, 跑前冻结)
背景: L3首站(9/24方案书冻结)原定tushare月截面数据路径被实测证伪(接口无权限), 主人裁决改前向影子模式。
口径: 月末最后交易日 xiaoshi快照 双低=价格+溢价率*100 升序Top10等权; 次月首交易日开盘买入(ftshare,
next-bar); 下一调仓日收盘卖出; 双边万1。影子=零交易权只记账(直跑制, 无schtask, 调仓日由agent驱动)。

数据流(混合模式: agent拉截面, 脚本自治计算):
  1. agent 月末MCP分页拉xiaoshi快照 -> data/cb_snapshot/YYYYMMDD.jsonl (文件名即PIT日期)
  2. python cb_shadow_track.py --review YYYYMMDD  -> 输出双低Top30候选 data/cb_review/YYYYMMDD_candidates.json
  3. agent 用westock data_bond批量查候选评级/规模/到期日 -> data/cb_review/YYYYMMDD_westock.jsonl
  4. python cb_shadow_track.py --signal YYYYMMDD  -> 全过滤后Top10入仓 data/cb_shadow_positions.jsonl
  5. 次调仓日: agent拉ftshare开盘价(次月首交易日)与收盘价 -> data/cb_prices/YYYYMMDD.jsonl
     python cb_shadow_track.py --settle YYYYMMDD  -> 结算上一期仓位 data/cb_shadow_settled.jsonl
     再走 2-4 开新仓
用法:
  python cb_shadow_track.py --review 20260928
  python cb_shadow_track.py --signal 20260928
  python cb_shadow_track.py --settle 20261030   # 10月末调仓日: 先结算9月仓再开10月仓
  python cb_shadow_track.py --status
gate(prereg冻结, 影子期满12个月): 年化>股票腿同期 且 年化>8% 且 12月中>=7月正 且 MDD<=20% 且 月度NW-t>=2
→ 方可提请主人复议入场。
"""
import argparse, json, os, sys, glob

ROOT = r"D:\zcodesj"
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

SNAP_DIR = os.path.join(ROOT, "data", "cb_snapshot")
REVIEW_DIR = os.path.join(ROOT, "data", "cb_review")
PRICE_DIR = os.path.join(ROOT, "data", "cb_prices")
POSITIONS = os.path.join(ROOT, "data", "cb_shadow_positions.jsonl")
SETTLED = os.path.join(ROOT, "data", "cb_shadow_settled.jsonl")
PREREG = os.path.join(ROOT, "data", "prereg_cb_doublelow_shadow_v1.json")

FINGERPRINT = "26792bae163b3e44"
COST_BPS = 1.0          # 双边万1
TOP_N = 10
REVIEW_N = 60  # 9/18 dry-run实证: 双低前排富集临期券(20/30剩余<1年), Top30过五重过滤仅7只; 加深复核缓冲(工程参数, prereg信号定义/判据不变)
MIN_RATING = "AA-"      # 评级下限(含), 低于或缺失剔除
MIN_SIZE_YI = 2.0       # 规模下限(亿元, 含)
CONV_VALUE_MAX = 130.0  # 强赎带: 转股价值>=130剔除
RATING_ORDER = {"AAA": 20, "AA+": 19, "AA": 18, "AA-": 17, "A+": 16, "A": 15,
                "A-": 14, "BBB+": 13, "BBB": 12, "BBB-": 11}


def die(msg, code=2):
    print(f"[CB-SHADOW][ERROR] {msg}")
    sys.exit(code)


def load_prereg():
    if not os.path.exists(PREREG):
        die(f"prereg缺失: {PREREG}")
    p = json.load(open(PREREG, encoding="utf-8"))
    if p.get("fingerprint") != FINGERPRINT:
        die(f"prereg指纹不符: 文件={p.get('fingerprint')} 预期={FINGERPRINT} (禁改冻结文件, 需新指纹独立prereg)")
    return p


def load_jsonl(path):
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def append_jsonl(path, rows):
    with open(path, "a", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def snapshot_path(yyyymmdd):
    p = os.path.join(SNAP_DIR, f"{yyyymmdd}.jsonl")
    if not os.path.exists(p):
        die(f"快照缺失: {p} (agent须先MCP分页拉存)")
    rows = [r for r in load_jsonl(p) if "code" in r]  # 跳过兜底源meta行(以_source等非code键开头)
    if len(rows) < 100:
        die(f"快照行数异常: {p} 仅{len(rows)}行(<100), 疑拉取不完整")
    return p, rows


def parse_snapshot_row(r):
    """统一字段: 兼容xiaoshi_quant_data的items结构。"""
    raw = {}
    try:
        raw = json.loads(r.get("raw_json") or "{}")
    except Exception:
        raw = {}
    def fnum(*keys):
        for k in keys:
            v = r.get(k, raw.get(k))
            if v is None or v == "-" or v == "":
                continue
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
        return None
    return {
        "cb_code": str(r.get("code") or raw.get("转债代码") or ""),
        "cb_name": r.get("name") or raw.get("转债名称") or "",
        "price": fnum("bond_price", "latest_price", "转债最新价"),
        "premium": fnum("conversion_premium", "转股溢价率"),
        "conv_value": fnum("conversion_value", "转股价值"),
        "conv_start": str(r.get("conversion_start_date") or raw.get("开始转股日") or ""),
        "stock_code": str(r.get("stock_code") or raw.get("正股代码") or ""),
        "stock_name": r.get("stock_name") or raw.get("正股名称") or "",
        "rating": r.get("credit_rating") or raw.get("评级") or None,
        "issue_size": fnum("issue_size"),
    }


def base_filter(s):
    """快照内可做的过滤(xiaoshi字段); 评级/规模/剩余期限在westock复核步。返回None=通过, str=剔除原因。"""
    if not s["cb_code"]:
        return "无代码"
    if s["cb_code"].startswith("118"):
        return "科创板转债(屠龙铁律禁科创板,主人9/23令)"  # 20260925铁律一致性落地
    if s["price"] is None or s["price"] <= 0:
        return "无价格(未上市/停牌)"
    if s["premium"] is None:
        return "无溢价率"
    if s["conv_value"] is not None and s["conv_value"] >= CONV_VALUE_MAX:
        return f"强赎带(转股价值{s['conv_value']:.0f}>=130)"
    if "ST" in (s["stock_name"] or "").upper():
        return f"正股ST({s['stock_name']})"
    if s["conv_start"]:
        cs = s["conv_start"].replace("-", "")
        if len(cs) == 8 and cs.isdigit() and cs > "".join(s["_sig_ymd"]):
            return f"转股期未开始({cs})"
    return None


def cmd_review(yyyymmdd):
    load_prereg()
    spath, rows = snapshot_path(yyyymmdd)
    cands = []
    for r in rows:
        s = parse_snapshot_row(r)
        s["_sig_ymd"] = yyyymmdd
        why = base_filter(s)
        if s["price"] is None or s["premium"] is None:
            continue
        s["double_low"] = s["price"] + s["premium"]  # 溢价率单位=百分数, *100即与价格同量纲
        s["filter_reason"] = why
        cands.append(s)
    passed = sorted([c for c in cands if c["filter_reason"] is None], key=lambda x: x["double_low"])
    top = passed[:REVIEW_N]
    out = {
        "signal_date": yyyymmdd, "snapshot": spath, "snapshot_rows": len(rows),
        "parsed": len(cands), "base_passed": len(passed),
        "top_candidates": [{k: c[k] for k in ("cb_code", "cb_name", "price", "premium",
                                              "double_low", "stock_code", "stock_name")} for c in top],
        "review_ask": "agent用westock data_bond逐批查: issueCompanyRating/latestCompanyRating/size/dueDate -> 存 data/cb_review/%s_westock.jsonl (每行含cb_code+rating+size_yuan+due_date)" % yyyymmdd,
        "fingerprint": FINGERPRINT,
    }
    os.makedirs(REVIEW_DIR, exist_ok=True)
    op = os.path.join(REVIEW_DIR, f"{yyyymmdd}_candidates.json")
    json.dump(out, open(op, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(f"[REVIEW] {yyyymmdd}: 快照{len(rows)}行, 过滤后{len(passed)}只, Top{REVIEW_N}候选已存 {op}")
    print(f"  双低分位: Top10均值={sum(c['double_low'] for c in passed[:10])/min(10,len(passed)):.1f} "
          f"全市场中位={sorted(c['double_low'] for c in passed)[len(passed)//2]:.1f}")
    for c in top[:TOP_N]:
        print(f"  {c['cb_code']} {c['cb_name']:<8} 价{c['price']:.2f} 溢{c['premium']:.1f}% 双低{c['double_low']:.1f} 正股{c['stock_name']}")
    return 0


def norm_rating(r):
    """评级宽松解析: 兼容'AAAsti'/'AA+sti'等变体(取前缀匹配标准档)。返回档位序号或None。"""
    r = (r or "").strip().upper()
    for k in ("AAA", "AA+", "AA-", "AA", "A+", "A-", "A",
              "BBB+", "BBB-", "BBB", "BB+", "BB-", "BB", "B+", "B-", "B"):
        if r.startswith(k):
            return RATING_ORDER.get(k)
    return None


def cmd_signal(yyyymmdd):
    load_prereg()
    cpath = os.path.join(REVIEW_DIR, f"{yyyymmdd}_candidates.json")
    wpath = os.path.join(REVIEW_DIR, f"{yyyymmdd}_westock.jsonl")
    if not os.path.exists(cpath):
        die(f"候选文件缺失: {cpath} (先跑 --review {yyyymmdd})")
    if not os.path.exists(wpath):
        die(f"westock复核文件缺失: {wpath} (agent须先批量查评级/规模/到期日)")
    cand = {c["cb_code"]: c for c in json.load(open(cpath, encoding="utf-8"))["top_candidates"]}
    review = {}
    for r in load_jsonl(wpath):
        if r.get("cb_code"):
            review[r["cb_code"]] = r
    # 若have文件已含该signal_date(重复运行防护)
    have = {p["signal_date"] for p in load_jsonl(POSITIONS)}
    if yyyymmdd in have:
        print(f"[SIGNAL] {yyyymmdd} 已在仓台账, 幂等跳过")
        return 0
    picks, drops = [], []
    for code, c in cand.items():
        w = review.get(code)
        if w is None:
            drops.append((code, c["cb_name"], "westock复核缺失"))
            continue
        rating = (w.get("rating") or w.get("latest_rating") or "").strip()
        ro = norm_rating(rating)
        if ro is None or ro < RATING_ORDER[MIN_RATING]:
            drops.append((code, c["cb_name"], f"评级不达{MIN_RATING}({rating or '缺失'})"))
            continue
        size_yi = w.get("size_yuan")
        if size_yi is not None and float(size_yi) / 1e8 < MIN_SIZE_YI:
            drops.append((code, c["cb_name"], f"规模{float(size_yi)/1e8:.2f}亿<{MIN_SIZE_YI}亿"))
            continue
        due = str(w.get("due_date") or "").replace("-", "")
        if len(due) >= 8 and due[:8] <= f"{int(yyyymmdd[:4])+1}{yyyymmdd[4:]}":
            drops.append((code, c["cb_name"], f"剩余期限<1年(到期{due[:8]})"))
            continue
        picks.append({**c, "rating": rating, "size_yi": round(float(size_yi)/1e8, 2) if size_yi else None,
                      "due_date": due})
    picks = sorted(picks, key=lambda x: x["double_low"])[:TOP_N]
    if len(picks) < TOP_N:
        die(f"过滤后仅{len(picks)}只<Top{TOP_N}, 拒绝降标准开仓(候选{len(cand)}, 剔除{len(drops)})")
    rec = {
        "signal_date": yyyymmdd, "fingerprint": FINGERPRINT,
        "positions": [{"cb_code": p["cb_code"], "cb_name": p["cb_name"], "stock_name": p["stock_name"],
                       "signal_close": p["price"], "double_low": p["double_low"], "rating": p["rating"]}
                      for p in picks],
        "entry_due": "次月首个交易日开盘买入(ftshare), 买入价待 data/cb_prices/ 次月文件回填",
        "dropped": drops,
    }
    append_jsonl(POSITIONS, [rec])
    print(f"[SIGNAL] {yyyymmdd}: Top{TOP_N}入仓台账 {POSITIONS}")
    for p in picks:
        print(f"  {p['cb_code']} {p['cb_name']:<8} 价{p['price']:.2f} 双低{p['double_low']:.1f} {p['rating']} 规模{p['size_yi']}亿")
    print(f"  剔除{len(drops)}只: " + "; ".join(f"{c}:{r}" for c, n, r in drops[:8]) + ("..." if len(drops) > 8 else ""))
    return 0


def cmd_settle(yyyymmdd):
    """结算: 需 data/cb_prices/YYYYMMDD.jsonl 每行{cb_code, prev_open(次月首交易日开盘), close(本调仓日收盘)}"""
    load_prereg()
    ppath = os.path.join(PRICE_DIR, f"{yyyymmdd}.jsonl")
    if not os.path.exists(ppath):
        die(f"价格文件缺失: {ppath} (agent须先拉ftshare: 上期开仓月首交易日开盘价+本日收盘价)")
    px = {r["cb_code"]: r for r in load_jsonl(ppath)}
    pos_rows = load_jsonl(POSITIONS)
    open_row = None
    for r in pos_rows:
        if r["signal_date"] < yyyymmdd and (open_row is None or r["signal_date"] > open_row["signal_date"]):
            open_row = r
    if open_row is None:
        print(f"[SETTLE] {yyyymmdd}: 无待结算仓位")
        return 0
    have_keys = {(s["signal_date"], s["settle_date"]) for s in load_jsonl(SETTLED)}
    if (open_row["signal_date"], yyyymmdd) in have_keys:
        print(f"[SETTLE] {open_row['signal_date']}->{yyyymmdd} 已结算, 幂等跳过")
        return 0
    rets, missing = [], []
    for p in open_row["positions"]:
        q = px.get(p["cb_code"])
        if q is None or not q.get("prev_open") or not q.get("close"):
            missing.append(p["cb_code"])
            continue
        gross = float(q["close"]) / float(q["prev_open"]) - 1
        rets.append({"cb_code": p["cb_code"], "cb_name": p["cb_name"], "entry_open": float(q["prev_open"]),
                     "exit_close": float(q["close"]), "gross_ret": gross,
                     "net_ret": gross - 2 * COST_BPS / 10000})
    if missing:
        die(f"价格缺失{len(missing)}只: {missing} (禁止部分结算, 补齐价格文件后重跑)")
    port = sum(r["net_ret"] for r in rets) / len(rets)
    rec = {"signal_date": open_row["signal_date"], "settle_date": yyyymmdd, "fingerprint": FINGERPRINT,
           "n": len(rets), "port_net_ret": port, "legs": rets}
    append_jsonl(SETTLED, [rec])
    print(f"[SETTLE] {open_row['signal_date']}->{yyyymmdd}: {len(rets)}只, 组合净收益={port*100:+.2f}% (双边万{COST_BPS:.0f})")
    for r in sorted(rets, key=lambda x: -x["net_ret"]):
        print(f"  {r['cb_code']} {r['cb_name']:<8} {r['entry_open']:.2f}->{r['exit_close']:.2f} 净{r['net_ret']*100:+.2f}%")
    return 0


def cmd_status():
    load_prereg()
    pos = load_jsonl(POSITIONS)
    st = load_jsonl(SETTLED)
    snaps = sorted(os.path.basename(p) for p in glob.glob(os.path.join(SNAP_DIR, "*.jsonl")))
    print(f"[STATUS] prereg指纹={FINGERPRINT} 影子起始={json.load(open(PREREG,encoding='utf-8')).get('shadow_start')}")
    print(f"  快照文件: {snaps or '无'}")
    print(f"  在仓台账: {len(pos)}期 " + ", ".join(p["signal_date"] for p in pos))
    print(f"  已结算: {len(st)}期")
    if st:
        rets = [s["port_net_ret"] for s in st]
        cum = 1.0
        for r in rets:
            cum *= 1 + r
        print(f"  累计净收益={ (cum-1)*100:+.2f}% ({len(rets)}个月, 期末cushion年化≈{((cum)**(12/max(len(rets),1))-1)*100:+.1f}%)")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--review"); ap.add_argument("--signal"); ap.add_argument("--settle")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    if a.status:
        return cmd_status()
    if a.review:
        return cmd_review(a.review)
    if a.signal:
        return cmd_signal(a.signal)
    if a.settle:
        return cmd_settle(a.settle)
    ap.print_help()
    return 1


if __name__ == "__main__":
    sys.exit(main())
