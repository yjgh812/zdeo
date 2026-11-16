import os, subprocess
BASE = r"C:/tmp_base"
def t2():
    p = os.path.join(BASE, "prices_raw.csv")
    subprocess.run(["bash", "scripts/script.sh", "calculate-file", p], cwd=r"C:/tmp_skill")
