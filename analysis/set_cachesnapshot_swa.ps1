# Set CacheSnapshot_Daily_0610 StartWhenAvailable=True (host-offline resilience per 10/17 lesson)
# Run from PowerShell: powershell -ExecutionPolicy Bypass -File set_cachesnapshot_swa.ps1
$tn = "CacheSnapshot_Daily_0610"
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable
Set-ScheduledTask -TaskName $tn -Settings $settings
Write-Output ("SWA set for " + $tn)
