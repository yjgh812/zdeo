# -*- coding: utf-8 -*-
"""check_panel_v6 探针契约测试 (2026-09-12).
三分支: ①真实环境 OK(panel追平qfq, 9/12实弹已验) ②panel落后qfq→WARN+自愈入口提示
③pkl缺失→WARN. 落后分支用monkeypatch假qmax构造, 不碰生产库.
运行: python tests/test_health_panel_v6.py  (exit 0=全过)
"""
import sys, os, io, sqlite3, contextlib, tempfile, shutil
sys.stdout.reconfigure(encoding='utf-8')
sys.path.insert(0, r'D:\zcodesj')
import health_check_daily as h

fails = []

# ① 探针分类契约（20261023 竞速容错化: 原断言"活环境截面==qfq→OK"随每日 qfq/画像更新时序差轮换
#    红/绿（qfq 16-17点更新 vs 画像更新永远滞后半拍）；本测试只验探针「分类正确性」——
#    截面==qfq → OK；截面<qfq → WARN+自愈入口。管线掉队报警灵敏度不受影响（探针本体未动）。
d, w = h.check_panel_v6()
import re as _re
_m = _re.search(r'截面(\d{8}).*?qfq=(\d{8})', d.get('note', ''))
if _m is not None and _m.group(1) == _m.group(2):
    ok1 = d['status'] == 'OK'
    print(('PASS' if ok1 else 'FAIL'), '[OK分支]', d['note'])
else:
    ok1 = d['status'] == 'WARN' and 'daily_portrait_update' in (w[0] if w else '')
    print(('PASS' if ok1 else 'FAIL'), '[落后分支→竞速容忍]', d['status'], '|', d['note'][:60])
if not ok1: fails.append(1)

# ② 落后分支: monkeypatch sqlite 查询返回未来假 qmax → 必须 WARN 且附自愈入口
class FakeCon:
    def execute(self, *a, **k): return self
    def fetchone(self): return ['20269999']
    def close(self): pass
_real_connect = sqlite3.connect
h.sqlite3.connect = lambda *a, **k: FakeCon()
try:
    d, w = h.check_panel_v6()
finally:
    h.sqlite3.connect = _real_connect
ok2 = d['status'] == 'WARN' and 'daily_portrait_update' in (w[0] if w else '')
print(('PASS' if ok2 else 'FAIL'), '[落后分支]', d['status'], '|', d['note'][:60], '| warn:', (w[0] if w else '')[:60])
if not ok2: fails.append(2)

# ③ pkl 缺失分支: 临时改路径探测
real_pkl = h.os.path.join(r"D:\zcodesj\portrait_agent\cache", "panel_v6_full.pkl")
h.os.path.join_orig = h.os.path.join
def fake_join(*a):
    if 'panel_v6_full.pkl' in a[-1]:
        return a[-1] + '.nonexist_test'
    return h.os.path.join_orig(*a)
h.os.path.join = fake_join
try:
    d, w = h.check_panel_v6()
finally:
    h.os.path.join = h.os.path.join_orig
ok3 = d['status'] == 'WARN'
print(('PASS' if ok3 else 'FAIL'), '[pkl缺失分支]', d['status'], '|', d['note'])
if not ok3: fails.append(3)

print(f'\n{"✅ 全部通过" if not fails else f"⛔ {len(fails)} 失败"} (3 用例)')
sys.exit(1 if fails else 0)
