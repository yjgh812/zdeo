import os
BASE = r"C:/tmp_base"
def t3(tag):
    p = os.path.join(BASE, f"prices_{tag}.csv")
    with open(p, "w") as f:
        f.write("x")
