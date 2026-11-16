# -*- coding: utf-8 -*-
"""py_compile_guard.py — git 跟踪 Python 面全量编译完整性守卫(2026-09-08 接入).

背景: 语法损坏文件会静默积累——daily_signal_production.py/v2 弃用后 __future__ 被
第二个字符串语句隔断, 无法编译状态存续 17 天(8/22~9/8)无人发现, 直到一次性全库
审查才暴露. 此前无任何 owner 检查"跟踪面上的 .py 是否全部可编译".
口径: 只查 git ls-files 跟踪面(与人工审查同口径), 排除 archive/qlib-learning/
_ts_test_20260905; 逐文件 py_compile(doraise), 读失败=计入错误(无法保证可运行).
阈值: 语法错误>0 → FAIL(exit 2, 代码真相级); 0 错误 → OK(带 SyntaxWarning 计数
参考, 不改变级别——存量 warning 已定性入债务台账); git 不可用/跟踪面为空 →
UNAVAILABLE(不伪健康).
实测: 841 文件全量编译约 2.1s, 可入每日巡检.
用法:
  python py_compile_guard.py [--repo D:\\zcodesj]
接线: health_check_daily.check_py_compile_guard(run_check import; FAIL→ERROR
代码真相级, UNAVAILABLE→WARN, 守卫异常 fail-open WARN 不阻断巡检).
"""
import os
import subprocess
import sys

sys.stdout.reconfigure(encoding="utf-8")

SKIP_PREFIXES = ("archive/", "qlib-learning/", "_ts_test_20260905/")


def run_check(repo=r"D:\zcodesj"):
    """编译完整性守卫核心(可 import 复用, health_check_daily 接线用). 返回:
    {status: OK|FAIL|UNAVAILABLE, errors: int|None, detail: str}"""
    try:
        p = subprocess.run(["git", "ls-files", "*.py"], cwd=repo,
                           capture_output=True, text=True, timeout=60)
        files = [f for f in p.stdout.splitlines()
                 if f and not f.startswith(SKIP_PREFIXES)]
    except Exception as e:
        return {"status": "UNAVAILABLE", "errors": None,
                "detail": f"git ls-files 不可用: {str(e)[:80]}"}
    if p.returncode != 0 or not files:
        return {"status": "UNAVAILABLE", "errors": None,
                "detail": f"git 跟踪面为空或不可用(rc={p.returncode}), 无法评估"}
    import py_compile
    import warnings
    errors = []
    missing = []  # 2026-09-19: 索引有但工作区缺失=未提交的删除/移动(如v6_research迁移), 非语法损坏, 单列附注不FAIL
    with warnings.catch_warnings(record=True) as wlist:
        warnings.simplefilter("always")
        for f in files:
            fp = os.path.join(repo, f)
            if not os.path.exists(fp):
                missing.append(f)
                continue
            try:
                py_compile.compile(fp, doraise=True)
            except Exception as e:
                errors.append(f"{f}: {str(e)[:80]}")
    n_warn = sum(1 for w in wlist if issubclass(w.category, SyntaxWarning))
    if errors:
        return {"status": "FAIL", "errors": len(errors),
                "detail": f"{len(files)}个跟踪py中{len(errors)}个无法编译: "
                          + "; ".join(errors[:3])}
    tail = f"; 存量SyntaxWarning={n_warn}(已定性债务,不改级别)" if n_warn else ""
    if missing:
        tail += (f"; 另有{len(missing)}个索引文件工作区缺失=未提交删除/移动待git收口"
                 f"(非语法损坏): {missing[0]} 等")
    return {"status": "OK", "errors": 0,
            "detail": f"{len(files)}个跟踪py全量编译通过{tail}"}


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=r"D:\zcodesj")
    args = ap.parse_args()
    r = run_check(args.repo)
    print(f"[py_compile] {r['status']}: {r['detail']}")
    if r["status"] == "UNAVAILABLE":
        print("⚠️ UNAVAILABLE — git 跟踪面不可评估(不伪健康)")
        sys.exit(2)
    if r["status"] == "FAIL":
        print("❌ FAIL — 存在无法编译的跟踪文件, 禁止假定其可运行, 先修复或归档")
        sys.exit(2)
    sys.exit(0)


if __name__ == "__main__":
    main()
