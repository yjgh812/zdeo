$taskName = "TulongSystemDaily"
$pythonExe = "C:\Users\yujie\AppData\Local\Microsoft\WindowsApps\PythonSoftwareFoundation.Python.3.13_qbz5n2kfra8p0\python.exe"
$script = "D:\workbuddyspace\tulong_system_daily.py"
$workDir = "D:\workbuddyspace"

$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At 10pm
$action = New-ScheduledTaskAction -Execute $pythonExe -Argument $script -WorkingDirectory $workDir
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -DontStopOnIdleEnd -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 1)
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $taskName -Trigger $trigger -Action $action -Settings $settings -Principal $principal -Force | Out-Null
Write-Host "Task $taskName rebuilt"

$task = Get-ScheduledTask -TaskName $taskName
Write-Host "--- Settings ---"
$task.Settings | Select-Object AllowStartIfOnBatteries, DontStopIfGoingOnBatteries, WakeToRun, StartWhenAvailable, ExecutionTimeLimit | Format-List
Write-Host "--- Action ---"
$task.Actions | Select-Object Execute, Arguments, WorkingDirectory | Format-List
Write-Host "--- Next Run ---"
(Get-ScheduledTaskInfo -TaskName $taskName).NextRunTime
