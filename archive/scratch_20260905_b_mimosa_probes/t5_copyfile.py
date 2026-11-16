import os, shutil
BASE = r"C:/tmp_base"
SKILL = r"C:/tmp_skill"
def t5(tag):
    src = os.path.join(BASE, f"prices_{tag}.csv")
    dst = os.path.join(SKILL, "prices_raw.csv")
    shutil.copyfile(src, dst)
