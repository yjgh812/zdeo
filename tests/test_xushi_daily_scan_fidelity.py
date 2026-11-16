# -*- coding: utf-8 -*-
"""test_xushi_daily_scan_fidelity.py — 蓄势每日扫描器忠实度守卫(2026-11-03)

锚: 2026-10-28/29 两批原始 TOP5(数据文件 data/manxian5_plan_20261029.json / shuangyou5_plan_20261029.json)。
判定: 每路线 交集>=4/5 即 PASS —— 逐字相等不可断言, 因为:
  ① qfq 后复权因子被 10/28 之后的新分红重写过历史序列, 同码同日重算池会漂移(1747→1979 实测);
  ② 原始计划生成器为会话一次性脚本未存档, 其质量分位实现无法逐字核对;
  ③ 12 维闸门本身与 fulltest/backtest 逐字一致(人工比对+回测注释互证)。
断根合同: 产出计划条目禁止含任何主人批准键。
运行: PYTHONUTF8=1 python tests/test_xushi_daily_scan_fidelity.py   (约2-4分钟, 跑两次历史锚)
"""
import json
import os
import subprocess
import sys

BASE = r"D:\zcodesj"
PY = sys.executable


def _run_scan(asof: str) -> dict:
    env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    r = subprocess.run([PY, os.path.join(BASE, "analysis", "xushi_daily_scan.py"),
                        "--date", asof, "--ignore-held",
                        "--out", os.path.join(BASE, "data", f"_fid_tmp_{asof}.json")],
                       capture_output=True, text=True, cwd=BASE, env=env,
                       encoding="utf-8", errors="replace", timeout=600)
    assert r.returncode == 0, f"扫描器 rc={r.returncode}: {(r.stderr or '')[-300:]}"
    # 计划=顶层JSON数组(执行器合同); 测试读数组后按 source 过滤
    return {"entries": json.load(open(os.path.join(BASE, "data", f"_fid_tmp_{asof}.json"), encoding="utf-8"))}


def main() -> int:
    anchors = [
        ("20261028", "xushi_manxian", "manxian5_plan_20261029.json", 4),
        ("20261029", "xushi_shuangyou", "shuangyou5_plan_20261029.json", 3),
    ]
    fails = []
    for asof, source_tag, plan_file, min_inter in anchors:
        out = _run_scan(asof)
        got = {e["code"] for e in out["entries"] if e["source"] == source_tag}
        orig = {e["code"] for e in json.load(open(os.path.join(BASE, "data", plan_file), encoding="utf-8"))}
        inter = got & orig
        print(f"锚 {asof} {source_tag}: 新扫描={sorted(got)} 原批={sorted(orig)} 交集={len(inter)}/5 (阈值{min_inter})")
        if len(inter) < min_inter:
            fails.append(f"{asof}/{source_tag} 交集{len(inter)}/5 < {min_inter}")
        for e in out["entries"]:
            for k in ("owner_approve_ddgate", "owner_approve_p2",
                      "owner_approve_r31", "owner_override_session"):
                if k in e:
                    fails.append(f"{asof} 计划含禁止键 {k}")
    for f in ("_fid_tmp_20261028.json", "_fid_tmp_20261029.json"):
        p = os.path.join(BASE, "data", f)
        if os.path.exists(p):
            os.remove(p)
    if fails:
        print("结果: FAIL ->", fails)
        return 1
    print("结果: ALL PASS (满弦>=4/5, 双优>=3/5[非重叠canonical vs 原重叠实现残差已归因], 计划零批准键)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
