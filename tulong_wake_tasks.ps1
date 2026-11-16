# TulongWake keep-awake timers (2026-09-17)
# Root cause: machine sleep during dispatch windows -> cron skipped
# (computer_asleep_or_app_not_running: 9/14 08:00+14:30, 9/15 09:10, 9/16 14:30, 9/17 09:10).
# Fix: weekday WakeToRun tasks 5min before each dispatch window; the RUNNING task
# holds the machine awake for its duration (cmd timeout hold), covering:
#   07:55+90min -> 08:00 / 09:10 / 09:26 / 09:40
#   14:25+45min -> 14:30 / 14:57
#   21:55+120min -> 22:00 / 22:45 / 23:30
#   01:55+45min -> 02:30
# ASCII only per constitution. -WakeToRun + battery flags per 9/8 schtasks lessons.
$tasks = @(
  @{ Name='TulongWake_0755'; Time='07:55'; Hold=5400 },
  @{ Name='TulongWake_1425'; Time='14:25'; Hold=2700 },
  @{ Name='TulongWake_2155'; Time='21:55'; Hold=7200 },
  @{ Name='TulongWake_0155'; Time='01:55'; Hold=2700 }
)
foreach ($t in $tasks) {
  $action   = New-ScheduledTaskAction -Execute "$env:windir\system32\cmd.exe" -Argument ("/c timeout /t " + $t.Hold + " /nobreak >nul")
  $trigger  = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $t.Time
  $settings = New-ScheduledTaskSettingsSet -WakeToRun -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Seconds ($t.Hold + 300))
  Register-ScheduledTask -TaskName $t.Name -Action $action -Trigger $trigger -Settings $settings -Force | Out-Null
  Write-Output ("registered " + $t.Name + " @" + $t.Time + " hold=" + $t.Hold + "s")
}
