import subprocess
def t1():
    subprocess.run(["bash", "scripts/script.sh", "calculate-file", "prices_raw.csv"],
                   cwd=r"C:/tmp_skill", capture_output=True, text=True)
