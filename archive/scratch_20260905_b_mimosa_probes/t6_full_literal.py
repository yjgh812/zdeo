import subprocess
def t6():
    subprocess.run(["bash", "scripts/script.sh", "calculate-file", "C:/tmp_base/prices_raw.csv"],
                   cwd=r"C:/tmp_skill")
