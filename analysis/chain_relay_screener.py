# -*- coding: utf-8 -*-
"""接力池每日扫描器 2026-10-10 (连板链体系呈现层落地, 零交易权)
确认池: 当日收盘涨停股 × 板数 × 封板时段(lc1可得时) → 次日连板率参考(19/35/49/61% by 板数; 早板加成)
胚胎池: 胚胎描述(H=5+链T-20口径): pos60>=60 且 0<ret20<20 且 60日板数>=2 且 当日未板 → 次日观察首板确认
输出: data/chain_relay_pool_latest.json + .txt (呈现层, 不进任何下单链路; 接权重/执行须主人裁决)
依据: reports/entry_portrait_db_20261010.md §十/§十一 + scan_fill_replay(排板判死, 扫板早板优)
"""
import json
import sqlite3
import struct
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, r'D:\zcodesj')
sys.stdout.reconfigure(encoding='utf-8')

from tdx_reader import _MIN_FMT, RECORD_SIZE, DEFAULT_TDX_ROOT

QFQ_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant_qfq.db'  # 2026-10-13 切wb唯一真源(SOP一百三十二)
ZT_DB = r'D:\zcodesj\data\zt_pool_history.db'
NAME_DB = r'D:\workbuddyspace\factor_engine\data\dragon_quant.db'
ROOT = Path(DEFAULT_TDX_ROOT)
OUT_JSON = r'D:\zcodesj\data\chain_relay_pool_latest.json'
OUT_TXT = r'D:\zcodesj\data\chain_relay_pool_latest.txt'
ZT_REF = {1: 19.4, 2: 35.1, 3: 49.4, 4: 61.0}  # 板数→次日连板率% (chain study, 封死口径)


def load_names():
    names = {}
    try:
        con = sqlite3.connect(f'file:{NAME_DB}?mode=ro', uri=True)
        for code, nm in con.execute('SELECT code, name FROM spot_daily'):
            if nm:
                names[code] = nm
        con.close()
    except sqlite3.Error:
        pass
    z = sqlite3.connect(f'file:{ZT_DB}?mode=ro', uri=True)
    row = z.execute('SELECT MAX(date) FROM zt_pool').fetchone()
    z.close()
    return names, (row[0] if row else None)


def thr_of(code, name):
    if 'ST' in (name or '').upper():
        return 4.85
    return 19.85 if code[:3] in ('300', '301') else 9.85


def seal_time(code6, day):
    """当日首次触板时刻分类; 无分钟返回N/A"""
    m = 'sh' if code6.startswith('6') else 'sz'
    p = ROOT / m / 'minline' / f'{m}{code6}.lc1'
    if not p.is_file():
        return 'N/A'
    n = p.stat().st_size // RECORD_SIZE
    with open(p, 'rb') as f:
        buf = f.read(n * RECORD_SIZE)
    prev_close = None
    prev_day_close = None
    cur_day = None
    bars = []
    for b in struct.iter_unpack(_MIN_FMT, buf):
        dnum, tmin, o, h, l, c, amt, vol, _ = b
        if dnum == 0 or o <= 0:
            continue
        year = dnum // 2048 + 2004
        md = dnum % 2048
        day_i = year * 10000 + md // 100 * 100 + md % 100
        if day_i != cur_day:
            prev_day_close = prev_close
            bars = []
            cur_day = day_i
        hh, mm = divmod(tmin, 60)
        bars.append((hh * 100 + mm, h, l, c))
        prev_close = c
        if day_i > day:
            break
    if cur_day != day or not prev_day_close:
        return 'N/A'
    name = ''
    thr = 9.85
    limit = round(prev_day_close * (1 + thr / 100) + 1e-9, 2)  # 仅时段分类, 阈值误差不影响时刻
    for (t, h, l, c) in bars:
        if h >= limit - 0.001:
            return '早板<10:00' if t < 1000 else '上午10-11:30' if t < 1131 else '午后13-14' if t < 1400 else '尾盘>=14:00'
    return 'N/A'


def main():
    names, zt_date = load_names()
    con = sqlite3.connect(f'file:{QFQ_DB}?mode=ro', uri=True)
    data = {}
    for code, d, c, p in con.execute("SELECT code,date,close,change_pct FROM stock_daily_meta_qfq "
                                     "WHERE date>='20250601' ORDER BY code,date"):
        if code[0] in '684' or code[:2] == '92':
            continue
        data.setdefault(code, ([], [], []))
        data[code][0].append(str(d))
        data[code][1].append(c)
        data[code][2].append(p)
    con.close()

    # 取最新有数据日
    latest = max(ds[-1] for ds, _, _ in data.values())
    confirm, embryo = [], []
    st_excluded = 0
    for code, (ds, cs, ps) in data.items():
        if not ds or ds[-1] != latest:
            continue
        i = len(ds) - 1
        p = ps[i]
        if p is None or i < 60:
            continue
        name = names.get(code, code)
        if 'ST' in name.upper():
            st_excluded += 1
            continue
        thr = thr_of(code, name)
        if p >= thr:  # 确认池
            cz = 0
            for k in range(i, -1, -1):
                if ps[k] is not None and ps[k] >= thr:
                    cz += 1
                else:
                    break
            tcat = seal_time(code, int(latest))
            confirm.append({'code': code, 'name': name, 'boards': cz, 'pct': p,
                            'seal_time': tcat,
                            'next_zt_ref': ZT_REF.get(min(cz, 4), ZT_REF[4]),
                            'early': int(str(tcat).startswith('早'))})
        else:  # 胚胎池
            cl = cs[i]
            w = cs[i - 59:i + 1]
            hi_, lo_ = max(w), min(w)
            pos60 = 100.0 * (cl - lo_) / (hi_ - lo_) if hi_ > lo_ else None
            ret20 = 100.0 * (cl / cs[i - 20] - 1)
            zt60 = sum(1 for x in ps[i - 59:i + 1] if x is not None and x >= thr)
            if pos60 is not None and pos60 >= 60 and 0 < ret20 < 20 and zt60 >= 2:
                embryo.append({'code': code, 'name': name, 'pos60': round(pos60, 0),
                               'ret20': round(ret20, 1), 'zt60': zt60, 'pct': p})
    confirm.sort(key=lambda x: (-x['boards'], -x['early'], x['seal_time']))
    embryo.sort(key=lambda x: (-x['zt60'], -x['pos60']))

    out = {'date': latest, 'zt_pool_date': zt_date, 'st_excluded': st_excluded,
           'note': '呈现层零交易权; 宇宙剔ST/科创/北交; 确认池=当日涨停(封死口径), next_zt_ref=链研究次日连板率参考; '
                   '胚胎池=高位缓升+板史(H5+胚胎描述), 观察首板确认; 排板已被分钟回放判死, 介入仅盘中扫板域待主人裁决',
           'confirm_pool': confirm, 'embryo_pool': embryo}  # 全量无截断: 哨监测与影子池内口径依赖完整名单
    json.dump(out, open(OUT_JSON, 'w', encoding='utf-8'), ensure_ascii=False, indent=1)
    # 按日归档池: 影子[chain_shadow]处理T日须读T-1晚归档池(防latest被当晚覆盖造成前视)
    json.dump(out, open(rf'D:\zcodesj\data\chain_relay_pool_{latest}.json', 'w', encoding='utf-8'),
              ensure_ascii=False, indent=1)
    lines = [f'接力池 {latest} (确认{len(confirm)}只/胚胎{len(embryo)}只/剔ST{st_excluded}只) —— 呈现层零交易权',
             f'次 日 连 板 率 参 考: 首板19% 2板35% 3-5板49% 6板+61% (封死口径); 早板封死f1med+3.4%优于尾盘+1.2%',
             '', '== 确认池(按板数降序, 最多40) ==']
    for r in confirm[:40]:
        lines.append(f"{r['name']}({r['code']}) {r['boards']}板 {r['pct']:+.1f}% {r['seal_time']} 参考连板率≈{r['next_zt_ref']:.0f}%")
    lines.append('')
    lines.append('== 胚胎池(高位缓升+60日板史≥2, 未板观察, 最多30) ==')
    for r in embryo[:30]:
        lines.append(f"{r['name']}({r['code']}) pos60={r['pos60']:.0f} ret20={r['ret20']:+.1f}% 板史{r['zt60']} 今日{r['pct']:+.1f}%")
    open(OUT_TXT, 'w', encoding='utf-8').write('\n'.join(lines))
    print(f'[chain_pool] {latest}: 确认池{len(confirm)} 胚胎池{len(embryo)} → data/chain_relay_pool_latest.txt')


if __name__ == '__main__':
    main()
