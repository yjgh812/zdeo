# tickflow_panel_guard.ps1 — TickFlow 面板(localhost:3018)存活守护
# O25(2026-08-27): health_check 连续报 10061(面板DOWN) 且无常驻守护(schtasks 从未注册)。
# 行为: 探测 3018; 不通则以隐藏窗口拉起 backend uvicorn(detached), 并记日志。
# 幂等: 面板健康时不做任何事。日志上界: 仅追加单行/次。
$ErrorActionPreference = "SilentlyContinue"
$ok = $false
try {
    $r = Invoke-WebRequest -Uri "http://localhost:3018" -UseBasicParsing -TimeoutSec 5
    if ($r.StatusCode -lt 500) { $ok = $true }
} catch { $ok = $false }

$log = "D:\zcodesj\reports\tickflow_panel_guard.log"
$ts = Get-Date -Format "yyyy-MM-dd HH:mm:ss"
if ($ok) {
    Add-Content -Path $log -Value "$ts OK" 
} else {
    Add-Content -Path $log -Value "$ts DOWN -> relaunch uvicorn :3018"
    $py = "D:\zcodesj\tickflow-stock-panel\backend\.venv\Scripts\python.exe"
    if (Test-Path $py) {
        Start-Process -FilePath $py `
            -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port','3018' `
            -WorkingDirectory "D:\zcodesj\tickflow-stock-panel\backend" `
            -WindowStyle Hidden
        Start-Sleep -Seconds 10
        try {
            $r2 = Invoke-WebRequest -Uri "http://localhost:3018" -UseBasicParsing -TimeoutSec 5
            if ($r2.StatusCode -lt 500) { Add-Content -Path $log -Value "$ts RELAUNCHED OK" }
            else { Add-Content -Path $log -Value "$ts RELAUNCH still failing" }
        } catch { Add-Content -Path $log -Value "$ts RELAUNCH verify failed" }
    } else {
        Add-Content -Path $log -Value "$ts venv python missing, cannot relaunch"
    }
}
