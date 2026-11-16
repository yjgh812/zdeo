# -*- coding: utf-8 -*-
"""全市场三级别顶底画像普查（2026-09-19 主人令：对所有A股统计，非只候选股）
对全市场每只股票：提取当前特征 → 与三级别（小/中/大）底顶画像匹配打分 → 输出分布与清单
"""
from __future__ import annotations
import json, os, sqlite3, sys, statistics
from datetime import datetime

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
sys.path.insert(0, ROOT)
from stock_name_lookup import get_name  # noqa: E402

QFQ = r"file:D:/workbuddyspace/factor_engine/data/dragon_quant_qfq.db?mode=ro"
PORTRAIT = os.path.join(ROOT, 'data', 'topbot_3levels_20260919.json')


def f3(x):
    try:
        return float(x)
    except Exception:
        return None


def match_score(f, portrait):
    hit = tot = 0
    for feat, pr in portrait.items():
        v = f.get(feat)
        if v is None:
            continue
        tot += 1
        if pr['q1'] <= v <= pr['q3']:
            hit += 2
        elif pr['q1'] * 0.7 <= v <= pr['q3'] * 1.3:
            hit += 1
    return round(hit / (2 * tot), 3) if tot else 0


def main():
    lib = json.load(open(PORTRAIT, encoding='utf-8'))
    mid_bot = lib['中级行情']['bot']
    mid_top = lib['中级行情']['top']

    db = sqlite3.connect(QFQ, uri=True)
    codes = [r[0] for r in db.execute(
        "SELECT DISTINCT code FROM stock_daily_meta_qfq WHERE date>='20260901'").fetchall()]
    codes = [c for c in codes if not c.startswith('688')]

    results = []
    skipped = 0
    for ci, code in enumerate(codes):
        nm = get_name(code) or ''
        if 'ST' in nm.upper() or '退' in nm:
            skipped += 1; continue
        h = db.execute(
            "SELECT close, volume FROM stock_daily_meta_qfq WHERE code=? ORDER BY date DESC LIMIT 500",
            (code,)).fetchall()
        rh = [(f3(a), f3(b)) for a, b in h]
        if len(rh) < 320 or rh[0][0] is None or not (2 <= rh[0][0] <= 500):
            skipped += 1; continue
        closes = [x[0] for x in rh]; vols = [x[1] or 0 for x in rh]
        c = closes[0]; n = len(closes); avg_v = sum(vols) / n
        if not avg_v:
            skipped += 1; continue
        ma20 = sum(closes[:20]) / 20
        yr = closes[:250]
        hi_yr, lo_yr = max(yr), min(yr)
        # 区域特征(当前±10日→只用最近10日)
        seg_v = vols[:10]
        v_reg = sum(seg_v) / len(seg_v) / avg_v if seg_v else 1
        amp = (max(closes[:10]) / min(closes[:10]) - 1) * 100 if min(closes[:10]) else 0
        f = {'v_reg': round(v_reg, 3),
             'off_ma20': round((c / ma20 - 1) * 100, 1) if ma20 else 0,
             'off_hi_yr': round((c / hi_yr - 1) * 100, 1) if hi_yr else 0,
             'off_lo_yr': round((c / lo_yr - 1) * 100, 1) if lo_yr else 0,
             'amp_reg': round(amp, 1),
             'prev_chg': round((c / closes[20] - 1) * 100, 1) if closes[20] else 0,
             'days_prev': 30}
        bot_s = match_score(f, mid_bot)
        top_s = match_score(f, mid_top)
        verdict = '偏底部' if bot_s - top_s > 0.1 else ('偏顶部' if top_s - bot_s > 0.1 else '中性')
        results.append({'code': code, 'name': nm, 'px': c,
                        'bot': bot_s, 'top': top_s, 'verdict': verdict,
                        'off_hi': f['off_hi_yr'], 'off_lo': f['off_lo_yr'],
                        'off_ma20': f['off_ma20'], 'v_reg': f['v_reg']})
        if ci % 800 == 0:
            print(f'  ...{ci}/{len(codes)}', flush=True)
    db.close()

    # 统计
    from collections import Counter
    vc = Counter(r['verdict'] for r in results)
    bots = sorted([r for r in results if r['verdict'] == '偏底部'], key=lambda x: -x['bot'])
    tops = sorted([r for r in results if r['verdict'] == '偏顶部'], key=lambda x: -x['top'])
    mids = [r for r in results if r['verdict'] == '中性']

    today = datetime.now().strftime('%Y%m%d')
    L = [f"# 全市场三级别顶底画像普查（{datetime.now().strftime('%Y-%m-%d %H:%M')}）",
         "",
         f"> 覆盖 {len(results)} 只(剔科创/ST/数据不足 {skipped}) ｜ 中级行情级别(25%阈值)画像匹配",
         f"> 分布: 🔴偏底部 {vc.get('偏底部',0)} ｜ 🟢偏顶部 {vc.get('偏顶部',0)} ｜ ⚪中性 {vc.get('中性',0)}",
         f"> 底部画像=缩量0.65x+距高-36%+破MA20-10%+距低<9% ｜ 顶部画像=放量1.26x+距低+55%+乖离+12%+距高<9%",
         "",
         "## 🔴 偏底部 Top50（当前特征最像历史底部区域）",
         "",
         "| 代码 | 名称 | 价 | 底分 | 顶分 | 距一年高 | 距一年低 | 距MA20 | 量/均 |",
         "|---|---|---|---|---|---|---|---|---|"]
    for r in bots[:50]:
        L.append(f"| {r['code']} | {r['name']} | {r['px']:.2f} | **{r['bot']:.2f}** | {r['top']:.2f} "
                 f"| {r['off_hi']:+.0f}% | +{r['off_lo']:.0f}% | {r['off_ma20']:+.1f}% | {r['v_reg']:.2f}× |")
    L += ["", "## 🟢 偏顶部 Top50（当前特征最像历史顶部区域）", "",
          "| 代码 | 名称 | 价 | 底分 | 顶分 | 距一年高 | 距一年低 | 距MA20 | 量/均 |",
          "|---|---|---|---|---|---|---|---|---|"]
    for r in tops[:50]:
        L.append(f"| {r['code']} | {r['name']} | {r['px']:.2f} | {r['bot']:.2f} | **{r['top']:.2f}** "
                 f"| {r['off_hi']:+.0f}% | +{r['off_lo']:.0f}% | {r['off_ma20']:+.1f}% | {r['v_reg']:.2f}× |")
    out = os.path.join(ROOT, 'reports', f'topbot_census_{today}.md')
    open(out, 'w', encoding='utf-8').write('\n'.join(L))
    json.dump({'counts': dict(vc), 'bots': bots[:100], 'tops': tops[:100]},
              open(os.path.join(ROOT, 'data', f'topbot_census_{today}.json'), 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    print(f"[TOPBOT-CENSUS-OK] {out} | 🔴底{vc.get('偏底部',0)} 🟢顶{vc.get('偏顶部',0)} ⚪中{vc.get('中性',0)}")


if __name__ == '__main__':
    main()
