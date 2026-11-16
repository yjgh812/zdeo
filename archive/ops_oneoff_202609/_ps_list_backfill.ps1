Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'backfill_minute_baostock|m5_recover' } | ForEach-Object {
  $cmd = $_.CommandLine
  if ($cmd.Length -gt 130) { $cmd = $cmd.Substring(0, 130) }
  "{0} {1} {2}" -f $_.ProcessId, $_.CreationDate.ToString('HH:mm:ss'), $cmd
}
