# -*- coding: utf-8 -*-
"""白名单画像独立标注列(观测级接入①, 主人2026-09-05指令): 给 reports/buy_whitelist_*.csv
追加 pa_ 前缀独立列(可排序筛选), 不动 tulong_buy_whitelist.py 生成器(owner=并行链路, note内嵌标注保留).
数据唯一来源=data/portrait_feed.json(portrait_feed.py=画像→屠龙唯一对接owner, 本脚本不读画像内部文件).
幂等: 已有 pa_ 列则刷新值; 备份.pa_bak保留上一版; 白名单票不在画像宇宙(>20元/次新/ST等)标NA+原因."""
import os, sys, json, glob, csv, shutil, time
sys.stdout.reconfigure(encoding='utf-8')
BASE = r'D:\zcodesj'
FEED = os.path.join(BASE, 'data', 'portrait_feed.json')

def main():
    feed = json.load(open(FEED, encoding='utf-8'))
    if feed.get('unavailable'):
        print(f'[annotate] feed不可用: {feed["unavailable"]} — 不标注(fail-open)'); return 1
    s_short, s_mid = feed.get('scores_short', {}), feed.get('scores_mid', {})
    snap = feed.get('snapshot_date')
    cands = sorted(glob.glob(os.path.join(BASE, 'reports', 'buy_whitelist_*.csv')))
    if not cands:
        print('[annotate] 无白名单文件'); return 1
    f = cands[-1]
    with open(f, encoding='utf-8-sig', newline='') as fh:
        rd = csv.reader(fh); rows = list(rd)
    hdr = rows[0]
    if 'code' not in hdr:
        print('[annotate] 白名单无code列'); return 1
    ci = hdr.index('code')
    new_cols = ['pa_short_prob', 'pa_short_pct', 'pa_mid_pct', 'pa_snapshot_date']
    if not all(c in hdr for c in new_cols):
        shutil.copyfile(f, f + '.pa_bak')
        rows[0] = hdr + new_cols
        for r in rows[1:]:
            r.extend([''] * len(new_cols))
        hdr = rows[0]
    idx = {c: hdr.index(c) for c in new_cols}
    n_hit = n_miss = 0
    for r in rows[1:]:
        code = r[ci].zfill(6)
        ss, sm = s_short.get(code), s_mid.get(code)
        if ss:
            r[idx['pa_short_prob']] = f"{ss['prob']:.3f}"
            r[idx['pa_short_pct']] = f"{ss['pct']:.1f}"
            r[idx['pa_mid_pct']] = f"{sm['pct']:.1f}" if sm else 'NA'
            r[idx['pa_snapshot_date']] = str(snap)
            n_hit += 1
        else:
            r[idx['pa_short_prob']] = 'NA(不在画像宇宙)'
            r[idx['pa_mid_pct']] = r[idx['pa_snapshot_date']] = ''
            n_miss += 1
    with open(f, 'w', encoding='utf-8-sig', newline='') as fh:
        csv.writer(fh).writerows(rows)
    print(f'[annotate] {os.path.basename(f)}: 标注{n_hit}只/NA{n_miss}只 (截面{snap}, 影子{feed.get("shadow",{}).get("phase")})')
    return 0

if __name__ == '__main__':
    sys.exit(main())
