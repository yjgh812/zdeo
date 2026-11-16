# -*- coding: utf-8 -*-
"""周度胜率台账 v0 — 方案 winrate_lift_engineering_plan §3 度量闭环(2026-09-19)
口径: executor_orders BUY 全量 × fwd5/20/60(qfq close→close, 与侦察回测同源;
     与 9/18 重算的成交价口径有微小除息差异, 已声明) × 三维度分层(regime env 桶/信号源 kind/执行特征).
执行特征: 追涨度=买入日 qfq 当日涨幅; 乖离=买入日 close/MA20-1.
env: 全市场等权指数 250 日窗口分位(与 up_bucket 回测同口径).
到期: fwd5 需买入日后≥5交易日; fwd20/60 未到期标 None(如实), 到期后重跑即补全.
输出: data/winrate_ledger_weekly_<stamp>.json + stdout 报表
v1(2026-09-20 数学落地第二轮): describe() 加 win_ci95 = Wilson 95% 置信区间
  (owner=tulong_math_toolkit.wilson_ci)。周度 n 常在 10~40, 点估计胜率 30% 的真实区间
  可能横跨 15~50% —— n<30 的分组胜率禁脱离区间单独解读(报告域4: 30笔60%→CI[42,75])。
"""
import itertools
import json
import math
import os
import sqlite3
import sys
import time
from collections import defaultdict
from datetime import datetime, date, timedelta

sys.path.insert(0, r'D:\zcodesj')
from tulong_math_toolkit import wilson_ci

sys.stdout.reconfigure(encoding='utf-8', errors='replace')
ROOT = r'D:\zcodesj'
DB = 'file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro'
DATA_START = '20240601'   # env 指数 warmup
FWDS = (5, 20, 60)


def describe(xs):
    xs = sorted(x for x in xs if x is not None and math.isfinite(x))
    if not xs:
        return None
    n = len(xs)
    med = xs[n // 2] if n % 2 else 0.5 * (xs[n // 2 - 1] + xs[n // 2])
    wins = sum(1 for x in xs if x > 0)
    lo, hi = wilson_ci(wins, n)
    return {'n': n, 'mean': round(sum(xs) / n, 2), 'med': round(med, 2),
            'win': round(wins / n * 100, 1),
            'win_ci95': [round(lo * 100, 1), round(hi * 100, 1)]}


def main():
    t0 = time.time()
    # 1. 交易台账
    buys = []
    for line in open(os.path.join(ROOT, 'data', 'executor_orders.jsonl'), encoding='utf-8'):
        line = line.strip()
        if not line:
            continue
        try:
            o = json.loads(line)
        except Exception:
            continue
        if o.get('action') == 'BUY':
            code = o['code']
            code = code[-6:] if len(code) > 6 else code  # 早期订单带 sh/sz 前缀, qfq 库为裸 6 位
            buys.append({'code': code, 'name': o.get('name', ''), 'd': o['trade_date'],
                         'px': o.get('price'), 'qty': o.get('qty'), 'kind': o.get('kind', '?')})
    print(f'[LEDGER] buys={len(buys)}', flush=True)

    # 2. env 等权指数
    db = sqlite3.connect(DB, uri=True)
    ret_sum, ret_cnt = defaultdict(float), defaultdict(int)
    cur = db.execute(f"SELECT code,date,close FROM stock_daily_meta_qfq WHERE date>='{DATA_START}' ORDER BY code,date")
    for code, grp in itertools.groupby(cur, key=lambda r: r[0]):
        rows = list(grp)
        for k in range(1, len(rows)):
            d0, c0 = rows[k - 1][1], rows[k - 1][2]
            d1, c1 = rows[k][1], rows[k][2]
            if c0 and c0 > 0 and c1 and c1 > 0:
                ret_sum[d1] += c1 / c0 - 1
                ret_cnt[d1] += 1
    mdates = sorted(ret_cnt)
    idx, cum = [], 1.0
    for d in mdates:
        cum *= 1 + ret_sum[d] / ret_cnt[d]
        idx.append(cum)
    mpos = {d: k for k, d in enumerate(mdates)}
    env_of = {}
    for d in mdates:
        k = mpos[d]
        if k < 249:
            continue
        w = idx[k - 249:k + 1]
        env_of[d] = 100 * (idx[k] - min(w)) / (max(w) - min(w)) if max(w) > min(w) else 50
    print(f'[ENV] {len(env_of)} days, {time.time()-t0:.0f}s', flush=True)

    # 3. 每笔买入取 qfq 价格序列
    db2 = sqlite3.connect(DB, uri=True)
    recs = []
    miss = 0
    for i, b in enumerate(buys):
        rows = db2.execute("SELECT date,close FROM stock_daily_meta_qfq WHERE code=? AND date>=? "
                           "ORDER BY date LIMIT 80", (b['code'], b['d'])).fetchall()
        if not rows or rows[0][0] != b['d'] or not rows[0][1]:
            miss += 1
            continue
        cl = [r[1] for r in rows]
        c0 = cl[0]
        prev = db2.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date<? "
                           "ORDER BY date DESC LIMIT 1", (b['code'], b['d'])).fetchone()
        ret_day = (c0 / prev[0] - 1) * 100 if prev and prev[0] else None
        m20 = db2.execute("SELECT close FROM stock_daily_meta_qfq WHERE code=? AND date<=? "
                          "ORDER BY date DESC LIMIT 20", (b['code'], b['d'])).fetchall()
        ma20 = sum(r[0] for r in m20) / len(m20) if len(m20) == 20 and all(r[0] for r in m20) else None
        f = {}
        for h in FWDS:
            f[h] = (cl[h] / c0 - 1) * 100 if len(cl) > h and cl[h] else None
        recs.append({**b, 'f5': f[5], 'f20': f[20], 'f60': f[60],
                     'ret_day': ret_day, 'bias20': (c0 / ma20 - 1) * 100 if ma20 else None,
                     'env': env_of.get(b['d'])})
    db2.close()
    print(f'[RECS] {len(recs)} priced (miss {miss}), {time.time()-t0:.0f}s', flush=True)

    def wk(d):
        y, w, _ = date(int(d[:4]), int(d[4:6]), int(d[6:])).isocalendar()
        return f'{y}-W{w:02d}'

    # 周度双轨行(自省第三期长效机制#1): 本周【人】/【系统】问题各≤3条, 连续4周空=自省失效信号
    # 真源: data/weekly_dual_track.jsonl 每周一行(周日晚收口时登记), 台账周一12:30读最新
    def dual_track():
        rows = []
        fp = os.path.join(ROOT, 'data', 'weekly_dual_track.jsonl')
        if os.path.exists(fp):
            for ln in open(fp, encoding='utf-8'):
                ln = ln.strip()
                if ln:
                    try:
                        rows.append(json.loads(ln))
                    except Exception:
                        pass
        cur_y, cur_w, _ = date.today().isocalendar()
        cur = f'{cur_y}-W{cur_w:02d}'
        rows = [r for r in rows if r.get('week') and r['week'] <= cur]
        rows.sort(key=lambda r: r['week'])
        latest = rows[-1] if rows else None
        # 漏检质询: 最近4个已结束周(不含当周)连续无登记(用日期回退算ISO周,规避52/53周边界)
        def prev_weeks(tag, n=4):
            y, w = int(tag[:4]), int(tag[6:])
            mon = date.fromisocalendar(y, w, 1)
            out = []
            for k in range(1, n + 1):
                py, pw, _ = (mon - timedelta(days=7 * k)).isocalendar()
                out.append(f'{py}-W{pw:02d}')
            return out
        if latest:
            window = prev_weeks(cur)
            gap4 = all(pw not in {r['week'] for r in rows} for pw in window)
            # 警报仅当登记历史早于检测窗口起点(建账初期窗口全空≠漏检)
            if gap4 and rows[0]['week'] < window[-1]:
                note = '🔴连续4周未登记=触发是否漏检质询'
            else:
                gap4 = False
                note = 'ok' if not all(pw not in {r['week'] for r in rows} for pw in window) else '建账初期(窗口早于首登记)'
        else:
            gap4, note = False, '建账初期(尚无登记行)'
        return {'current_week': cur, 'latest': latest, 'gap4_warning': gap4, 'note': note,
                'registered_weeks': len(rows)}

    R = {'generated': datetime.now().strftime('%Y-%m-%d %H:%M'), 'buys': len(buys), 'priced': len(recs),
         'note': 'fwd=qfq close口径; fwd20/60 多数未到期如实为None; env=等权指数250日分位'}
    R['overall'] = {f'f{h}': describe([r[f'f{h}'] for r in recs]) for h in FWDS}
    by = defaultdict(list)
    for r in recs:
        by[wk(r['d'])].append(r)
    R['by_week'] = {k: {f'f{h}': describe([r[f'f{h}'] for r in v]) for h in FWDS} for k, v in sorted(by.items())}
    bk = defaultdict(list)
    for r in recs:
        bk[r['kind']].append(r)
    R['by_kind'] = {k: {f'f{h}': describe([r[f'f{h}'] for r in v]) for h in FWDS} for k, v in bk.items()}
    be = defaultdict(list)
    for r in recs:
        if r['env'] is not None:
            be['<40' if r['env'] < 40 else ('40-79' if r['env'] < 80 else '80+')].append(r)
    R['by_env'] = {k: {f'f{h}': describe([r[f'f{h}'] for r in v]) for h in FWDS} for k, v in sorted(be.items())}
    def bucket(x, edges):
        for lo, hi in edges:
            if lo <= x < hi:
                return f'{lo}~{hi}'
        return edges[-1][1] if x >= edges[-1][1] else edges[0][0]
    rt_edges = [(-99, 0), (0, 3), (3, 6), (6, 99)]
    bt_edges = [(-99, -3), (-3, 3), (3, 99)]
    br = defaultdict(list)
    bb = defaultdict(list)
    for r in recs:
        if r['ret_day'] is not None:
            br[bucket(r['ret_day'], rt_edges)].append(r)
        if r['bias20'] is not None:
            bb[bucket(r['bias20'], bt_edges)].append(r)
    R['by_chase'] = {k: {f'f{h}': describe([r[f'f{h}'] for r in v]) for h in FWDS} for k, v in sorted(br.items())}
    R['by_bias'] = {k: {f'f{h}': describe([r[f'f{h}'] for r in v]) for h in FWDS} for k, v in sorted(bb.items())}
    R['dual_track'] = dual_track()

    print('=== 周度胜率台账 v0 ===')
    print('OVERALL:', json.dumps(R['overall'], ensure_ascii=False))
    print('WEEK:', json.dumps(R['by_week'], ensure_ascii=False))
    print('KIND:', json.dumps(R['by_kind'], ensure_ascii=False))
    print('ENV:', json.dumps(R['by_env'], ensure_ascii=False))
    print('CHASE:', json.dumps(R['by_chase'], ensure_ascii=False))
    print('BIAS:', json.dumps(R['by_bias'], ensure_ascii=False))
    dt = R['dual_track']
    print('DUAL_TRACK(周度双轨行):', json.dumps(dt, ensure_ascii=False))
    stamp = datetime.now().strftime('%Y%m%d')
    json.dump(R, open(f'{ROOT}/data/winrate_ledger_weekly_{stamp}.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1, default=str)
    print(f'[DONE] {time.time()-t0:.0f}s -> data/winrate_ledger_weekly_{stamp}.json')


if __name__ == '__main__':
    main()
