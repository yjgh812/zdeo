@echo off
rem WestockChannelPreopen_0845 launcher (2026-11-09, pure ASCII only - cmd.exe parses by GBK).
rem Constitution S7: all schtasks must go through ASCII cmd launcher, no bare python.
rem Live probe of the executor's direct HTTP channel (westock_client.quote) before market open.
cd /d D:\zcodesj
D:\zcodesj\py_run.cmd D:\zcodesj\westock_outage_guard.py --preopen >> D:\zcodesj\data\westock_channel_preopen.log 2>&1
