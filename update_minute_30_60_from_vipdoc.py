# -*- coding: utf-8 -*-
"""update_minute_30_60_from_vipdoc.py - vipdoc .lc5 聚合 30/60 分钟 K 线入 minute_archive.db"""
import os, sys, json, sqlite3, struct, time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
ROOT = r"D:\zcodesj"
VIPDOC = r"D:\anzhuang\tdx\vipdoc"
ARCHIVE = os.path.join(ROOT, "data", "minute_archive.db")
DRAGON = r"D:\workbuddyspace\factor_engine\data\dragon_quant.db"
SUMMARY = os.path.join(ROOT, "data", "update_minute_30_60_summary.json")
LOG = os.path.join(ROOT, "data", "update_minute_30_60.log")
REC = struct.Struct("<HHfffffII")
COMMIT_EVERY = 300
PROGRESS_EVERY = 500


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("[%Y-%m-%d ") + line[9:] + "\n")


def load_st_list():
    con = sqlite3.connect(f"file:{DRAGON}?mode=ro", uri=True)
    st = set()
    for r in con.execute("select code from stock_info where is_st=1"):
        st.add(r[0])
    for r in con.execute("select code from spot_daily where name like ? or name like ?", ("%ST%", "%退%")):
        st.add(r[0])
    con.close()
    return st


def open_archive():
    con = sqlite3.connect(ARCHIVE)
    con.executescript("""CREATE TABLE IF NOT EXISTS bars30 (
code TEXT NOT NULL, day INTEGER NOT NULL, tmin INTEGER NOT NULL,
o REAL, h REAL, l REAL, c REAL, vol INTEGER, PRIMARY KEY(code,day,tmin)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta30 (code TEXT PRIMARY KEY, last_day INTEGER, bars INTEGER);
CREATE TABLE IF NOT EXISTS bars60 (
code TEXT NOT NULL, day INTEGER NOT NULL, tmin INTEGER NOT NULL,
o REAL, h REAL, l REAL, c REAL, vol INTEGER, PRIMARY KEY(code,day,tmin)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS meta60 (code TEXT PRIMARY KEY, last_day INTEGER, bars INTEGER);""")
    return con


def bucket30(t):
    return ((t - 1) // 30) * 30 + 30


def bucket60(t):
    if t <= 630: return 630
    if t <= 690: return 690
    if t > 840: return 900
    return 840


def build_universe():
    uni, skipped = [], {"not_a_share": 0, "star": 0}
    for mkt, pfx in (("sh", ("60",)), ("sz", ("00", "30"))):
        d = os.path.join(VIPDOC, mkt, "fzline")
        for fn in os.listdir(d):
            if not fn.endswith(".lc5"): continue
            code = fn[2:8]
            if code[:2] not in pfx:
                if code[:3] in ("688", "689"): skipped["star"] += 1
                else: skipped["not_a_share"] += 1
                continue
            uni.append((code, os.path.join(d, fn)))
    return uni, skipped


def aggregate_file(path):
    with open(path, "rb") as f:
        data = f.read()
    n = len(data) // 32
    a30, a60 = {}, {}
    for d_raw, t_raw, o, h, l, c, amt, vol, x in REC.iter_unpack(data[: n * 32]):
        if vol == 0 and c == 0: continue
        yr = 2004 + (d_raw >> 11)
        md = d_raw & 0x7FF
        day = yr * 10000 + md // 100 * 100 + md % 100
        t = t_raw
        if not (575 <= t <= 900): continue
        b30 = ((t - 1) // 30) * 30 + 30
        if t <= 630: b60 = 630
        elif t <= 690: b60 = 690
        elif t <= 840: b60 = 840
        else: b60 = 900
        k = (day, b30)
        cur = a30.get(k)
        if cur is None:
            a30[k] = [o, h, l, c, vol]
        else:
            if h > cur[1]: cur[1] = h
            if l < cur[2]: cur[2] = l
            cur[3] = c
            cur[4] += vol
        k = (day, b60)
        cur = a60.get(k)
        if cur is None:
            a60[k] = [o, h, l, c, vol]
        else:
            if h > cur[1]: cur[1] = h
            if l < cur[2]: cur[2] = l
            cur[3] = c
            cur[4] += vol
    l30 = [(d, b, v[0], v[1], v[2], v[3], v[4]) for (d, b), v in sorted(a30.items())]
    l60 = [(d, b, v[0], v[1], v[2], v[3], v[4]) for (d, b), v in sorted(a60.items())]
    return l30, l60


def main():
    t0 = time.time()
    st = load_st_list()
    log(f"ST 名单 {len(st)} 只 (stock_info.is_st 并 spot 名称含ST/退)")
    uni, skipped = build_universe()
    st_cnt = sum(1 for c, _ in uni if c in st)
    log(f"宇宙 {len(uni)} 只 (60/00/30); 跳过 {skipped}; ST 剔除 {st_cnt}")
    con = open_archive()
    n30 = n60 = written = st_excluded = 0
    err = []
    for i, (code, path) in enumerate(uni, 1):
        if code in st:
            st_excluded += 1
            continue
        try:
            b30, b60 = aggregate_file(path)
        except Exception as e:
            err.append({"code": code, "err": str(e)[:200]})
            continue
        if b30:
            con.executemany("INSERT OR REPLACE INTO bars30(code,day,tmin,o,h,l,c,vol) VALUES(?,?,?,?,?,?,?,?)",
                            [(code, d, b, v[0], v[1], v[2], v[3], v[4]) for (d, b, *v) in b30])
            con.execute("INSERT OR REPLACE INTO meta30(code,last_day,bars) VALUES(?,?,?)", (code, b30[-1][0], len(b30)))
            n30 += len(b30)
        if b60:
            con.executemany("INSERT OR REPLACE INTO bars60(code,day,tmin,o,h,l,c,vol) VALUES(?,?,?,?,?,?,?,?)",
                            [(code, d, b, v[0], v[1], v[2], v[3], v[4]) for (d, b, *v) in b60])
            con.execute("INSERT OR REPLACE INTO meta60(code,last_day,bars) VALUES(?,?,?)", (code, b60[-1][0], len(b60)))
            n60 += len(b60)
        written += 1
        if written % COMMIT_EVERY == 0:
            con.commit()
        if i % PROGRESS_EVERY == 0:
            log(f"进度 {i}/{len(uni)} 30min={n30} 60min={n60}")
    con.commit()
    con.close()
    summary = {"as_of": time.strftime("%Y-%m-%d %H:%M:%S"), "universe_count": len(uni),
               "st_excluded": st_excluded, "skipped": skipped, "stocks_written": written,
               "bars30_rows": n30, "bars60_rows": n60, "error_count": len(err),
               "errors": err[:20], "elapsed_sec": round(time.time() - t0, 1)}
    with open(SUMMARY, "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2)
    log("完成: " + json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
