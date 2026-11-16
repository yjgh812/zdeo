import os, subprocess
BASE = r"C:/tmp_base"
SKILL = r"C:/tmp_skill"
def t7(tag):
    p = os.path.realpath(os.path.join(BASE, f"prices_{tag}.csv"))
    assert os.path.dirname(p) == os.path.realpath(BASE), "escape"
    subprocess.run(["bash", "scripts/script.sh", "calculate-file", p.replace("\\", "/")], cwd=SKILL)
