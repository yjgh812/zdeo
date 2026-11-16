# -*- coding: utf-8 -*-
"""候选癸免费影子·台账记账件(观测层, 零交易权零闸门)
PREREG_NEWS_SENTIMENT_SHADOW_20261007 (ACTIVE, 指纹b4a9775064a6f6e4)

职责: 会话层(cron agent)完成采集(MCP)+LLM打分后, 用本件做机械记账:
  --add    : UPSERT一行事件(从stdin读json, 幂等键=date+code+headline_hash)
  --backfill: 对未判定行回填 fwd1/5/20 超额(qfq库, next-bar口径) + pos_bucket(250日分位)
  --status : 台账状态摘要(判读辅助)

台账: data/news_sentiment_shadow_ledger.jsonl
判读: 60交易日窗满按 prereg readout_LOCKED 三门(G1/G2/G3), 由 --status 输出供会话呈报。
"""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

LEDGER = Path(r"D:\zcodesj\data\news_sentiment_shadow_ledger.jsonl")
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
FIELD_ORDER = ["date", "event_time", "code", "name", "sentiment", "importance",
               "expost", "headline", "source", "is_key", "headline_hash",
               "pos_bucket", "fwd1", "fwd5", "fwd20", "excess1", "excess5",
               "excess20", "judged", "source_unavailable"]
REQUIRED = ["date", "code", "name", "sentiment", "importance", "expost", "headline"]


def load_rows():
    if not LEDGER.exists():
        return []
    return [json.loads(l) for l in LEDGER.read_text(encoding="utf-8").splitlines() if l.strip()]


def save_rows(rows):
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def headline_hash(headline: str) -> str:
    return hashlib.sha256(headline.strip().encode()).hexdigest()[:16]


def cmd_add(payload: str) -> int:
    row = json.loads(payload)
    missing = [k for k in REQUIRED if k not in row]
    if missing:
        print(json.dumps({"ok": False, "error": f"missing {missing}"}, ensure_ascii=False))
        return 2
    if row.get("expost"):
        print(json.dumps({"ok": False, "error": "expost=true事件禁止入账(prereg expost_rule)"},
                         ensure_ascii=False))
        return 2
    if int(row["sentiment"]) == 0:
        print(json.dumps({"ok": False, "error": "sentiment=0属对照组不入账(prereg)"},
                         ensure_ascii=False))
        return 2
    if not str(row.get("code", "")).isdigit() or len(str(row["code"])) != 6:
        print(json.dumps({"ok": False, "error": "code须6位纯数字"}, ensure_ascii=False))
        return 2
    row.setdefault("headline_hash", headline_hash(row["headline"]))
    row.setdefault("judged", False)
    rows = load_rows()
    key = (row["date"], row["code"], row["headline_hash"])
    if any((r["date"], r["code"], r.get("headline_hash")) == key for r in rows):
        print(json.dumps({"ok": True, "action": "duplicate_skipped", "key": key},
                         ensure_ascii=False))
        return 0
    rows.append(row)
    save_rows(rows)
    print(json.dumps({"ok": True, "action": "added", "key": key, "total": len(rows)},
                     ensure_ascii=False))
    return 0


def _qfq_close_series(code: str) -> dict:
    con = sqlite3.connect(QFQ_DB)
    q = "SELECT date, close FROM stock_daily_meta_qfq WHERE code=? AND close>0 ORDER BY date"
    rows = con.execute(q, (code,)).fetchall()
    con.close()
    return {d: c for d, c in rows}


def _market_equal_series() -> dict:
    con = sqlite3.connect(QFQ_DB)
    q = """SELECT date, AVG(close/NULLIF(lag_close,0)) FROM (
             SELECT date, code, close,
                    LAG(close) OVER (PARTITION BY code ORDER BY date) lag_close
             FROM stock_daily_meta_qfq WHERE close>0)
           GROUP BY date"""
    rows = con.execute(q).fetchall()
    con.close()
    return {d: v for d, v in rows if v}


def cmd_backfill(asof: str) -> int:
    rows = load_rows()
    dates = sorted({r["date"] for r in rows})
    mkt = _market_equal_series()
    cache = {}
    n_filled = 0
    for r in rows:
        if r.get("judged") or r["date"] > asof:
            continue
        if r["code"] not in cache:
            cache[r["code"]] = _qfq_close_series(r["code"])
        ser = cache[r["code"]]
        ds = sorted(ser)
        if r["date"] not in ser:
            continue  # 事件日非交易日(停牌等), 留待后续
        i = ds.index(r["date"])
        # next-bar口径: 事件日收盘买入不可行, 按prereg用事件日收盘为基准的T+1起算
        for h in (1, 5, 20):
            j = i + h
            if j < len(ds):
                r[f"fwd{h}"] = round((ser[ds[j]] / ser[r["date"]] - 1) * 100, 3)
            # 250日分位→pos_bucket
        hist = [ser[d] for d in ds[max(0, i - 249):i + 1]]
        if len(hist) >= 60:
            pct = sum(1 for x in hist if x <= ser[r["date"]]) / len(hist) * 100
            r["pos_bucket"] = "low" if pct < 40 else ("high" if pct > 60 else "mid")
        # 超额待fwd齐后统一算
        if all(r.get(f"fwd{h}") is not None for h in (1, 5, 20)) and r["date"] in mkt:
            base = mkt[r["date"]]
            mkt_fwd = {}
            mdates = sorted(mkt)
            k = mdates.index(r["date"]) if r["date"] in mdates else None
            # 简化: 市场等权fwd用同日历偏移近似(日频近似, 判读呈报时注明)
            if k is not None:
                for h in (1, 5, 20):
                    if k + h < len(mdates):
                        mkt_fwd[h] = (mkt[mdates[k + h]] / base - 1) * 100
                if all(h in mkt_fwd for h in (1, 5, 20)):
                    for h in (1, 5, 20):
                        r[f"excess{h}"] = round(r[f"fwd{h}"] - mkt_fwd[h], 3)
                    r["judged"] = True
                    n_filled += 1
    save_rows(rows)
    print(json.dumps({"ok": True, "filled": n_filled, "total": len(rows)}, ensure_ascii=False))
    return 0


def cmd_status() -> int:
    rows = load_rows()
    pos = [r for r in rows if r.get("sentiment", 0) >= 1 and r.get("judged")]
    neg = [r for r in rows if r.get("sentiment", 0) <= -1 and r.get("judged")]

    def med(v):
        v = sorted(v)
        return round(v[len(v) // 2], 3) if v else None
    out = {
        "total_rows": len(rows),
        "judged": sum(1 for r in rows if r.get("judged")),
        "pending": sum(1 for r in rows if not r.get("judged")),
        "G3_n": len(pos) + len(neg),
        "pos": {"n": len(pos), "excess5_med": med([r["excess5"] for r in pos])},
        "neg": {"n": len(neg), "excess5_med": med([r["excess5"] for r in neg])},
        "window": "60交易日自20261008起(约2027-01下旬判读)",
        "prereg": "PREREG_NEWS_SENTIMENT_SHADOW_20261007 ACTIVE 指纹b4a9775064a6f6e4",
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--add", help="UPSERT一行: 值为stdin则从标准输入读json, 否则视为json字符串/文件路径")
    ap.add_argument("--backfill", metavar="ASOF", help="回填fwd/pos至指定日YYYYMMDD")
    ap.add_argument("--status", action="store_true", help="台账状态摘要")
    a = ap.parse_args()
    if a.add:
        if a.add.strip() == "stdin":
            payload = sys.stdin.read()
        elif Path(a.add).exists():
            payload = Path(a.add).read_text(encoding="utf-8")
        else:
            payload = a.add
        sys.exit(cmd_add(payload))
    elif a.backfill:
        sys.exit(cmd_backfill(a.backfill))
    elif a.status:
        sys.exit(cmd_status())
    else:
        ap.print_help()


if __name__ == "__main__":
    main()
