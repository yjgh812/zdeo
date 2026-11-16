# -*- coding: utf-8 -*-
"""
屠龙 × Fincept AI Quant Lab 包装器
把 Fincept 的 qlib_service 指到屠龙自有 qlib 数据(D:/workbuddyspace/qlib_data/qlib_bin)。

用法(屠龙主环境 python):
  python tulong_qlab.py check_status
  python tulong_qlab.py get_calendar '{"limit":5}'
  python tulong_qlab.py get_data '{"instruments":["SH600519"],"start_date":"2026-08-01","end_date":"2026-08-13","fields":["$close","$volume"]}'
  python tulong_qlab.py get_factor_library

命令集与参数格式同 qlib_service.py（scripts/ai_quant_lab/README.md）。
"""
import json
import sys

FINCEPT_SCRIPTS = r"C:\Program Files\FinceptTerminal\scripts"
TULONG_QLIB_DATA = r"D:\workbuddyspace\qlib_data\qlib_bin"

sys.path.insert(0, FINCEPT_SCRIPTS)

# qlib.init 只允许一次;qlib_service.QlibService 实例化时 init
from ai_quant_lab.qlib_service import QlibService, QLIB_AVAILABLE

service = QlibService(provider_uri=TULONG_QLIB_DATA, region="cn")

COMMANDS = {
    "check_status", "list_models", "get_data_handlers", "get_factor_library",
    "get_strategies", "get_data", "create_dataset", "train_model", "predict",
    "run_backtest", "get_factor_analysis", "get_feature_importance",
    "save_model", "load_model", "get_calendar", "get_instruments",
}

if __name__ == "__main__":
    if len(sys.argv) < 1 or sys.argv[0] not in COMMANDS and len(sys.argv) < 2:
        print(json.dumps({"success": False, "error": "usage: tulong_qlab.py <command> [json_params]"}))
        sys.exit(1)
    cmd = sys.argv[1] if len(sys.argv) > 1 else "check_status"
    if cmd not in COMMANDS:
        print(json.dumps({"success": False, "error": f"unknown command {cmd}"}))
        sys.exit(1)
    params = json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}

    if cmd == "check_status":
        print(json.dumps({"success": True, "qlib_available": QLIB_AVAILABLE,
                          "provider_uri": TULONG_QLIB_DATA, "region": "cn"}))
    elif cmd == "get_calendar":
        print(json.dumps({"success": True,
                          "tail": service.get_calendar(params.get("freq", "day")) and "see below",
                          "calendar_tail": (service.get_calendar(params.get("freq", "day")) or [])[-params.get("limit", 5):]}))
    elif cmd == "get_data":
        r = service.get_data(params.get("instruments", []), params.get("start_date"),
                             params.get("end_date"), params.get("fields"), params.get("freq", "day"))
        print(json.dumps(r, ensure_ascii=False, default=str))
    elif cmd == "get_factor_library":
        print(json.dumps(service.get_factor_library(), default=str))
    elif cmd == "list_models":
        print(json.dumps(service.list_models(), default=str))
    else:
        print(json.dumps({"success": False,
                          "error": f"command {cmd} 需直接调用 QlibService 方法,见 qlib_service.py"}))
