# -*- coding: utf-8 -*-
"""轻量产物探针: 十三合一 cron 流水线推进状态(只读)"""
import sqlite3, os, glob, datetime

def q(db, sql):
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        v = con.execute(sql).fetchone()[0]
        con.close()
        return v
    except Exception as e:
        return f"ERR:{type(e).__name__}"


def jq(path, key):
    """读 json 产物字段（如 prem_seal/vibe 卡片的 data_date），失败返回缺省。"""
    try:
        import json as _json
        with open(path, encoding="utf-8") as f:
            return _json.load(f).get(key)
    except Exception as e:
        return f"ERR:{type(e).__name__}"

MAIN = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
FUND = r"D:\workbuddyspace\databases\fundamental_data.db"
ATTR = r"D:\zcodesj\attribution\attribution.db"

print(f"== 产物探针 {datetime.datetime.now():%H:%M:%S} ==")
print("v43      :", q(MAIN, "SELECT MAX(date) FROM stock_signals_v43"))
print("v375     :", q(MAIN, "SELECT MAX(date) FROM stock_signals_v375"))
print("chan     :", q(MAIN, "SELECT MAX(date) FROM chanlun_signals_nolookahead"))
print("smc_csv  :", sorted(glob.glob(r"D:\zcodesj\reports\smc_s2_roe_signal_2026082*.csv"))[-1:] or "无")
print("sig_csv  :", sorted(glob.glob(r"D:\zcodesj\reports\signal_202608*.csv"))[-1:] or "无")
print("sentiment:", q(FUND, "SELECT MAX(trade_date) FROM feature_market_sentiment"))
print("lhb_daily:", q(FUND, "SELECT MAX(trade_date) FROM longhubang_daily"))
print("dt_new   :", q(FUND, "SELECT MAX(trade_date) FROM dragon_tiger_new"))
print("north    :", q(FUND, "SELECT MAX(trade_date) FROM northbound_daily"))
print("limt_up  :", q(ATTR, "SELECT MAX(trade_date) FROM limit_up_patterns"))
print("limt_dn  :", q(ATTR, "SELECT MAX(trade_date) FROM limit_down_risks"))
print("sig_snap :", q(ATTR, "SELECT MAX(date) FROM signal_snapshots"))
print("em_rank  :", q(r"D:\zcodesj\eastmoney_quant\stock\stock_data.db", "SELECT MAX(rank_date) FROM stock_rank"))
# 2026-08-28 扩充: 8/27 22:00 整链未触发, 探针清单漏 flow/combo 两表(当晚两处漏检) →
# 十三步产物逐项行数核对; 补 第十三步双口径 / 第一步A-B试验 / 第十二步SC001+盘感 / 封单
print("flow_snap:", q(r"D:\zcodesj\data\em_flow_snapshot.db", "SELECT MAX(trade_date) FROM flow_snapshot"))
print("combo    :", q(ATTR, "SELECT MAX(created_at) FROM combo_experiment"))
print("prem_seal:", jq(r"D:\zcodesj\reports\prem_seal_latest.json", "date"))
print("vibe     :", jq(r"D:\zcodesj\reports\vibe_card_latest.json", "data_date"))
print("seal     :", q(ATTR, "SELECT MAX(date) FROM seal_snapshot"))
