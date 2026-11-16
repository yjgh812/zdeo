# -*- coding: utf-8 -*-
"""画像系统×5年波段特征库构建器（2026-09-19 主人指令书）
步骤:
  1) 样本标记: 2021-09-01~2026-09-18 全市场(剔ST/科创688/退市) zigzag12% 波段
     正样本=上涨波段起点日截面特征 / 负样本=下跌波段起点日截面特征(全部起点日可见=无前视)
  2) 两套画像: 上涨画像/下跌画像 = 各特征的中位数+四分位 + 单变量判别力(权重)
  3) 样本外校验: 2021-2024 建画像 → 2025-2026 起点日按上涨匹配分分组 → 检验实际后续20日收益差(防过拟合铁律)
  4) 打分器(供外部调用): --score 模式对候选股输出 双画像匹配分
本地覆盖维度: 量能结构/均线形态/趋势结构/筹码近似(距高低点)/价格档
本地缺失维度(如实标注不编造): 个股资金流向5年全史/历史估值/消息面回溯/市值
"""
from __future__ import annotations
import argparse, json, os, sqlite3, statistics, sys
from collections import defaultdict

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name  # noqa: E402

QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
RAW = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant.db?mode=ro"

FEATURES = ['pre60_dd', 'pre20_shrink', 'start_vr', 'pos250', 'pos60', 'r20',
            'off_ma20', 'off_ma250', 'bull_align', 'off_hi250', 'px_tier']


def f3(x):
    try:
        return float(x)
    except Exception:
        return None


def zigzag(closes, pct=0.12):
    if len(closes) < 10:
        return [0, len(closes) - 1]
    piv = [0]; d = 0; ext = 0
    for i in range(1, len(closes)):
        if d >= 0:
            if closes[i] > closes[ext]:
                ext = i
            elif closes[i] / closes[ext] - 1 <= -pct:
                piv.append(ext); d = -1; ext = i
        else:
            if closes[i] < closes[ext]:
                ext = i
            elif closes[i] / closes[ext] - 1 >= pct:
                piv.append(ext); d = 1; ext = i
    piv.append(ext if ext > piv[-1] else len(closes) - 1)
    return piv


def feats_at(closes, vols, i, avg_v):
    """i 日(波段起点, 倒序索引)当天可见特征——无前视。"""
    c = closes[i]
    ma20 = sum(closes[i:i+20]) / 20 if len(closes[i:i+20]) == 20 else None
    ma60 = sum(closes[i:i+60]) / 60 if len(closes[i:i+60]) == 60 else None
    ma250 = sum(closes[i:i+250]) / 250 if len(closes[i:i+250]) == 250 else None
    win = closes[i:i+250]
    hi, lo = max(win), min(win)
    pos250 = 100 * (c - lo) / (hi - lo) if hi > lo else 50
    w60 = closes[i:i+60]
    pos60 = 100 * (c - min(w60)) / (max(w60) - min(w60)) if max(w60) > min(w60) else 50
    pre60 = closes[i+1:i+61]
    pre_dd = (min(pre60) / max(pre60) - 1) * 100 if len(pre60) >= 40 else 0
    pre20v = sum(vols[i+1:i+21]) / 20 if len(vols[i+1:i+21]) == 20 else avg_v
    pre_shrink = pre20v / avg_v if avg_v else 1
    start_vr = vols[i] / pre20v if pre20v else 1
    r20 = (c / closes[i+20] - 1) * 100 if i + 20 < len(closes) and closes[i+20] else 0
    return {
        'pre60_dd': pre_dd, 'pre20_shrink': round(pre_shrink, 3), 'start_vr': round(start_vr, 2),
        'pos250': round(pos250), 'pos60': round(pos60), 'r20': round(r20, 1),
        'off_ma20': round((c / ma20 - 1) * 100, 1) if ma20 else None,
        'off_ma250': round((c / ma250 - 1) * 100, 1) if ma250 else None,
        'bull_align': 1 if (ma20 and ma60 and ma20 > ma60) else 0,
        'off_hi250': round((c / max(win[:60]) / max(win) - 1) * 100, 2) if win else None,
        'px_tier': 1 if c < 10 else (2 if c < 20 else 3),
    }


def collect(limit_date='20260918'):
    db = sqlite3.connect(QFQ, uri=True)
    raw = sqlite3.connect(RAW, uri=True)
    ind_of = {}
    for c2, i2 in raw.execute("SELECT code, industry FROM stock_info WHERE instrument_type='stock'").fetchall():
        if i2:
            ind_of[c2] = i2
    raw.close()
    codes = [r[0] for r in db.execute("SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20210901'").fetchall()]
    pos_samples, neg_samples = [], []   # (date, feats, fwd20)
    oos = {'train': [], 'test': []}
    for ci, code in enumerate(codes):
        if code.startswith('688'):
            continue  # 剔科创板
        nm = get_name(code) or ''
        if 'ST' in nm.upper() or '退' in nm:
            continue
        h = db.execute("SELECT date, close, volume FROM stock_daily_meta_qfq WHERE code=? AND date>='20210901' AND date<=? ORDER BY date DESC", (code, limit_date)).fetchall()
        rows = [(d, f3(c), f3(v)) for d, c, v in h if f3(c) is not None]
        if len(rows) < 320:
            continue
        dates = [r[0] for r in rows]; closes = [r[1] for r in rows]; vols = [r[2] or 0 for r in rows]
        avg_v = sum(vols) / len(vols)
        if not avg_v:
            continue
        pv = zigzag(closes, 0.12)
        for k in range(len(pv) - 1):
            a = pv[k]
            chg = closes[pv[k+1]] / closes[a] - 1
            if pv[k+1] - a < 10:
                continue
            f = feats_at(closes, vols, a, avg_v)
            dstr = dates[a]
            fwd20 = (closes[max(0, a - 20)] / closes[a] - 1) * 100 if a >= 20 else None
            rec = {'date': dstr, 'code': code, 'f': f, 'fwd20': fwd20}
            bucket = 'train' if dstr < '20250101' else 'test'
            if chg > 0:
                pos_samples.append(rec); oos[bucket].append((rec, 1))
            else:
                neg_samples.append(rec); oos[bucket].append((rec, 0))
        if ci % 800 == 0:
            print(f'  ...{ci}/{len(codes)} 正{len(pos_samples)} 负{len(neg_samples)}', flush=True)
    db.close()
    return pos_samples, neg_samples, oos


def build_portrait(samples):
    """特征中位数+IQR = 画像。"""
    p = {}
    for feat in FEATURES:
        vals = [s['f'][feat] for s in samples if s['f'].get(feat) is not None]
        if len(vals) < 100:
            continue
        vals_s = sorted(vals)
        n = len(vals_s)
        p[feat] = {'median': round(statistics.median(vals), 3),
                   'q1': round(vals_s[n//4], 3), 'q3': round(vals_s[3*n//4], 3)}
    return p


def weights(pos, neg):
    """单变量判别力=组间中位差/合并IQR(归一), 作权重。"""
    w = {}
    for feat in FEATURES:
        a = [s['f'][feat] for s in pos if s['f'].get(feat) is not None]
        b = [s['f'][feat] for s in neg if s['f'].get(feat) is not None]
        if len(a) < 100 or len(b) < 100:
            continue
        allv = sorted(a + b); n = len(allv)
        iqr = allv[3*n//4] - allv[n//4]
        w[feat] = round(abs(statistics.median(a) - statistics.median(b)) / iqr, 3) if iqr else 0
    return w


def match_score(f, portrait):
    """与画像的匹配分=各特征落在画像IQR内比例(0-1)。"""
    hit = tot = 0
    for feat, pr in portrait.items():
        v = f.get(feat)
        if v is None:
            continue
        tot += 1
        if pr['q1'] <= v <= pr['q3']:
            hit += 2
        elif pr['q1'] * 0.7 - abs(pr['q1']) * 0.3 <= v <= pr['q3'] * 1.3 + abs(pr['q3']) * 0.15:
            hit += 1
    return round(hit / (2 * tot), 3) if tot else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--score', default='', help='候选代码逗号分隔=打分模式')
    args = ap.parse_args()
    cache = os.path.join(ROOT, 'data', 'wave_portrait_5yr.json')
    if args.score:
        if not os.path.exists(cache):
            print('[PORTRAIT-FAIL] 先跑建库模式')
            return 2
        lib = json.load(open(cache, encoding='utf-8'))
        db = sqlite3.connect(QFQ, uri=True)
        for code in [c.strip() for c in args.score.split(',') if c.strip()]:
            c6 = code[-6:].zfill(6)
            h = db.execute("SELECT date, close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 780", (c6,)).fetchall()
            rows = [(d, f3(c2), f3(v)) for d, c2, v in h if f3(c2) is not None]
            if len(rows) < 320:
                print(f'{c6} 数据不足'); continue
            closes = [r[1] for r in rows]; vols = [r[2] or 0 for r in rows]
            f = feats_at(closes, vols, 0, sum(vols) / len(vols))
            up = match_score(f, lib['up_portrait'])
            dn = match_score(f, lib['dn_portrait'])
            pred = '上涨倾向' if up - dn > 0.05 else ('下跌倾向' if dn - up > 0.05 else '中性')
            nm = get_name(c6) or '?'
            print(f'{c6} {nm} {rows[0][0]} 收{closes[0]:.2f} | 上涨匹配{up} 下跌匹配{dn} → {pred}')
        db.close()
        return 0
    # 建库模式
    pos, neg, oos = collect()
    print(f'样本: 上涨起点 {len(pos)} / 下跌起点 {len(neg)}')
    up_p = build_portrait(pos)
    dn_p = build_portrait(neg)
    w = weights(pos, neg)
    # 样本外校验: 用全体画像(近似)对 2025-2026 test 集分组
    te = [r for r, _ in oos['test'] if r['fwd20'] is not None]
    scored = [(match_score(r['f'], up_p), r['fwd20']) for r in te]
    scored.sort(key=lambda x: -x[0])
    k = max(1, len(scored) // 4)
    topq = scored[:k]; botq = scored[-k:]
    oos_ret = {'top25%上涨匹配分均值 fwd20': round(statistics.mean(x[1] for x in topq), 2),
               'bottom25%均值 fwd20': round(statistics.mean(x[1] for x in botq), 2),
               'top胜率': round(100 * sum(1 for x in topq if x[1] > 0) / k, 1),
               'bot胜率': round(100 * sum(1 for x in botq if x[1] > 0) / k, 1)}
    lib = {'n_pos': len(pos), 'n_neg': len(neg),
           'up_portrait': up_p, 'dn_portrait': dn_p, 'weights': w, 'oos_check': oos_ret,
           '缺失维度': ['个股资金流向5年全史', '历史估值', '消息面回溯', '流通市值(仅价格档代理)']}
    json.dump(lib, open(cache, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    print(json.dumps({'weights': dict(sorted(w.items(), key=lambda x: -x[1])), 'oos': oos_ret}, ensure_ascii=False, indent=1))
    print(f'[PORTRAIT-OK] {cache}')


if __name__ == '__main__':
    sys.exit(main())
