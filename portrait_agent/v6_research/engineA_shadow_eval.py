# -*- coding: utf-8 -*-
"""引擎A影子期 T+20 判定 CLI (RISING-STOCK-PLAN P3 收口件).

用法: python engineA_shadow_eval.py            # 判定台账中所有已到期扫描日
口径: 每个扫描日(截面日)的触发股组合, T+1收盘→T+21收盘等权(qfq),
      对照 prereg 锚(样本内日聚簇+1.54%/20日, 行级+2.91%) 与同期全池等权.
判定: 影子期>=20个扫描日且到期数>=15 后出具阶段结论; 未到期如实报 NOT DUE.
只读观察工具.
"""
import sys, os, json, sqlite3
sys.stdout.reconfigure(encoding='utf-8')
QFQ = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'
LEDGER = r'D:\zcodesj\data\engineA_shadow_ledger.jsonl'
PREREG = json.load(open(r'D:\zcodesj\data\prereg_engineA_lowrev_v1_20260909.json', encoding='utf-8'))

def main():
    if not os.path.exists(LEDGER):
        print('台账为空'); return
    rows = [json.loads(l) for l in open(LEDGER, encoding='utf-8') if l.strip()]
    con = sqlite3.connect(f'file:{QFQ}?mode=ro', uri=True)
    tds = [r[0] for r in con.execute("SELECT DISTINCT date FROM stock_daily_meta_qfq ORDER BY date").fetchall()]
    qm = {}
    # 收益查询函数: code列表 + 起日(index) → T+1..T+21
    idx = {d: i for i, d in enumerate(tds)}
    due, pending = [], []
    for r in rows:
        sd = r['scan_date']
        if sd not in idx:
            pending.append((sd, '截面日不在qfq库')); continue
        i0 = idx[sd]
        if i0 + 21 < len(tds):
            due.append(r)
        else:
            pending.append((sd, f'T+20未到(已{len(tds)-i0-1}日)'))
    print(f'台账{len(rows)}个扫描日: 已到期{len(due)} 未到期{len(pending)}')
    for sd, why in pending: print(f'  NOT DUE {sd}: {why}')
    if not due:
        print('阶段结论: 影子期样本不足, 继续积累'); return
    print(f"\n{'截面日':<10}{'n':>4}{'组合20日%':>10}{'全池%':>8}{'超额pp':>8}  锚(日聚簇{PREREG['样本内锚_2013_2026']['信号日聚簇均值%']}%)")
    total_ok = 0
    for r in due:
        sd, codes = r['scan_date'], r['codes']
        i0 = idx[sd]
        d1, d21 = tds[i0+1], tds[i0+21]
        ph = ','.join('?'*len(codes))
        try:
            px = dict(con.execute(f"SELECT code,close FROM stock_daily_meta_qfq WHERE date=? AND code IN ({ph})", (d1, *codes)).fetchall())
            px2 = dict(con.execute(f"SELECT code,close FROM stock_daily_meta_qfq WHERE date=? AND code IN ({ph})", (d21, *codes)).fetchall())
            rs = [px2[c]/px[c]-1 for c in codes if c in px and c in px2]
        except Exception as e:
            print(f'  {sd}: 查询失败 {e}'); continue
        if not rs: continue
        port = (sum(rs)/len(rs))*100
        bench = con.execute("SELECT AVG(close/(SELECT close FROM stock_daily_meta_qfq AS t2 WHERE t2.code=t1.code AND t2.date=?)-1)*100 FROM stock_daily_meta_qfq AS t1 WHERE t1.date=?", (d1, d21)).fetchone()[0]
        ex = port - (bench or 0)
        total_ok += 1
        print(f"{sd:<10}{len(rs):>4}{port:>10.2f}{bench or 0:>8.2f}{ex:>8.2f}  {'达标' if ex>0 else '未达标'}")
    print(f'\n影子期进度: 到期{total_ok}/{len(rows)} (需>=15个到期出具阶段结论)')

if __name__ == '__main__':
    main()
