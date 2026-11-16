# -*- coding: utf-8 -*-
"""UP×env<40 影子台账 fwd 到期判定 — PREREG_UP_ENV40_20260919 闭环件(2026-09-19)
口径(冻结文档 §2): fwd=qfq 收盘, T=reg_date 起第 20/60 个交易日; 超额=个票 fwd−同日全市场 fwd 中位。
行为: 扫台账 PENDING 记录 → 已到期(库中存在 reg_date+20/+60 交易日)者计算并回填 → 全判完标 DONE;
     停牌致数据缺失到期 60 日后仍无 → 标 NODATA; 幂等(已有值不重算); 原子重写台账。
产出: stdout 判定汇总; 汇总统计随台账行内字段。手动或 cron 均可, 每日/每周跑皆幂等。
"""
import json
import os
import sqlite3
import sys
from datetime import datetime

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
DB = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
LEDGER = os.path.join(ROOT, 'data', 'up_env40_shadow_ledger.jsonl')
FWDS = (20, 60)


def main():
    db = sqlite3.connect(DB, uri=True)
    all_dates = [r[0] for r in db.execute(
        "SELECT DISTINCT date FROM stock_daily_meta_qfq WHERE date>='20250101' ORDER BY date")]
    didx = {d: i for i, d in enumerate(all_dates)}

    rows = [json.loads(l) for l in open(LEDGER, encoding='utf-8') if l.strip()]
    med_cache = {}
    n_eval = n_done = 0

    def base_med(d, h):
        key = (d, h)
        if key in med_cache:
            return med_cache[key]
        i = didx.get(d)
        if i is None or i + h >= len(all_dates):
            med_cache[key] = None
            return None
        dh = all_dates[i + h]
        r = db.execute(
            "SELECT b.close/a.close FROM stock_daily_meta_qfq a JOIN stock_daily_meta_qfq b "
            "ON a.code=b.code WHERE a.date=? AND b.date=? AND a.close>0 AND b.close>0", (d, dh)).fetchall()
        xs = sorted(x[0] - 1 for x in r if x and x[0])
        m = xs[len(xs) // 2] if len(xs) >= 100 else None
        med_cache[key] = m
        return m

    for rec in rows:
        if rec.get('status') not in (None, 'PENDING'):
            continue
        i = didx.get(rec['reg_date'])
        if i is None:
            continue
        changed = False
        for h in FWDS:
            key = f'fwd{h}'
            if rec.get(key) is not None:
                continue
            if i + h >= len(all_dates):
                continue  # 未到期
            dh = all_dates[i + h]
            r = db.execute(
                "SELECT b.close/a.close FROM stock_daily_meta_qfq a JOIN stock_daily_meta_qfq b "
                "ON a.code=b.code WHERE a.code=? AND a.date=? AND b.date=? AND a.close>0 AND b.close>0",
                (rec['code'], rec['reg_date'], dh)).fetchone()
            if r and r[0]:
                f = (r[0] - 1) * 100
                bm = base_med(rec['reg_date'], h)
                rec[key] = round(f, 2)
                rec[f'x{h}'] = round(f - bm, 2) if bm is not None else None
                n_eval += 1
                changed = True
            elif i + h < len(all_dates) and (didx[all_dates[-1]] - i) >= h + 5:
                rec[key] = 'NODATA'  # 到期超过5交易日仍无价(长期停牌/退市)
                changed = True
        if all(rec.get(f'fwd{h}') is not None for h in FWDS):
            rec['status'] = 'DONE'
            rec['done_ts'] = datetime.now().strftime('%Y-%m-%d %H:%M')
            n_done += 1
        elif changed:
            rec['status'] = 'PARTIAL'
            rec['eval_ts'] = datetime.now().strftime('%Y-%m-%d %H:%M')

    db.close()
    tmp = LEDGER + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        for rec in rows:
            f.write(json.dumps(rec, ensure_ascii=False) + '\n')
    os.replace(tmp, LEDGER)
    st = {}
    for rec in rows:
        st[rec.get('status', 'PENDING')] = st.get(rec.get('status', 'PENDING'), 0) + 1
    print(f'[FWD-EVAL] evaluated={n_eval} newly_done={n_done} status={st} ledger={LEDGER}')
    done20 = [(r['reg_date'], r['x20']) for r in rows if isinstance(r.get('fwd20'), (int, float))
              and isinstance(r.get('x20'), (int, float))]
    if done20:
        xs = sorted(x for _, x in done20)
        print(f"[FWD-EVAL] done20 n={len(done20)} x20_med={xs[len(xs)//2]:.2f} "
              f"win={round(100*sum(1 for _, x in done20 if x > 0)/len(done20), 1)}%")


if __name__ == '__main__':
    main()
