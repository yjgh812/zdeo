# 修复计划任务起始于路径问题
# 用PowerShell直接设置WorkingDirectory

$tasks = @(
    @{Name="TulongIntradayAM"; WorkDir="D:\workbuddyspace"; Exec="python tulong_intraday.py"},
    @{Name="TulongIntradayPM"; WorkDir="D:\workbuddyspace"; Exec="python tulong_intraday.py"},
    @{Name="M1DailySignal"; WorkDir="D:\reasonix\quant_backtest"; Exec="python m1_daily.py --days 5"},
    @{Name="AQDailyPicker"; WorkDir="D:\reasonix\quant_backtest"; Exec="run_daily.bat"},
    @{Name="Claw_DailyDeploy"; WorkDir="D:\workbuddyspace\Claw"; Exec="daily_run_wrapper.bat"},
    @{Name="BpEpPick"; WorkDir="D:\workbuddyspace"; Exec="python bp_ep_turnover_picker.py"}
)

foreach ($t in $tasks) {
    try {
        $task = Get-ScheduledTask -TaskName $t.Name -ErrorAction Stop
        $action = $task.Actions[0]
        $action.WorkingDirectory = $t.WorkDir
        # 如果原来执行的是完整路径,保留; 否则用相对路径
        if ($action.Execute -like "*python.exe") {
            $action.Arguments = $t.Exec
        }
        Set-ScheduledTask -TaskName $t.Name -Action $action -ErrorAction Stop
        Write-Host "  ✅ $($t.Name): WorkingDirectory=$($t.WorkDir), Action=$($t.Exec)"
    } catch {
        Write-Host "  ❌ $($t.Name): $($_.Exception.Message)"
    }
}