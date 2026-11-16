# -*- coding: utf-8 -*-
"""引擎A 低位反转启动 · 影子扫描 CLI (RISING-STOCK-PLAN P3).

配方: prereg_engineA_lowrev_v1_20260909.json 指纹19d5c13578b8ca96 (跑前冻结, 禁改).
用法: python engine_a_shadow_scan.py            # 扫panel最新截面, 出触发名单+追加台账
      python engine_a_shadow_scan.py --dry      # 只看不写台账
      python engine_a_shadow_scan.py --date 20260915  # 回扫历史截面(2026-09-19补:
        9/16 v6_research 迁移断链致 0915-0917 截面漏登, panel 全史真实截面无前视补录; 台账幂等)
台账: data/engineA_shadow_ledger.jsonl (同截面日幂等, 重复跑不重复写).
新鲜度: panel截面落后qfq最新交易日>=3 → WARN(影子停摆风险).
只读: panel/qfq只读; 台账为本观察唯一写入.
"""
import sys, os, json, sqlite3, argparse
import pandas as pd
sys.stdout.reconfigure(encoding='utf-8')
HERE = r'D:\zcodesj\portrait_agent'
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
LEDGER = r'D:\zcodesj\data\engineA_shadow_ledger.jsonl'
PREREG = json.load(open(r'D:\zcodesj\data\prereg_engineA_lowrev_v1_20260909.json', encoding='utf-8'))

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--dry', action='store_true')
    ap.add_argument('--date', default=None, help='回扫历史截面YYYYMMDD(panel全史真实截面, 补录断链漏登日)')
    a = ap.parse_args()
    panel = pd.read_pickle(os.path.join(HERE, 'cache', 'panel_v6_full.pkl'))
    panel['date'] = panel['date'].astype(str)
    last = a.date if a.date else panel['date'].max()
    if a.date and last not in set(panel['date']):
        print(f'FAIL 截面{last}不在panel中'); sys.exit(2)
    snap = panel[panel['date'] == last]
    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    qmax = con.execute("SELECT MAX(date) FROM stock_daily_meta_qfq").fetchone()[0]
    con.close()
    lag_gap = int(qmax) - int(last)
    if a.date:
        warn = f"回扫历史截面(scan_date={last}, 当前qfq最新={qmax})"
    else:
        warn = f"WARN panel截面{last}落后qfq最新{qmax}共{lag_gap}日历日, 影子时效受限" if lag_gap >= 3 else None

    pos = pd.to_numeric(snap['pos250'], errors='coerce')
    ev = (snap[['chan_buy1_10','chan_buy2_10','island_5','gap_up_hold']].fillna(0) > 0.5).any(axis=1)
    neg = (snap[['rounded_20','lhb_buy_5','vwap60_bias','vwap20_bias']].fillna(1) <= 0.5).all(axis=1)
    hit = snap[(pos <= 0.40) & ev & neg][['code', 'pos250']].copy()
    # 2026-09-10 B-M5: 剔除 688 科创板(宪法排除面, 20% 涨跌幅制与可交易宇宙不一致,
    # 影子台账曾命中 688538/688093/688283 污染 T+20 阶段结论)
    hit = hit[~hit['code'].astype(str).str.startswith('688')]
    hit['pos250'] = hit['pos250'].round(3)
    try:
        mkt_hot_val = int((snap['mkt_hot'].fillna(0) > 0.5).sum() > 0)
    except Exception:
        mkt_hot_val = None
    rec = {"scan_date": last, "qfq_max": qmax, "fingerprint": PREREG['指纹'],
           "n_hits": len(hit), "codes": hit['code'].tolist(), "warn": warn, "mkt_hot": mkt_hot_val}
    print(f"影子扫描 截面={last} (qfq最新={qmax}) 命中={len(hit)} 只")
    if warn: print(warn)
    if len(hit): print(hit.to_string(index=False))
    if a.dry:
        print('[dry] 未写台账'); return
    done = set()
    if os.path.exists(LEDGER):
        done = {json.loads(l)['scan_date'] for l in open(LEDGER, encoding='utf-8') if l.strip()}
    if last in done:
        print(f'台账已有截面{last}, 幂等跳过'); return
    with open(LEDGER, 'a', encoding='utf-8') as f:
        f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    print(f'台账追加: {LEDGER}')

if __name__ == '__main__':
    main()
