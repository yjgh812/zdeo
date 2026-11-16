import os, subprocess
BASE = r"C:/tmp_base"
SKILL = r"C:/tmp_skill"
def t4(tag):
    p = os.path.join(BASE, f"prices_{tag}.csv")
    subprocess.run(["bash", "scripts/script.sh", "calculate-file", p.replace("\\", "/")], cwd=SKILL)
