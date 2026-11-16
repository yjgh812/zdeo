# -*- coding: utf-8 -*-
"""py_compile_guard 四分支契约测试（2026-09-08，照守卫接线合同）。
分支: OK(全可编译) / FAIL(存在语法错误) / UNAVAILABLE(非git目录) / 排除面(archive下损坏不计)。
运行: python tests/test_py_compile_guard.py  → 全 PASS exit 0，任一 FAIL exit 1。
不触碰生产库与生产文件（tmp 目录伪造），无 --no-log 需求（零 guard_precheck_log 写入）。
"""
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, r"D:\zcodesj")
from py_compile_guard import run_check  # noqa: E402

RESULTS = []


def check(name, cond, detail=""):
    RESULTS.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'} {name}: {detail}")


def make_git_repo(files):
    """tmp 目录 git init + 写文件 + git add（ls-files 只需 index，无需 identity）"""
    tmp = tempfile.mkdtemp(prefix="pcg_test_")
    for rel, content in files.items():
        fp = os.path.join(tmp, rel)
        os.makedirs(os.path.dirname(fp), exist_ok=True)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
    subprocess.run(["git", "init", "-q"], cwd=tmp, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=tmp, capture_output=True)
    return tmp


# 1) OK 分支：全部可编译
tmp1 = make_git_repo({
    "good_a.py": "x = 1\n",
    "sub/good_b.py": "def f():\n    return 2\n",
})
r = run_check(tmp1)
check("OK 分支", r["status"] == "OK" and r["errors"] == 0, r["detail"])
shutil.rmtree(tmp1, ignore_errors=True)

# 2) FAIL 分支：存在语法错误文件
tmp2 = make_git_repo({
    "good.py": "x = 1\n",
    "broken.py": "def f(:\n    pass\n",
})
r = run_check(tmp2)
check("FAIL 分支", r["status"] == "FAIL" and r["errors"] == 1 and "broken.py" in r["detail"], r["detail"])
shutil.rmtree(tmp2, ignore_errors=True)

# 3) UNAVAILABLE 分支：非 git 目录
tmp3 = tempfile.mkdtemp(prefix="pcg_nogit_")
with open(os.path.join(tmp3, "any.py"), "w", encoding="utf-8") as f:
    f.write("x = 1\n")
r = run_check(tmp3)
check("UNAVAILABLE 分支", r["status"] == "UNAVAILABLE" and r["errors"] is None, r["detail"])
shutil.rmtree(tmp3, ignore_errors=True)

# 4) 排除面分支：archive/ 下损坏文件不计（与审查口径一致）
tmp4 = make_git_repo({
    "ok.py": "x = 1\n",
    "archive/old_broken.py": "def f(:\n    pass\n",
})
r = run_check(tmp4)
check("排除面分支", r["status"] == "OK", r["detail"])
shutil.rmtree(tmp4, ignore_errors=True)

# 5) 生产面实弹锚：真实仓库当前应为 OK（0 语法错误态）
r = run_check(r"D:\zcodesj")
check("生产面实弹", r["status"] == "OK", r["detail"])

failed = [n for n, ok, _ in RESULTS if not ok]
print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} PASS" + (f", FAILED: {failed}" if failed else ""))
sys.exit(1 if failed else 0)
