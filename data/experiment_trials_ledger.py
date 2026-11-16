# -*- coding: utf-8 -*-
"""试验台账计数器（B1 落地：rdagent Trace 思想的屠龙版——DSR 试验池 N 的一键审计）

汇总三类可辩护试验源, 输出当前试验池规模, 供 DSR/通缩校正直接引用:
  1. prereg_*.json     — 预注册假设数(每份=1个已冻结假设)
  2. falsified_rules   — 证伪规则键数(rules 顶层键)
  3. tests/实验脚本    — data/ 下带日期后缀的一次性研究脚本数(近 30 天活跃)
口径说明: 本计数器给"下界"(可审计的登记物), 不含未登记的口头/中途放弃试验——
真实 N >= 此数, 因此 DSR 用此数是**保守偏松**判定, 不会冤枉好策略。

用法: python data/experiment_trials_ledger.py [--json OUT.json]
"""
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def count_prereg():
    files = list((ROOT / "data").glob("prereg_*.json"))
    return len(files), [f.name for f in files]


def count_falsified():
    p = ROOT / "data" / "falsified_rules.json"
    if not p.exists():
        return 0, []
    d = json.loads(p.read_text(encoding="utf-8"))
    rules = d.get("rules", {})
    return len(rules), sorted(rules.keys())


def count_research_scripts(days: int = 30):
    """data/ 下一次性研究脚本(下划线前缀+日期后缀), 近 N 天修改。"""
    import time

    cutoff = time.time() - days * 86400
    scripts = []
    for f in (ROOT / "data").glob("_*.py"):
        if f.stat().st_mtime >= cutoff:
            scripts.append(f.name)
    return len(scripts), sorted(scripts)


def main():
    n_prereg, prereg_files = count_prereg()
    n_falsified, falsified_keys = count_falsified()
    n_scripts, script_files = count_research_scripts()

    out = {
        "generated": time.strftime("%Y-%m-%d %H:%M"),
        "trial_pool": {
            "prereg_frozen": n_prereg,
            "falsified_rules_keys": n_falsified,
            "recent_research_scripts_30d": n_scripts,
            "total_defensible_lower_bound": n_prereg + n_falsified + n_scripts,
        },
        "prereg_files": prereg_files,
        "falsified_keys": falsified_keys,
        "recent_scripts": script_files,
        "usage": "DSR 通缩的 N 取 total_defensible_lower_bound(保守下界); 真实 N>=此数",
    }
    print(json.dumps(out["trial_pool"], ensure_ascii=False, indent=2))
    if "--json" in sys.argv:
        out_path = sys.argv[sys.argv.index("--json") + 1]
        Path(out_path).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"saved -> {out_path}")


if __name__ == "__main__":
    main()
