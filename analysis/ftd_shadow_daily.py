# -*- coding: utf-8 -*-
"""FTD跟进日影子日更(指数右侧确认事件, 观测层零交易权)
对应 PREREG_FTD_FOLLOWTHROUGH_20261007, 主人2026-10-07裁决批准激活影子层。
状态机与fwd口径与样内验证 analysis/ftd_followthrough_study_20261007.py 逐位一致;
参数单一真源=data/PREREG_FTD_FOLLOWTHROUGH_20261007.json trigger_locked+approval
(缺字段即fail-closed退出, 禁止回退硬编码)。
数据源=vipdoc sh000001.day 全史(与样内验证同源同解析)。
幂等: date主键全量覆盖, 重跑安全; 台账含样内参考层(in_sample=true)与OOS影子层。
边界: 纯观测影子层零交易权, 禁止接交易闸门/交易cron; FTD不得作为PD-034后续加仓规则
(prereg boundary); OOS验收门(LOCKED)=OOS n>=3或日历12月, fwd20 med>=+2%且win>=60%;
kill_switch=连续3个OOS事件fwd20全负提请废案。

用法:
  python analysis/ftd_shadow_daily.py            # 全史重算+台账幂等覆盖+状态快照
  python analysis/ftd_shadow_daily.py --report   # 同上并打印简报
"""
from __future__ import annotations

import json
import struct
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

PREREG = Path(r"D:\zcodesj\data\PREREG_FTD_FOLLOWTHROUGH_20261007.json")
VIPDOC_DAY = r"D:\anzhuang\tdx\vipdoc\sh\lday\sh000001.day"
LEDGER = Path(r"D:\zcodesj\data\ftd_shadow_ledger.jsonl")
SNAPSHOT = Path(r"D:\zcodesj\data\ftd_shadow_state_latest.json")
BRIEF = Path(r"D:\zcodesj\data\ftd_shadow_brief_latest.txt")
FWD_HORIZONS = [5, 10, 20, 60]


def load_prereg() -> dict:
    """参数单一真源: prereg JSON; 缺字段fail-closed。"""
    p = json.loads(PREREG.read_text(encoding="utf-8"))
    t = p.get("trigger_locked") or {}
    for k in ("precondition", "day1", "window", "ftd"):
        if k not in t:
            raise SystemExit(f"[FAIL-CLOSED] prereg trigger_locked 缺字段 {k}")
    win = t["window"]
    # 从规则文本解析锁定数值(格式钉死在prereg, 解析失败即退出)
    dd = float(t["precondition"].split(">=")[1].replace("%", "").strip())
    gain = float(t["ftd"].split(">=")[1].replace("%", "").split()[0])
    import re
    m = re.search(r"第\s*(\d+)\s*[~\-]\s*(\d+)\s*个交易日", win)
    w_lo, w_hi = int(m.group(1)), int(m.group(2))
    m2 = re.search(r"(\d+)\s*交易日冷却", t["ftd"])
    cooldown = int(m2.group(1))
    since = int((p.get("approval") or {}).get("activation_since", ""))
    if not since:
        raise SystemExit("[FAIL-CLOSED] prereg approval.activation_since 缺失(未批准激活)")
    return {"dd": dd, "gain": gain, "w_lo": w_lo, "w_hi": w_hi,
            "cooldown": cooldown, "since": since,
            "name": p.get("name"), "status": p.get("status")}


def read_vipdoc(path: str) -> list:
    rows = []
    with open(path, "rb") as f:
        while True:
            b = f.read(32)
            if len(b) < 32:
                break
            d, o, h, l, c, amt, vol, _r = struct.unpack("<IIIIIfII", b)
            rows.append({"date": str(d), "close": c / 100.0, "vol": vol})
    return rows


def find_ftds(rows: list, cfg: dict):
    """状态机与样内验证脚本逐位同构(参见 ftd_followthrough_study_20261007.py)。"""
    closes = [r["close"] for r in rows]
    vols = [r["vol"] for r in rows]
    rets = [0.0] + [closes[i] / closes[i - 1] - 1 for i in range(1, len(closes))]
    n = len(rows)
    dd = []
    for i in range(n):
        lo_i = max(0, i - 59)
        dd.append((closes[i] / max(closes[lo_i:i + 1]) - 1) * 100.0)

    hits = []  # (事件索引, Day1偏移, 事件日回撤深度)
    in_drawdown = False
    day1_idx = None
    last_ftd_i = -999
    for i in range(n):
        if dd[i] <= -cfg["dd"]:
            in_drawdown = True
            day1_idx = None
        elif in_drawdown and day1_idx is None and rets[i] > 0:
            day1_idx = i
        if in_drawdown and day1_idx is not None:
            offset = i - day1_idx
            if cfg["w_lo"] <= offset <= cfg["w_hi"]:
                if rets[i] >= cfg["gain"] / 100.0:
                    if vols[i] > vols[i - 1] and i - last_ftd_i > cfg["cooldown"]:
                        hits.append((i, offset, dd[i]))
                        last_ftd_i = i
                        in_drawdown = False
                        day1_idx = None
    state = {"last_i": n - 1, "last_date": rows[-1]["date"], "dd_last": round(dd[-1], 2),
             "in_drawdown": in_drawdown, "day1_set": day1_idx is not None,
             "day1_offset": (n - 1 - day1_idx) if day1_idx is not None else None,
             "cool_left": max(0, cfg["cooldown"] - (n - 1 - last_ftd_i)) if last_ftd_i >= 0 else 0}

    events_out = []
    for i, offset, ddi in hits:
        rec = {"date": int(rows[i]["date"]),
               "gain_pct": round(rets[i] * 100.0, 2),
               "vol_ratio": round(vols[i] / vols[i - 1], 2) if vols[i - 1] else None,
               "day_offset": offset, "dd_depth_pct": round(ddi, 2),
               "in_sample": int(rows[i]["date"]) < cfg["since"]}
        for h in FWD_HORIZONS:
            j = i + h
            rec[f"fwd{h}"] = round((closes[j] / closes[i] - 1) * 100.0, 2) if j < n else None
        j20 = min(i + 20, n - 1)
        rec["mdd20"] = round((min(closes[i:j20 + 1]) / closes[i] - 1) * 100.0, 2)
        events_out.append(rec)
    return events_out, state


def load_ledger() -> dict:
    rows = {}
    if LEDGER.exists():
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                rows[r["date"]] = r
    return rows


def save_ledger(rows: dict) -> None:
    tmp = LEDGER.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        for d in sorted(rows):
            f.write(json.dumps(rows[d], ensure_ascii=False) + "\n")
    tmp.replace(LEDGER)


def median_win(vals: list):
    if not vals:
        return None, None
    s = sorted(vals)
    med = round(s[len(s) // 2], 2)
    win = round(sum(1 for v in vals if v > 0) * 100.0 / len(vals), 1)
    return med, win


def evaluate_oos(events: list) -> dict:
    oos = [e for e in events if not e["in_sample"]]
    done = [e for e in oos if e["fwd20"] is not None]
    med, win = median_win([e["fwd20"] for e in done])
    tail_neg = 0
    for e in reversed(done):
        if e["fwd20"] is not None and e["fwd20"] < 0:
            tail_neg += 1
        else:
            break
    ins = [e for e in events if e["in_sample"] and e["fwd20"] is not None]
    ins_med, ins_win = median_win([e["fwd20"] for e in ins])
    return {"oos_n": len(oos), "oos_done": len(done), "oos_med": med, "oos_win": win,
            "kill_tail_neg": tail_neg,
            "in_sample_n": len(ins), "in_sample_med": ins_med, "in_sample_win": ins_win}


def phase_text(state: dict, cfg: dict) -> str:
    if state["cool_left"] > 0:
        return f"FTD后冷却中(剩{state['cool_left']}交易日, 冷却期内新FTD被拦截)"
    if state["in_drawdown"] and not state["day1_set"]:
        return f"回撤前提满足(dd={state['dd_last']}%), 等待Day1(回撤带外首个收涨日)"
    if state["in_drawdown"] and state["day1_set"]:
        off = state["day1_offset"]
        if off < cfg["w_lo"]:
            return f"Day1已现, 等待窗口开启(现第{off}日, 窗口第{cfg['w_lo']}~{cfg['w_hi']}日)"
        if off <= cfg["w_hi"]:
            return f"FTD窗口内(第{off}日/窗{cfg['w_lo']}~{cfg['w_hi']})"
        return f"本轮反弹窗口已滑过未确认(第{off}日>窗{cfg['w_hi']}), 等待下一次深回撤重置"
    return f"回撤前提未满足(当前距60日高点回撤{state['dd_last']}%)"


def main() -> int:
    cfg = load_prereg()
    if cfg["status"] != "ACTIVE":
        print(f"[SKIP] prereg status={cfg['status']}, 未批准激活")
        return 0
    rows = read_vipdoc(VIPDOC_DAY)
    events, state = find_ftds(rows, cfg)
    old = load_ledger()
    changed = 0
    for e in events:
        if old.get(e["date"]) != e:
            changed += 1
        old[e["date"]] = e
    save_ledger(old)
    ev = evaluate_oos(events)
    print(f"[SCAN] {state['last_date']} 台账{len(old)}行(样内{ev['in_sample_n']}+OOS{ev['oos_n']}) "
          f"幂等覆盖{changed}行")
    print(f"[STATE] dd={state['dd_last']}% 阶段: {phase_text(state, cfg)}")
    kill = ev["kill_tail_neg"] >= 3
    if kill:
        print(f"[KILL-SWITCH] 连续{ev['kill_tail_neg']}个OOS事件fwd20全负 -> 提请废案(须主人裁决)")
    if "--report" in sys.argv:
        ready = ev["oos_done"] >= 3
        verdict = ("可判读" if ready else "未满判读条件(n>=3或日历12月)")
        lines = [
            f"【FTD跟进日 {state['last_date']}】距60日高点回撤{state['dd_last']}%",
            f"状态机: {phase_text(state, cfg)}",
            f"台账: 样内{ev['in_sample_n']}事件(fwd20 med{ev['in_sample_med']}%/win{ev['in_sample_win']}%参考) "
            f"| OOS影子n={ev['oos_n']}(已到期{ev['oos_done']}, med={ev['oos_med']}% win={ev['oos_win']}%)",
            f"验收门(LOCKED): OOS fwd20 med>=+2%且win>=60%, 判读={verdict} | "
            f"kill_switch={'已触发提请废案' if kill else '未触发'}",
            "(观测层零交易权; FTD不作PD-034加仓规则, 仅独立右侧择时锚; 命中FTD不构成任何买入指令)",
        ]
        text = "\n".join(lines)
        BRIEF.write_text(text, encoding="utf-8")
        SNAPSHOT.write_text(json.dumps(
            {"state": state, "eval": ev, "cfg": {k: cfg[k] for k in ("dd", "gain", "w_lo", "w_hi", "cooldown", "since")},
             "ledger_rows": len(old), "kill_switch": kill},
            ensure_ascii=False, indent=2), encoding="utf-8")
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
