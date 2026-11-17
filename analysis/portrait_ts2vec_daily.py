r"""portrait_ts2vec_daily.py — 画像系统·TS2Vec形态检索日更面板（PREREG_PORTRAIT_TS2VEC_DAILY_V1，指纹325792e14e2663b0）。

两目标(上证最近30日/600519贵州茅台最近30日)对D层库全市场历史30日窗口余弦检索Top-500→DTW精配Top-150
→后续20行分布描述。日更面板=纯描述零预测零交易权，禁作选股公式/信号源/闸门输入。
复用标定件核心(import不复制逻辑)，挂 universe_portrait_daily.py 编排器(夜链23:00 step4内)。
产物: data/ts2vec_retrieval_latest.json + data/ts2vec_retrieval_ledger.jsonl(按asof幂等)。
用法: python -I analysis/portrait_ts2vec_daily.py
"""
import os

import hashlib
import json
import sqlite3
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import portrait_ts2vec_r1_20261117 as core  # noqa: E402  (import即设置CUBLAS确定性环境并导入torch)

PREREG_DAILY = r"D:\zcodesj\data\PREREG_PORTRAIT_TS2VEC_DAILY_V1_20261117.json"
QFQ_DB = r"D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db"
OUT_JSON = r"D:\zcodesj\data\ts2vec_retrieval_latest.json"
LEDGER = r"D:\zcodesj\data\ts2vec_retrieval_ledger.jsonl"
LEAK_CODE = 600519  # 贵州茅台


def fail_na(reason):
    """结构化N/A: 台账行+latest json 双落 availability=false, exit 3 禁伪健康。"""
    asof = int(time.strftime("%Y%m%d"))
    row = {"asof": asof, "availability": False, "reason": reason}
    upsert_ledger(row)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(row, f, ensure_ascii=False)
    print("结构化不可用: %s (台账+快照已落N/A行, exit 3)" % reason)
    sys.exit(3)


def upsert_ledger(row):
    """台账按asof幂等: 同日重跑替换该日行(窗口删除+追加)。"""
    rows = []
    try:
        with open(LEDGER, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    rows.append(json.loads(line))
    except FileNotFoundError:
        pass
    rows = [r for r in rows if r.get("asof") != row["asof"]]
    rows.append(row)
    with open(LEDGER, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return len(rows)


def verify_prereg():
    raw = open(PREREG_DAILY, "rb").read()
    prereg = json.loads(raw.decode("utf-8"))
    body = raw.replace((' "fingerprint": "%s"' % prereg["fingerprint"]).encode(),
                       b' "fingerprint": ""')
    fp = hashlib.sha256(body).hexdigest()[:16]
    if fp != prereg["fingerprint"]:
        print("结构化错误: 日更预注册指纹不匹配(%s vs %s), fail-closed exit 2" % (fp, prereg["fingerprint"]))
        sys.exit(2)
    print("PREREG指纹校验: %s 匹配=True" % fp)
    return fp


def load_targets(con):
    """T1=上证最后30交易日, T2=600519最后30行close>0。返回(t1_vec, t1_end, t2_vec, t2_end)。"""
    rows = con.execute("SELECT date, idx_sh_c FROM market_daily WHERE idx_sh_c IS NOT NULL "
                       "ORDER BY date DESC LIMIT 30").fetchall()
    rows.reverse()
    t1 = core.norm01([float(r[1]) for r in rows])
    t1_end = int(rows[-1][0])
    rows2 = con.execute("SELECT date, close FROM stock_daily WHERE code=600519 AND close>0 "
                        "ORDER BY date DESC LIMIT 30").fetchall()
    rows2.reverse()
    t2 = core.norm01([float(r[1]) for r in rows2])
    t2_end = int(rows2[-1][0])
    return t1, t1_end, t2, t2_end


def main():
    t0 = time.time()
    fp = verify_prereg()
    import torch
    if not torch.cuda.is_available():
        fail_na("cuda_unavailable")
    dev = torch.device("cuda")
    qfq_max = int(sqlite3.connect("file:%s?mode=ro" % QFQ_DB, uri=True).execute(
        "SELECT max(date) FROM stock_daily_meta_qfq").fetchone()[0])
    con = sqlite3.connect("file:%s?mode=ro" % core.SRC_DB, uri=True)
    fs_max = int(con.execute("SELECT max(date) FROM stock_daily").fetchone()[0])
    if fs_max != qfq_max:
        fail_na("stale_library: fullseries max=%d != qfq max=%d" % (fs_max, qfq_max))
    t1, t1_end, t2, t2_end = load_targets(con)
    print("目标: T1=上证至%s / T2=600519至%s (库鲜度闸: fullseries==qfq==%d)" % (t1_end, t2_end, fs_max))
    train_arr, nwin_a = core.scan_and_sample(con)
    model = core.train_encoder(train_arr, dev)
    model.eval()
    t1_emb = core.encode_norm(model, t1, dev)[0]
    t2_emb = core.encode_norm(model, t2, dev)[0]
    heap1, heap2, nwin = core.coarse_scan(model, dev, con, t1_emb, t2_emb,
                                          leak_code=LEAK_CODE, leak_date=t2_end)
    r1 = core.run_target("T1_上证", t1, sorted(heap1), con, nwin)
    r2 = core.run_target("T2_600519", t2, sorted(heap2), con, nwin)
    con.close()
    res = {"asof": t2_end, "availability": True, "prereg_fp": fp,
           "targets": {"T1_sh": r1, "T2_600519": r2}}
    ledger_row = {"asof": t2_end, "availability": True, "prereg_fp": fp, "n_lib_windows": nwin,
                  "stats": {"T1_sh": r1["stats"], "T2_600519": r2["stats"]},
                  "era": {"T1_sh": r1["era"], "T2_600519": r2["era"]},
                  "diag": {"T1_sh": r1["diag"], "T2_600519": r2["diag"]}}
    upsert_ledger(ledger_row)
    with open(OUT_JSON, "w", encoding="utf-8") as f:
        json.dump(res, f, ensure_ascii=False)
    print("日更面板完成 asof=%d 判定可用 台账n=%d 总耗时%.0fs" % (
        t2_end, len(open(LEDGER, encoding="utf-8").read().splitlines()), time.time() - t0))


if __name__ == "__main__":
    main()
