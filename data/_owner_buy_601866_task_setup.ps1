$a = New-ScheduledTaskAction -Execute 'D:\zcodesj\data\_owner_buy_601866_20260911.cmd'
$t = New-ScheduledTaskTrigger -Once -At '2026-09-11T09:31:00'
Register-ScheduledTask -TaskName 'Tulong_601866_OwnerBuy_20260911' -Action $a -Trigger $t -Force | Out-Null
$info = Get-ScheduledTaskInfo -TaskName 'Tulong_601866_OwnerBuy_20260911'
$task = Get-ScheduledTask -TaskName 'Tulong_601866_OwnerBuy_20260911'
Write-Output ("State=" + $task.State + " NextRun=" + $info.NextRunTime)
