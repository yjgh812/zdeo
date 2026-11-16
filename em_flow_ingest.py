import sqlite3, json, sys

DB_PATH = r'D:\zcodesj\data\em_flow_snapshot.db'

SCHEMA = '''CREATE TABLE IF NOT EXISTS flow_snapshot (
    ts TEXT, trade_date TEXT, code TEXT, name TEXT,
    em_today REAL, em_5d REAL, em_10d REAL, em_20d REAL,
    ths_5d REAL, ths_3d REAL, ths_10d REAL, limit_days_5 INT,
    PRIMARY KEY(code,trade_date)
)'''

def init_db():
    with sqlite3.connect(DB_PATH) as con:
        con.execute(SCHEMA)

def upsert(row):
    required = ['ts','trade_date','code','name','em_today','em_5d','em_10d','em_20d',
                'ths_5d','ths_3d','ths_10d','limit_days_5']
    for k in required:
        if k not in row: row[k] = row.get(k) or (0 if 'em' in k or 'ths' in k or 'limit' in k else '')
    cols=','.join(required)
    ph=','.join('?' for _ in required)
    with sqlite3.connect(DB_PATH) as con:
        con.execute(f'INSERT INTO flow_snapshot ({cols}) VALUES ({ph}) '
                    f'ON CONFLICT(code,trade_date) DO UPDATE SET '
                    f'{",".join(f"{c}=excluded.{c}" for c in required)}',
                    [row[k] for k in required])

def get_snapshot(code):
    with sqlite3.connect(DB_PATH) as con:
        r = con.execute('SELECT * FROM flow_snapshot WHERE code=? ORDER BY trade_date DESC LIMIT 1',(code,)).fetchone()
    return r

def main():
    if sys.argv[1:2]==['--init']: init_db(); print(f'{DB_PATH} schema ready'); return
    if sys.argv[1:2]==['--ingest']:
        rows=json.loads(sys.stdin.read())
        for r in rows: upsert(r)
        print(f'{len(rows)} rows upserted')
        return
    if sys.argv[1:2]==['--show']:
        with sqlite3.connect(DB_PATH) as con:
            for r in con.execute('SELECT code,name,em_5d,ths_5d,limit_days_5 FROM flow_snapshot ORDER BY trade_date DESC').fetchall():
                print(f'{r[0]:>8} {r[1]:<8} em5={r[2]:>12,.0f} ths5={r[3]:>12,.0f} ld={r[4]}')
        return
    print('Usage: --init | --ingest <json | --show')

if __name__=='__main__': main()