# -*- coding: utf-8 -*-
"""画像系统日更一键入口 (2026-10-02 已挂调度: automation-3ad6e95b 影子晨跑六步机第7步, 每交易日08:10).

链条: chan_batch重跑(约7min) -> panel_v6_update(增量) -> panel_v6 parquet投影 -> 引擎A影子扫描 -> 组合C影子信号
用法: python daily_portrait_update.py [--skip-chan]   # --skip-chan: 无新交易日/缠论无需更新时
日志: data/portrait_daily_<date>.log ; 任一步失败即停(exit code 传递), 幂等可重跑.
2026-09-25: 本文件随线B直跑件迁入 v6_research/, STEPS 路径同步更新.
2026-10-02: 挂载收口——此前"手工入口待裁决"状态致 panel_v6 截面长期靠 health_check WARN 人工补
            (截面断粮饿死引擎A/组合C两影子, U24断粮的上游同族缺口); 实弹 rc=0 五步全绿后入链.
"""
import sys, os, subprocess, time, argparse
sys.stdout.reconfigure(encoding='utf-8')
HERE = r'D:\zcodesj\portrait_agent'
PA = r'D:\zcodesj\portrait_agent'
V6R = r'D:\zcodesj\portrait_agent\v6_research'
import datetime
DAY = datetime.datetime.now().strftime('%Y%m%d')
LOG = rf'D:\zcodesj\data\portrait_daily_{DAY}.log'

STEPS = [
    ('chan_batch 重跑(缠论缓存全史)', [sys.executable, '-u', os.path.join(V6R, 'chan_batch.py')]),
    ('panel_v6 增量更新', [sys.executable, '-u', os.path.join(V6R, 'panel_v6_update.py')]),
    # 2026-09-24 接线: pkl更新后parquet研究侧投影此前无任何日更接线, 长期落后只能靠
    # health_check WARN人工补(9/24实证: 22:47 WARN后手工跑)。紧跟panel_v6, 同享commit预检。
    ('panel_v6 parquet投影刷新', [sys.executable, '-u', os.path.join(V6R, 'panel_v6_to_parquet.py')]),
    ('引擎A 影子扫描', [sys.executable, '-u', os.path.join(V6R, 'engine_a_shadow_scan.py')]),  # 2026-09-16移v6_research/, 2026-09-19修引用断链(影子台账停在0914的根因)
    ('组合C 影子信号', [sys.executable, '-u', os.path.join(V6R, 'combo_c_shadow.py'), '--signal']),
]

def _commit_avail_gb():
    """2026-09-23 加: 系统commit余量预检(panel_v6需~15GB峰值; 9/23凌晨实锤commit见顶致OOM/进程被杀)"""
    import ctypes, ctypes.wintypes as wt
    class MS(ctypes.Structure):
        _fields_ = [('dwLength', wt.DWORD), ('dwMemoryLoad', wt.DWORD),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    m = MS(); m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPageFile / 2**30

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--skip-chan', action='store_true')
    a = ap.parse_args()
    with open(LOG, 'a', encoding='utf-8') as lf:
        for name, cmd in STEPS:
            if a.skip_chan and any('chan_batch' in x for x in cmd):
                lf.write(f'\n[SKIP] {name}\n'); print(f'[SKIP] {name}'); continue
            # commit 预检(仅内存敏感的 panel_v6/chan_batch; 不足则明确 SKIP 留痕而非半途 OOM)
            if any(k in name for k in ('panel_v6', 'chan_batch')):
                avail = _commit_avail_gb()
                if avail < 25:
                    msg = f'[SKIP-COMMIT] {name}: commit余量{avail:.1f}GB<25GB(多会话MCP占用), 请关闭闲置会话后重跑'
                    lf.write(f'\n{msg}\n'); print(msg)
                    continue
            lf.write(f'\n==== {name} : {time.strftime("%H:%M:%S")} ====\n'); print(f'==== {name} ...', flush=True)
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=7200,
                               cwd=r'D:\zcodesj', encoding='utf-8', errors='replace')
            lf.write((r.stdout or '') + (r.stderr or ''))
            if r.returncode != 0:
                lf.write(f'\n[FAIL] {name} exit={r.returncode}\n')
                print(f'[FAIL] {name} exit={r.returncode}, 详见 {LOG}')
                sys.exit(r.returncode)
            lf.write(f'[OK] {name}\n'); print(f'[OK] {name}')
        lf.write(f'\n==== 日更完成 {time.strftime("%H:%M:%S")} ====\n')
    print(f'日更完成, 日志 {LOG}')

if __name__ == '__main__':
    main()
