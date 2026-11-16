# -*- coding: utf-8 -*-
"""vipdoc_friday_probe.py — 周五盘后 vipdoc 日线下载探针（schtasks 工作日 21:00 触发）。

背景：9/11(周五) 晚 23:30 链跑时 vipdoc 无当日线（通达信客户端未下载）→ 数据链静默降级
至 9/10（falsified 见 SOP 十五/十八；23:30 链对账告警为事后兜底）。本探针=提前发现层：
周五 21:00 检测 vipdoc 是否已有当日线，缺失则 toast 提醒主人打开通达信补数据。

判定（双源交叉，防节假日误报）：
- 仅周五运行（其他工作日通达信盘中/盘后常开，历史无此缺口；且 23:30 对账告警兜底）。
- vipdoc 指数日线 MAX == 今日 → OK 退出（数据已就位）。
- vipdoc MAX < 今日 时看 baostock 独立源（daily_basic_meta，与通达信无依赖）：
  baostock MAX == 今日 → 今日确为交易日而 tdx 未下载 → toast 提醒（exit 2）；
  baostock MAX < 今日 → 今日非交易日或源整体延迟 → 静默退出不误报。
退出码：0=正常/跳过；2=缺当日线已告警。
用法：python vipdoc_friday_probe.py [--force]（--force 绕过周五校验，供验收）
"""
import os, sys, struct, datetime, sqlite3, subprocess

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

VIPDOC_DAY = r'D:/anzhuang/tdx/vipdoc/sh/lday/sh000001.day'
BAOSTOCK_DB = r'D:/workbuddyspace/factor_engine/data/dragon_quant_meta.db'
NOTIFY_PY = r'D:/zcodesj/notify.py'


def vipdoc_max_date():
    dates = []
    with open(VIPDOC_DAY, 'rb') as f:
        data = f.read()
    rec = struct.Struct('<IIIIIfII')
    for i in range(0, len(data) - 31, 32):
        d = rec.unpack_from(data, i)[0]
        if d > 19900101:
            dates.append(d)
    return max(dates) if dates else 0


def baostock_max_date():
    con = sqlite3.connect(BAOSTOCK_DB, timeout=10)
    r = con.execute('SELECT MAX(date) FROM stock_daily_meta').fetchone()[0]
    con.close()
    return int(str(r).replace('-', '')[:8])


def main():
    ap = sys.argv[1:]
    force = '--force' in ap
    now = datetime.datetime.now()
    today = int(now.strftime('%Y%m%d'))
    if now.weekday() != 4 and not force:
        print(f'[probe] 非周五({now.weekday()})，跳过')
        return 0
    v = vipdoc_max_date()
    b = baostock_max_date()
    print(f'[probe] today={today} vipdoc_max={v} baostock_max={b}')
    if v >= today:
        print('[probe] OK: vipdoc 已有当日线')
        return 0
    if b >= today:
        msg = (f'通达信 vipdoc 缺 {today} 日线(vipdoc_max={v}, baostock 已有 {b})，'
               f'今晚 23:30 链将数据降级——请打开通达信补盘后数据')
        print(f'[probe] ALERT: {msg}')
        try:
            subprocess.run([sys.executable, NOTIFY_PY, 'send',
                            '--title', 'vipdoc 缺当日线提醒',
                            '--content', msg, '--level', 'warning'], timeout=60)
        except Exception as e:
            print(f'[probe] notify 失败: {e}')
        return 2
    print('[probe] 今日非交易日或源整体未更新，静默退出（不误报）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
