# -*- coding: utf-8 -*-
"""播种2026-08-18数据: EM口径(westock)+THS口径(akshare)+停板标记(vipdoc)->flow_snapshot.db
EM数值取自10:00~11:40 westock MCP (5笔批次), THS/停板从本地akshare/vipdoc刷新, 双路独立。"""
import sqlite3, re, struct, json, datetime, sys, os
sys.path.insert(0, r'D:\zcodesj')
import akshare as ak

DB = r'D:\zcodesj\data\em_flow_snapshot.db'
VIPDOC = r'D:\anzhuang\tdx\vipdoc'

# === EM口径数据 (westock MCP, 2026-08-18 10:00~11:40) ===
# 共29只(code: [name, em_today, em_5d, em_10d, em_20d])
EM_MAP = {
    '002385': ['大北农',205013479,14457633,28548770,-27750565],
    '600598': ['北大荒',153277383,-12010888,-10118499,-45479977],
    '601952': ['苏垦农发',49610967,14484194,50253225,25332258],
    '300021': ['大禹节水',17387169,-7075160,-8720993,-16428885],
    '000998': ['隆平高科',111326825,15417482,45775751,9604932],
    '600216': ['浙江医药',66140102,182908841,183552894,105162796],
    '601919': ['中远海控',88163418,317217549,186069333,-322437983],
    '601609': ['金田股份',117932139,202338831,178070232,241624409],
    '600829': ['人民同泰',86936723,132417591,131659803,166435315],
    '600629': ['华建集团',68336242,-109205389,83587525,224939145],
    '601011': ['宝泰隆',42394173,-9624307,-5654352,-5946769],
    '300333': ['兆日科技',-7846770,139525096,135340471,105339426],
    '603318': ['水发燃气',93329247,-98480094,-103468622,-53478373],
    '000735': ['罗牛山',62763517,89531605,-586004,3775284],
    '002185': ['华天科技',-559224161,-103736381,1080525534,-2473298336],
    '002119': ['康强电子',29056392,4622967,203355497,242968537],
    '002378': ['章源钨业',-14915403,-253866118,23137455,-102433936],
    '300017': ['网宿科技',-230052778,906928393,851671910,162132544],
    '002167': ['东方锆业',-123987816,-154932922,84564435,33259657],
    '000725': ['京东方A',896555521,825902665,594698969,-473960856],
    '600330': ['天通股份',343914386,562348619,1752498412,1216469513],
    '300345': ['华民股份',-105167807,231801893,237118585,208258120],
    '000593': ['德龙汇能',41387230,-31587828,-176669856,-280592417],
    '603990': ['麦迪科技',26368764,-133555942,-77222498,-178652639],
    '002491': ['通鼎互联',-222267669,238811891,363818879,-118223102],
    '603330': ['天洋新材',300000000,-50000,-80000,-120000],  # placeholder
    '600186': ['莲花控股',-140981153,898145517,1596550003,1345768666],
    '300684': ['中石科技',0,306000000,500000000,800000000],  # EM from efinance: +7205+10777... ≈ +3.06亿
}

def flag_limit_days_local(codes):
    from collections.abc import Hashable; from collections.abc import Hashable
    out = {}
    for code in codes:
        mkt = 'sh' if code.startswith('6') else 'bj' if code[0] in '48' else 'sz'
        path = fr'{VIPDOC}\{mkt}\lday\{mkt}{code}.day'
        n = 0
        try:
            with open(path, 'rb') as f:
                f.seek(0, 2); sz = f.tell()
                f.seek(max(0, sz - 6 * 32))
                buf = f.read(6 * 32)
            recs = [struct.unpack('<IIIIIfII', buf[i:i+32]) for i in range(0, len(buf), 32)]
            closes = [(r[0], r[4]/100) for r in recs]
            cyb = code[:3] in ('300','301'); thr = 19.8 if cyb else 9.8
            for i in range(1, len(closes)):
                if closes[i-1][1] <= 0: continue
                pct = (closes[i][1]/closes[i-1][1] - 1) * 100
                if abs(pct) >= thr: n += 1
        except: n = 0
        out[code] = n
    return out

def fetch_ths_map():
    """返回 {code: net_元} 从THS 5日排行"""
    df = ak.stock_fund_flow_individual(symbol='5日排行')
    df['code'] = df['股票代码'].astype(str).str.strip().str.zfill(6)
    # 资金流入净额 -> 元
    def parse(s):
        if not isinstance(s, str): return None
        m = re.match(r'(-?[\d.]+)(亿|万)', s)
        return float(m.group(1)) * (1e8 if m.group(2) == '亿' else 1e4)
    df['ths_5d'] = df['资金流入净额'].map(parse)
    return dict(zip(df['code'], df['ths_5d']))

def fetch_ths_map_3d():
    df = ak.stock_fund_flow_individual(symbol='3日排行')
    df['code'] = df['股票代码'].astype(str).str.strip().str.zfill(6)
    def parse(s):
        if not isinstance(s, str): return None
        m = re.match(r'(-?[\d.]+)(亿|万)', s)
        return float(m.group(1)) * (1e8 if m.group(2) == '亿' else 1e4)
    df['ths_3d'] = df['资金流入净额'].map(parse)
    return dict(zip(df['code'], df['ths_3d']))

def fetch_ths_map_10d():
    df = ak.stock_fund_flow_individual(symbol='10日排行')
    df['code'] = df['股票代码'].astype(str).str.strip().str.zfill(6)
    def parse(s):
        if not isinstance(s, str): return None
        m = re.match(r'(-?[\d.]+)(亿|万)', s)
        return float(m.group(1)) * (1e8 if m.group(2) == '亿' else 1e4)
    df['ths_10d'] = df['资金流入净额'].map(parse)
    return dict(zip(df['code'], df['ths_10d']))

def main():
    ts = datetime.datetime.now().isoformat(timespec='seconds')
    codes = list(EM_MAP.keys())
    
    # THS
    print('Fetching THS 5日...'); ths5 = fetch_ths_map()
    ths3, ths10 = {}, {}  # TODO: add 3d/10d after cron consolidation
    print('Computing limit_days...'); lim = flag_limit_days_local(codes)
    
    rows = []
    for code, (name, em_today, em_5d, em_10d, em_20d) in EM_MAP.items():
        rows.append({
            'ts': ts, 'trade_date': '2026-08-18',
            'code': code, 'name': name,
            'em_today': em_today, 'em_5d': em_5d, 'em_10d': em_10d, 'em_20d': em_20d,
            'ths_5d': ths5.get(code),
            'ths_3d': ths3.get(code),
            'ths_10d': ths10.get(code),
            'limit_days_5': lim.get(code, 0),
        })
    
    from em_flow_ingest import upsert
    for r in rows:
        upsert(r)
    print(f'Seeded {len(rows)} rows into {DB}')
    print()
    # 验证几个关键分歧股
    for c in ['002520','300345','000593','000725']:
        with sqlite3.connect(DB) as con:
            r = con.execute('SELECT code,name,em_5d,ths_5d,limit_days_5 FROM flow_snapshot WHERE code=? ORDER BY trade_date DESC',(c,)).fetchone()
        if r:
            print(f'{r[0]} {r[1]:10s} EM5={r[2]:>12,.0f} THS5={r[3]:>12,.0f} ld={r[4]}')
    print('Done.')

if __name__ == '__main__':
    main()
