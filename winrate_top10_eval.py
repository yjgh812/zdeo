# -*- coding: utf-8 -*-
"""winrate_top10_eval.py · 胜率Top10场外验证评估器 (配套 PREREG_WINRATE_TOP10_TENCENT_20260914.json)

基准 = 20260914 开盘价(qfq, 自动取, 无需人工记录)。
复核点: T+1月≈20261014 / T+3月≈20261214(主判据) / T+6月≈20270316 — 以qfq实际交易日动态判定.
判据(冻结): H1 实测月度上涨占比 vs 基线58.8%(连续两期<50%=外推失败) / H2 等权收益>池等权(T+3月主判据).
用法: python winrate_top10_eval.py --asof 20261214   # asof=评估基准日(默认=qfq最新交易日)
"""
import os, sys, json, glob, argparse, sqlite3
sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')
import numpy as np
import pandas as pd

BASE = r'D:\zcodesj'
QFQ = r'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
REG_F = os.path.join(BASE, 'data', 'PREREG_WINRATE_TOP10_TENCENT_20260914.json')
BASE_DATE = '20260914'          # 买入基准日(开盘价)
BASELINE_WIN = 58.8             # 名单历史61月胜率均值基线
MAIN_CHECK = '20261214'         # 主判据时点(名义)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--asof', default=None, help='评估日(YYYYMMDD, 默认qfq最新交易日)')
    args = ap.parse_args()

    reg = json.load(open(REG_F, encoding='utf-8'))
    stocks = reg['stocks']
    codes = [s['code'] for s in stocks]
    names = {s['code']: s['name'] for s in stocks}

    con = sqlite3.connect(QFQ, uri=True, timeout=60)
    # 基准开盘价
    ph = ','.join('?' * len(codes))
    base = pd.read_sql_query(
        f"SELECT code, open FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date='{BASE_DATE}'",
        con, params=codes)
    all_d = [str(r[0]) for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>='20260914' ORDER BY date").fetchall()]
    close = pd.read_sql_query(
        f"SELECT code, date, close FROM stock_daily_meta_qfq WHERE code IN ({ph}) AND date>='{BASE_DATE}' ORDER BY code,date",
        con, params=codes)
    # 池等权(全市场日收益近似: 全池日收盘均值不可得→用等权抽样池=候选池自身外的对照改为上证? 简化: 池=全市场等权由 change_pct)
    pool = pd.read_sql_query(
        "SELECT date, AVG(change_pct)/100.0 AS ret FROM stock_daily_meta_qfq WHERE date>='20260914' GROUP BY date", con)
    con.close()
    base_map = dict(zip(base['code'].str.zfill(6), base['open']))
    missing = [c for c in codes if c not in base_map or not base_map[c]]
    if missing:
        print(f"❌ 基准日 {BASE_DATE} 开盘价缺失: {missing} (qfq未含当日? 等待数据链)")
        return 2
    all_d = [d for d in all_d if d > BASE_DATE]
    pool_ret = pool.set_index('date')['ret'] if not pool.empty else pd.Series(dtype=float)

    asof = args.asof or (all_d[-1] if all_d else None)
    if asof is None:
        print('❌ qfq 无基准日后数据'); return 2
    idx = all_d.index(asof) if asof in all_d else len(all_d) - 1
    asof = all_d[idx]
    bars_held = idx + 1
    print(f"=== 胜率Top10 场外验证评估 (基准 {BASE_DATE} 开盘, 评估日 {asof}, 持有{bars_held}交易日) ===\n")

    # 1) 逐只
    rows = []
    for s in stocks:
        c = s['code']
        g = close[close['code'] == c].set_index('date')['close']
        if asof not in g.index:
            rows.append({'code': c, 'name': names[c], 'note': '停牌/缺bar'}); continue
        ret = g[asof] / base_map[c] - 1
        rows.append({'code': c, 'name': names[c], 'source': s['source'],
                     'entry': base_map[c], 'now': round(float(g[asof]), 2),
                     'ret': round(ret * 100, 2), 'up': bool(ret > 0)})
    df = pd.DataFrame(rows)
    valid = df[df['ret'].notna()]
    print(valid.to_string(index=False))

    # 2) 判定
    n = len(valid)
    up_ratio = float((valid['up'] == True).mean() * 100) if n else np.nan
    eq_ret = float((np.expm1(np.log1p(valid['ret'] / 100).mean())) * 100) if n else np.nan
    pool_cum = float((np.expm1(pool_ret.loc[[d for d in all_d[:idx+1] if d in pool_ret.index]].sum())) * 100)
    print(f"\n--- 判定 (n={n}) ---")
    print(f"H1 实测上涨占比: {up_ratio:.0f}% vs 名单基线 {BASELINE_WIN}% ({'≥' if up_ratio >= BASELINE_WIN else '<'}基线)")
    print(f"   等权累计收益: {eq_ret:+.2f}% vs 全市场等权 {pool_cum:+.2f}% (超额 {eq_ret - pool_cum:+.2f}pp)")
    main_due = asof >= MAIN_CHECK
    verdicts = []
    if bars_held < 5:
        verdicts.append('观察期过短(<5交易日) — 仍属噪音区, 按 prereg 以月度窗口评估')
    h1_pass = up_ratio >= 50
    h2_pass = eq_ret > pool_cum
    if main_due:
        verdicts.append(f"主判据(T+3月): H1={'✅' if h1_pass else '❌'} H2={'✅' if h2_pass else '❌'}")
    else:
        verdicts.append(f"中间观察: H1方向={'✅' if h1_pass else '❌'} H2方向={'✅' if h2_pass else '❌'} (主判据 {MAIN_CHECK})")
    for v in verdicts:
        print(f"   {v}")
    # 落盘
    outp = os.path.join(BASE, 'data', f'winrate_top10_eval_{asof}.json')
    json.dump({'asof': asof, 'bars': bars_held, 'n': n, 'up_ratio': up_ratio,
               'eq_ret': eq_ret, 'pool_cum': pool_cum,
               'rows': rows}, open(outp, 'w', encoding='utf-8'), ensure_ascii=False, indent=1, default=float)
    print(f"落盘 {outp}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
