# -*- coding: utf-8 -*-
"""修复 cp_spmatch_trades JSON 字符串内裸控制符 + 合并五场(932/901/881/896)"""
import json

RAW = r'D:\zcodesj\data\cp_spmatch_trades_20261006.json'
A = r"C:\Users\Administrator\.zcode\cli\artifacts\sess_3c36e6a1-f539-4b81-9364-0c59613a7474"
F881 = A + r"\call_70e58ff74c4f40b4be37df5a-tool-result-a56f0447-43db-4427-89da-fe951e795fc8.json"
F896 = A + r"\call_a0857de4ca6e4e29a01aaf92-tool-result-82f78ef2-5099-495e-97fc-d64e8df34f06.json"


def fix_control(raw: str) -> str:
    out = []
    instr = False
    esc = False
    for c in raw:
        if esc:
            out.append(c)
            esc = False
            continue
        if c == '\\' and instr:
            out.append(c)
            esc = True
            continue
        if c == '"':
            instr = not instr
            out.append(c)
            continue
        if instr and ord(c) < 32:
            out.append('\\u%04x' % ord(c))
            continue
        out.append(c)
    return ''.join(out)


def load_artifact(f):
    raw = open(f, encoding='utf-8').read()
    i = raw.find('{"seq":')
    obj, _ = json.JSONDecoder(strict=False).raw_decode(raw[i:])
    return obj


def main():
    raw = open(RAW, encoding='utf-8', errors='ignore').read()
    d = json.loads(fix_control(raw))
    print("parsed old OK, matches:", list(d["matches"].keys()))
    o881 = load_artifact(F881)
    o896 = load_artifact(F896)
    # cp901(2026-02窗口54行)被cp881(2026-01-05→09-30主赛)完全覆盖, 跳过
    r901 = []
    merged = {
        "user": "陈小炮", "userID": 13232170, "harvested": "2026-10-06",
        "matches": {
            "cp932_2026Q3": d["matches"]["cp932"],
            "cp901_202602": r901,
            "cp881_2026主赛": o881["rows"],
            "cp896_2026主赛": o896["rows"],
        },
    }
    json.dump(merged, open(RAW, 'w', encoding='utf-8'), ensure_ascii=False)
    for k, v in merged["matches"].items():
        dates = [r["d"] for r in v if r["d"]]
        print(k, len(v), "行", dates[-1] if dates else "-", "→", dates[0] if dates else "-")
    print("saved OK")


if __name__ == "__main__":
    main()
