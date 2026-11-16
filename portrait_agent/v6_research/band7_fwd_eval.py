# -*- coding: utf-8 -*-
"""band7五簇前向判定CLI. 用法:
  python band7_fwd_eval.py --horizon 5    # T+5分簇对照(未到期则如实报not due)
  python band7_fwd_eval.py --horizon 20
口径: qfq复权收盘; 分簇等权中位/均值/胜率 vs 全池等权基准(剔除基准日缺失).
只读观察工具, 不写任何生产表.
"""
import sys, os, json, sqlite3, argparse
from collections import defaultdict
import statistics as st
sys.stdout.reconfigure(encoding='utf-8')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
OBS_FP = r'D:\zcodesj\data\band7_cluster_fwd_obs_20260908.json'

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--horizon', type=int, required=True)
    a = ap.parse_args()
    obs = json.load(open(OBS_FP, encoding='utf-8'))
    base_date = obs['登记日']
    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    tds = [r[0] for r in con.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
    if base_date not in tds:
        print(f'FAIL: 基线日{base_date}不在qfq库'); sys.exit(2)
    i0 = tds.index(base_date)
    fwd_tds = tds[i0+1:]
    if len(fwd_tds) < a.horizon:
        print(f'NOT DUE: 9/8后仅入库{len(fwd_tds)}个交易日, T+{a.horizon}需{a.horizon}个. '
              f'已入库末日={tds[-1]}'); return
    dt = fwd_tds[a.horizon-1]
    px1 = dict(con.execute("SELECT code, close FROM stock_daily_meta_qfq WHERE date=?", (dt,)).fetchall())
    px0 = dict(con.execute("SELECT code, close FROM stock_daily_meta_qfq WHERE date=?", (base_date,)).fetchall())
    con.close()

    # 全池等权基准(两日均有效的股票)
    common = [c for c in px0 if c in px1]
    bench = sum(px1[c]/px0[c]-1 for c in common)/len(common)*100

    groups = defaultdict(list)
    for code, rec in obs['基线'].items():
        if code not in px1:
            groups['缺价'].append((code, rec['name'])); continue
        r = (px1[code]/rec['close_0908']-1)*100
        groups[rec['cluster']].append((r, rec['name'], code))
    print(f"T+{a.horizon} 判定日={dt}  样本{sum(len(v) for k,v in groups.items() if k!='缺价')}/112  "
          f"全池等权基准={bench:+.2f}%  (缺失: {groups.get('缺价', [])})")
    print(f"{'簇':<4}{'N':>4}{'中位%':>9}{'均值%':>9}{'胜率%':>7}{'超额中位pp':>11}  Top3 / Bottom3")
    for cl in ['A', 'B', 'C', 'D', 'E']:
        rs = groups.get(cl, [])
        if not rs: continue
        vals = sorted(x[0] for x in rs)
        med = st.median(vals); mean = sum(vals)/len(vals)
        win = sum(1 for v in vals if v > 0)/len(vals)*100
        srt = sorted(rs, reverse=True)
        t3 = ' '.join(f"{nm}{v:+.1f}" for v, nm, _ in srt[:3])
        b3 = ' '.join(f"{nm}{v:+.1f}" for v, nm, _ in srt[-3:])
        print(f"{cl:<4}{len(rs):>4}{med:>9.2f}{mean:>9.2f}{win:>7.1f}{med-bench:>11.2f}  {t3} | {b3}")

if __name__ == '__main__':
    main()
